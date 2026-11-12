"use client";

import { useAuth, useOrganization } from "@clerk/nextjs";
import { useCallback, useEffect, useState } from "react";

import { AppHeader } from "@/components/app-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { apiFetch } from "@/lib/api";

type Lead = {
  id: string;
  name: string;
  email: string;
  status: string;
  created_at: string;
};
type Result = { id: string; status: string; duplicate: boolean };

function timeAgo(iso: string) {
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function TestLeadForm({ onSent }: { onSent: () => void }) {
  const { getToken } = useAuth();
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ text: string; error?: boolean } | null>(null);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    setBusy(true);
    setFeedback(null);
    try {
      const { data } = await apiFetch<Result>(getToken, "/v1/leads/test", {
        method: "POST",
        body: JSON.stringify({
          name: f.get("name"),
          email: f.get("email"),
          company: f.get("company") || null,
          message: f.get("message"),
        }),
      });
      setFeedback({
        text: data.duplicate ? "Duplicate — already received" : "Lead created",
      });
      if (!data.duplicate) form.reset();
      onSent();
    } catch {
      setFeedback({ text: "Could not send the lead. Check the fields and try again.", error: true });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Send a test lead</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
          <div className="grid gap-1.5">
            <Label htmlFor="name">Name</Label>
            <Input id="name" name="name" required maxLength={200} />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="email">Email</Label>
            <Input id="email" name="email" type="email" required />
          </div>
          <div className="grid gap-1.5 sm:col-span-2">
            <Label htmlFor="company">Company (optional)</Label>
            <Input id="company" name="company" maxLength={200} />
          </div>
          <div className="grid gap-1.5 sm:col-span-2">
            <Label htmlFor="message">Message</Label>
            <Textarea id="message" name="message" required maxLength={5000} />
          </div>
          <div className="flex items-center gap-3 sm:col-span-2">
            <Button type="submit" disabled={busy}>
              {busy ? "Sending..." : "Send lead"}
            </Button>
            {feedback && (
              <span className={feedback.error ? "text-sm text-destructive" : "text-sm"}>
                {feedback.text}
              </span>
            )}
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

// Parent keys this by org id, so switching organization remounts and refetches.
function LeadsPanel() {
  const { getToken } = useAuth();
  const [leads, setLeads] = useState<Lead[] | null>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await apiFetch<Lead[]>(getToken, "/v1/leads");
      setLeads(data);
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [getToken]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  return (
    <>
      <TestLeadForm onSent={load} />
      <Card>
        <CardHeader>
          <CardTitle>Leads</CardTitle>
        </CardHeader>
        <CardContent>
          {failed ? (
            <Badge variant="destructive">Could not load leads</Badge>
          ) : leads === null ? (
            <Badge variant="secondary">Loading...</Badge>
          ) : leads.length === 0 ? (
            <p className="text-sm text-muted-foreground">No leads yet.</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="py-2 pr-3 font-medium">Name</th>
                  <th className="py-2 pr-3 font-medium">Email</th>
                  <th className="py-2 pr-3 font-medium">Status</th>
                  <th className="py-2 text-right font-medium">Received</th>
                </tr>
              </thead>
              <tbody>
                {leads.map((l) => (
                  <tr key={l.id} className="border-b last:border-0">
                    <td className="py-2 pr-3">{l.name}</td>
                    <td className="py-2 pr-3">{l.email}</td>
                    <td className="py-2 pr-3">
                      <Badge variant="secondary">{l.status}</Badge>
                    </td>
                    <td className="py-2 text-right text-muted-foreground">
                      {timeAgo(l.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </>
  );
}

export default function LeadsPage() {
  const { isLoaded, organization } = useOrganization();
  return (
    <div className="mx-auto flex min-h-screen w-full max-w-3xl flex-col gap-6 p-6">
      <AppHeader />
      {!isLoaded ? null : organization ? (
        <LeadsPanel key={organization.id} />
      ) : (
        <p className="text-muted-foreground">Create an organization on the Home page to continue.</p>
      )}
    </div>
  );
}
