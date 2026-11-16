"use client";

import { useAuth } from "@clerk/nextjs";
import { createContext, useContext, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { apiFetch } from "@/lib/api";

export type Role = "owner" | "member";
export type CosSettings = {
  stopped: boolean;
  stopped_at: string | null;
  stopped_by: string | null;
  timezone: string;
  morning_time: string;
  evening_time: string;
  quiet_start: string;
  quiet_end: string;
  max_calls_per_day: number;
  min_gap_minutes: number;
};

export type CosCtx = { role: Role | null; settings: CosSettings; reload: () => void };

export const CosContext = createContext<CosCtx | null>(null);

export function useCos(): CosCtx {
  const c = useContext(CosContext);
  if (!c) throw new Error("useCos outside CosContext");
  return c;
}

export const fmt = (iso: string) => new Date(iso).toLocaleString();

export function actorLabel(a: string) {
  if (a === "agent:openclaw") return "Jarvis";
  if (a === "agent:hermes") return "Fury";
  if (a === "agent:voice") return "Voice";
  if (a.startsWith("system:")) return "System";
  if (a.startsWith("agent:")) return a.slice(6);
  if (a.startsWith("user:")) return "Dashboard user";
  return a;
}

export function StopCard() {
  const { settings, role, reload } = useCos();
  const { getToken } = useAuth();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function post(path: string) {
    setBusy(true);
    setFailed(false);
    try {
      await apiFetch(getToken, `/v1/cos/${path}`, { method: "POST" });
      reload();
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className={settings.stopped ? "border-destructive" : undefined}>
      <CardContent className="flex flex-wrap items-center justify-between gap-4">
        {settings.stopped ? (
          <div>
            <p className="text-lg font-semibold text-destructive">Chief of Staff is stopped</p>
            <p className="text-sm text-muted-foreground">
              Agents cannot change anything.
              {settings.stopped_by && ` Stopped by ${settings.stopped_by}.`}
            </p>
          </div>
        ) : (
          <div>
            <p className="text-lg font-semibold">Chief of Staff is running</p>
            <p className="text-sm text-muted-foreground">STOP blocks all agent writes at once.</p>
          </div>
        )}
        <div className="flex items-center gap-3">
          {failed && <Badge variant="destructive">Something went wrong.</Badge>}
          {settings.stopped ? (
            role === "owner" ? (
              <Button onClick={() => post("resume")} disabled={busy}>
                RESUME
              </Button>
            ) : (
              <span className="text-sm text-muted-foreground">Only the owner can resume.</span>
            )
          ) : (
            <Button variant="destructive" size="lg" onClick={() => post("stop")} disabled={busy}>
              STOP
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
