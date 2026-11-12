"use client";

import { CreateOrganization, useAuth, useOrganization } from "@clerk/nextjs";
import { useEffect, useState } from "react";

import { AppHeader } from "@/components/app-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Health = { status: string; db: boolean; redis: boolean };
type Me = { tenant: { id: string; name: string }; user: { id: string; role: string } };

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

function StatusCard() {
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
  );
}

// Parent keys this by org id, so switching organization remounts and refetches.
function YouCard() {
  const { getToken } = useAuth();
  const [me, setMe] = useState<Me | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`${API_URL}/v1/me`, {
          headers: { Authorization: `Bearer ${await getToken()}` },
        });
        if (!r.ok) throw new Error(String(r.status));
        const data = (await r.json()) as Me;
        if (!cancelled) setMe(data);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getToken]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>You</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-wrap items-center gap-2">
        {failed ? (
          <Badge variant="destructive">Could not load your account</Badge>
        ) : me ? (
          <>
            <span className="font-medium">{me.tenant.name}</span>
            <Badge variant="secondary">{me.user.role}</Badge>
          </>
        ) : (
          <Badge variant="secondary">Loading...</Badge>
        )}
      </CardContent>
    </Card>
  );
}

export default function Home() {
  const { isLoaded, organization } = useOrganization();

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-md flex-col gap-6 p-6">
      <AppHeader />
      {!isLoaded ? null : organization ? (
        <>
          <StatusCard />
          <YouCard key={organization.id} />
        </>
      ) : (
        <div className="flex flex-col items-center gap-3">
          <p className="text-muted-foreground">Create an organization to continue.</p>
          <CreateOrganization />
        </div>
      )}
    </div>
  );
}
