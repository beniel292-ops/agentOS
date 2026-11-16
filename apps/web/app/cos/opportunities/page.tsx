"use client";

import { useAuth } from "@clerk/nextjs";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch } from "@/lib/api";

import { actorLabel, useCos } from "../cos-context";

type Opportunity = {
  id: string;
  title: string;
  source: string | null;
  url: string | null;
  description: string | null;
  relevance: number | null;
  estimated_value: string | null;
  deadline: string | null;
  next_action: string | null;
  verification_status: "unverified" | "verified" | "rejected";
  status: "new" | "shown" | "saved" | "dismissed";
  created_by: string | null;
};

const FILTERS = ["all", "new", "shown", "saved", "dismissed"];

function host(url: string) {
  try {
    return new URL(url).host;
  } catch {
    return null;
  }
}

export default function OpportunitiesPage() {
  const { role } = useCos();
  const { getToken } = useAuth();
  const owner = role === "owner";
  const [filter, setFilter] = useState("all");
  const [items, setItems] = useState<Opportunity[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [n, setN] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const q = filter === "all" ? "" : `&status=${filter}`;
        const r = await apiFetch<Opportunity[]>(getToken, `/v1/cos/opportunities?limit=200${q}`);
        if (cancelled) return;
        setItems(r.data);
        setLoadFailed(false);
        setLoaded(true);
      } catch {
        if (!cancelled) setLoadFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getToken, filter, n]);

  async function patch(id: string, body: Record<string, string>) {
    setBusy(true);
    setError(null);
    try {
      await apiFetch(getToken, `/v1/cos/opportunities/${id}`, { method: "PATCH", body: JSON.stringify(body) });
      setN((x) => x + 1);
    } catch (e) {
      setError(e instanceof Error && e.message === "423" ? "Chief of Staff is stopped" : "Could not save.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Opportunities</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-wrap gap-2">
          {FILTERS.map((f) => (
            <Button key={f} size="sm" variant={filter === f ? "default" : "outline"} onClick={() => setFilter(f)}>
              {f === "all" ? "All" : f}
            </Button>
          ))}
        </div>
        {error && <Badge variant="destructive">{error}</Badge>}
        {loadFailed && <Badge variant="destructive">Something went wrong. Try again.</Badge>}
        {!loaded && !loadFailed ? (
          <Badge variant="secondary">Loading...</Badge>
        ) : items.length === 0 ? (
          <p className="text-sm text-muted-foreground">No opportunities.</p>
        ) : (
          <ul className="flex flex-col divide-y">
            {items.map((o) => {
              const safe = o.url && /^https?:\/\//.test(o.url);
              return (
                <li key={o.id} className="flex flex-col gap-1.5 py-3 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-medium">{o.title}</p>
                    {o.relevance !== null && <Badge variant="secondary">{o.relevance}/100</Badge>}
                    <Badge variant={o.verification_status === "rejected" ? "destructive" : "secondary"}>
                      {o.verification_status}
                    </Badge>
                    <Badge variant="secondary">{o.status}</Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {[
                      o.source,
                      o.created_by && `by ${actorLabel(o.created_by)}`,
                      o.estimated_value && `value ${o.estimated_value}`,
                      o.deadline && `deadline ${o.deadline}`,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                  {safe && o.url && (
                    <p className="text-xs">
                      <a
                        href={o.url}
                        target="_blank"
                        rel="noopener noreferrer nofollow"
                        className="break-all underline"
                      >
                        Open link
                      </a>{" "}
                      <span className="text-muted-foreground">({host(o.url) ?? "unknown host"})</span>
                    </p>
                  )}
                  {o.next_action && <p>Next: {o.next_action}</p>}
                  {o.description && <p className="whitespace-pre-wrap text-muted-foreground">{o.description}</p>}
                  {owner && (
                    <div className="flex flex-wrap gap-2">
                      <Button size="sm" variant="outline" disabled={busy} onClick={() => patch(o.id, { status: "saved" })}>
                        Save
                      </Button>
                      <Button size="sm" variant="ghost" disabled={busy} onClick={() => patch(o.id, { status: "dismissed" })}>
                        Dismiss
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        onClick={() => patch(o.id, { verification_status: "verified" })}
                      >
                        Verify
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={busy}
                        onClick={() => patch(o.id, { verification_status: "rejected" })}
                      >
                        Reject
                      </Button>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
