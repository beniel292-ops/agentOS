"use client";

import { useAuth } from "@clerk/nextjs";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch } from "@/lib/api";

import { actorLabel, fmt, useCos } from "../cos-context";

type Event = { id: number; actor: string; action: string; data: Record<string, unknown>; at: string };

const LIMIT = 50;
const VIEWS = [
  { id: "agents", label: "Agent activity" },
  { id: "security", label: "Security log" },
  { id: "all", label: "All" },
];
const ALERTS = ["cos_stopped", "agent_key_revoked"];

export default function ActivityPage() {
  const { role } = useCos();
  const { getToken } = useAuth();
  const owner = role === "owner";
  const [view, setView] = useState("agents");
  const [rows, setRows] = useState<Event[]>([]);
  const [more, setMore] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!owner) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await apiFetch<Event[]>(getToken, `/v1/cos/activity?view=${view}&limit=${LIMIT}`);
        if (cancelled) return;
        setRows(r.data);
        setMore(r.data.length >= LIMIT);
        setFailed(false);
        setLoaded(true);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getToken, owner, view]);

  async function loadMore() {
    setBusy(true);
    try {
      const before = rows[rows.length - 1].id;
      const r = await apiFetch<Event[]>(
        getToken,
        `/v1/cos/activity?view=${view}&limit=${LIMIT}&before=${before}`,
      );
      setRows((cur) => [...cur, ...r.data]);
      setMore(r.data.length >= LIMIT);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  if (!owner) return <p className="text-sm text-muted-foreground">Only the owner can see activity.</p>;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Activity</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-wrap gap-2">
          {VIEWS.map((v) => (
            <Button
              key={v.id}
              size="sm"
              variant={view === v.id ? "default" : "outline"}
              onClick={() => {
                setLoaded(false);
                setView(v.id);
              }}
            >
              {v.label}
            </Button>
          ))}
        </div>
        {failed && <Badge variant="destructive">Something went wrong. Try again.</Badge>}
        {!loaded && !failed ? (
          <Badge variant="secondary">Loading...</Badge>
        ) : rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing yet.</p>
        ) : (
          <ul className="flex flex-col divide-y text-sm">
            {rows.map((e) => (
              <li key={e.id} className="flex flex-col gap-0.5 py-2">
                <p className="flex flex-wrap items-center gap-2">
                  <span className="text-xs text-muted-foreground">{fmt(e.at)}</span>
                  <span className="font-medium">{actorLabel(e.actor)}</span>
                  {view === "security" && ALERTS.includes(e.action) ? (
                    <Badge variant="destructive">{e.action.replaceAll("_", " ")}</Badge>
                  ) : (
                    <span>{e.action.replaceAll("_", " ")}</span>
                  )}
                </p>
                {Object.keys(e.data ?? {}).length > 0 && (
                  <p className="break-all text-xs text-muted-foreground">
                    {Object.entries(e.data)
                      .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
                      .join(" · ")}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
        {more && (
          <div>
            <Button variant="outline" disabled={busy} onClick={loadMore}>
              Load more
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
