"use client";

import { useAuth } from "@clerk/nextjs";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch } from "@/lib/api";

import { actorLabel, fmt, useCos } from "../cos-context";

type Call = {
  id: string;
  kind: string;
  due_at: string;
  status: string;
  reason: string | null;
  created_by: string | null;
};
type Checkin = {
  id: string;
  kind: string;
  started_at: string;
  ended_at: string | null;
  transcript: string | null;
  summary: string | null;
};

function duration(a: string, b: string) {
  const s = Math.max(0, Math.round((new Date(b).getTime() - new Date(a).getTime()) / 1000));
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

export default function CallsPage() {
  const { role } = useCos();
  const { getToken } = useAuth();
  const [calls, setCalls] = useState<Call[]>([]);
  const [checkins, setCheckins] = useState<Checkin[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const owner = role === "owner";

  useEffect(() => {
    if (!owner) return;
    let cancelled = false;
    (async () => {
      try {
        const [c, t] = await Promise.all([
          apiFetch<Call[]>(getToken, "/v1/cos/calls?limit=50"),
          apiFetch<Checkin[]>(getToken, "/v1/cos/checkins?limit=50"),
        ]);
        if (cancelled) return;
        setCalls(c.data);
        setCheckins(t.data);
        setLoaded(true);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getToken, owner]);

  if (!owner) return <p className="text-sm text-muted-foreground">Only the owner can see calls and transcripts.</p>;
  if (failed) return <Badge variant="destructive">Something went wrong. Try again.</Badge>;
  if (!loaded) return <Badge variant="secondary">Loading...</Badge>;

  const pending = calls.find((c) => c.kind === "callback" && c.status === "pending");
  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Scheduled calls</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {pending ? (
            <p className="text-sm">
              Pending callback at <span className="font-medium">{fmt(pending.due_at)}</span>
              {pending.reason && ` (${pending.reason})`}
            </p>
          ) : (
            <p className="text-sm text-muted-foreground">No callback pending.</p>
          )}
          {calls.length > 0 && (
            <ul className="flex flex-col divide-y text-sm">
              {calls.map((c) => (
                <li key={c.id} className="flex items-start justify-between gap-3 py-1.5">
                  <div className="min-w-0">
                    <p>
                      {c.kind} · {fmt(c.due_at)}
                      {c.created_by && ` · ${actorLabel(c.created_by)}`}
                    </p>
                    {c.reason && <p className="text-xs text-muted-foreground">{c.reason}</p>}
                  </div>
                  <Badge variant="secondary">{c.status}</Badge>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Transcripts</CardTitle>
        </CardHeader>
        <CardContent>
          {checkins.length === 0 ? (
            <p className="text-sm text-muted-foreground">No transcripts yet.</p>
          ) : (
            <ul className="flex flex-col divide-y text-sm">
              {checkins.map((c) => (
                <li key={c.id} className="flex flex-col gap-1 py-2">
                  <p className="font-medium">
                    {c.kind} · {fmt(c.started_at)}
                    {c.ended_at && (
                      <span className="font-normal text-muted-foreground">
                        {" "}
                        · {duration(c.started_at, c.ended_at)}
                      </span>
                    )}
                  </p>
                  {c.summary && <p className="whitespace-pre-wrap">{c.summary}</p>}
                  {c.transcript && (
                    <details>
                      <summary className="cursor-pointer text-muted-foreground">Transcript</summary>
                      <p className="mt-1 whitespace-pre-wrap text-muted-foreground">{c.transcript}</p>
                    </details>
                  )}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </>
  );
}
