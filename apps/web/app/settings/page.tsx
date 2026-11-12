"use client";

import { useAuth, useOrganization } from "@clerk/nextjs";
import { useEffect, useState } from "react";

import { AppHeader } from "@/components/app-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { API_URL, apiFetch } from "@/lib/api";

type Role = "owner" | "member";

function IntakeKeyCard() {
  const { getToken } = useAuth();
  const [prefix, setPrefix] = useState<string | null | undefined>(undefined);
  const [role, setRole] = useState<Role | null>(null);
  const [newKey, setNewKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [k, me] = await Promise.all([
          apiFetch<{ prefix: string | null }>(getToken, "/v1/intake-key"),
          apiFetch<{ user: { role: Role } }>(getToken, "/v1/me"),
        ]);
        if (cancelled) return;
        setPrefix(k.data.prefix);
        setRole(me.data.user.role);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getToken]);

  async function generate() {
    setBusy(true);
    setFailed(false);
    try {
      const { data } = await apiFetch<{ key: string; prefix: string }>(
        getToken,
        "/v1/intake-key",
        { method: "POST" },
      );
      setNewKey(data.key);
      setPrefix(data.prefix);
      setCopied(false);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!newKey) return;
    await navigator.clipboard.writeText(newKey);
    setCopied(true);
  }

  const curl = `curl -X POST ${API_URL}/v1/leads \\
  -H "X-Intake-Key: ${newKey ?? "<your intake key>"}" \\
  -H "Content-Type: application/json" \\
  -d '{"name": "Ann Lee", "email": "ann@example.com", "message": "Can you build us a website?"}'`;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Website intake key</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {failed && <Badge variant="destructive">Something went wrong. Try again.</Badge>}
        {prefix === undefined ? (
          !failed && <Badge variant="secondary">Loading...</Badge>
        ) : (
          <div className="flex flex-wrap items-center gap-3">
            {prefix ? (
              <code className="rounded bg-muted px-2 py-1 text-sm">{prefix}…</code>
            ) : (
              <span className="text-muted-foreground">No key yet</span>
            )}
            {role === "owner" && (
              <Button onClick={generate} disabled={busy} variant={prefix ? "outline" : "default"}>
                {prefix ? "Rotate key" : "Generate key"}
              </Button>
            )}
          </div>
        )}
        {newKey && (
          <div className="flex flex-col gap-2 rounded-md border p-3">
            <p className="text-sm font-medium text-destructive">
              Copy this key now. It will not be shown again.
            </p>
            <div className="flex items-center gap-2">
              <code className="min-w-0 flex-1 break-all rounded bg-muted px-2 py-1 text-sm">
                {newKey}
              </code>
              <Button variant="outline" onClick={copy}>
                {copied ? "Copied" : "Copy"}
              </Button>
            </div>
          </div>
        )}
        {prefix && role === "member" && (
          <p className="text-sm text-muted-foreground">Only the owner can rotate the key.</p>
        )}
        <div className="flex flex-col gap-1.5">
          <p className="text-sm text-muted-foreground">
            Send leads from your website form with:
          </p>
          <pre className="overflow-x-auto rounded bg-muted p-3 text-xs">{curl}</pre>
        </div>
      </CardContent>
    </Card>
  );
}

export default function SettingsPage() {
  const { isLoaded, organization } = useOrganization();
  return (
    <div className="mx-auto flex min-h-screen w-full max-w-3xl flex-col gap-6 p-6">
      <AppHeader />
      {!isLoaded ? null : organization ? (
        <IntakeKeyCard key={organization.id} />
      ) : (
        <p className="text-muted-foreground">Create an organization on the Home page to continue.</p>
      )}
    </div>
  );
}
