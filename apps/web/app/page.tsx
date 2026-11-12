"use client";

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Health = { status: string; db: boolean; redis: boolean };

function Status({ label, ok }: { label: string; ok: boolean }) {
  return (
    <Badge
      variant={ok ? "default" : "destructive"}
      className={ok ? "bg-green-600 text-white hover:bg-green-600" : undefined}
    >
      {label}: {ok ? "ok" : "down"}
    </Badge>
  );
}

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    fetch(`${API_URL}/health`)
      .then((r) => r.json())
      .then(setHealth)
      .catch(() => setFailed(true));
  }, []);

  const apiOk = health?.status === "ok";

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-6 p-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">AgentOS</h1>
        <p className="text-muted-foreground">Lead reply MVP</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Status</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {failed ? (
            <Status label="API" ok={false} />
          ) : health ? (
            <>
              <Status label="API" ok={apiOk} />
              <Status label="DB" ok={health.db} />
              <Status label="Redis" ok={health.redis} />
            </>
          ) : (
            <Badge variant="secondary">Checking...</Badge>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
