"use client";

import { useAuth, useOrganization } from "@clerk/nextjs";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { AppHeader } from "@/components/app-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { apiFetch } from "@/lib/api";
import { ACTION, STATUS_STYLE, timeAgo } from "@/lib/leads";

type Detail = {
  id: string;
  name: string;
  email: string;
  company: string | null;
  message: string;
  source: string;
  status: string;
  fit_score: number | null;
  last_error: string | null;
  created_at: string;
  analysis: {
    intent: string | null;
    service: string | null;
    budget: string | null;
    urgency: string | null;
    fit_score: number | null;
    reasoning: string | null;
  } | null;
  draft: {
    id: string;
    subject: string;
    body: string;
    edited_body: string | null;
    decision: "approved" | "rejected" | null;
    decided_at: string | null;
    reject_reason: string | null;
    sent: boolean;
  } | null;
  timeline: {
    id: string;
    at: string;
    actor: string;
    action: string;
    from_state: string | null;
    to_state: string | null;
  }[];
};

function actor(a: string) {
  if (a.startsWith("user:")) return "Dashboard";
  if (a.startsWith("telegram:")) return "Telegram";
  if (a === "worker") return "AgentOS";
  if (a.startsWith("intake:")) return "Lead form";
  return a;
}

function errorText(e: unknown) {
  const code = e instanceof Error ? e.message : "";
  if (code === "409") return "Someone already handled this lead. Refreshed.";
  if (code === "422") return "Check the text and try again.";
  return "Something went wrong. Try again.";
}

function DraftSection({
  lead,
  onDone,
  onError,
}: {
  lead: Detail;
  onDone: () => Promise<void>;
  onError: (m: string | null) => void;
}) {
  const { getToken } = useAuth();
  const d = lead.draft;
  const [text, setText] = useState(d ? (d.edited_body ?? d.body) : "");
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  async function post(path: string, body: object) {
    setBusy(true);
    onError(null);
    try {
      await apiFetch(getToken, path, { method: "POST", body: JSON.stringify(body) });
    } catch (e) {
      onError(errorText(e));
      // 422: keep the form as is so the text can be fixed; otherwise refetch
      if (e instanceof Error && e.message === "422") {
        setBusy(false);
        return;
      }
    }
    await onDone();
    setBusy(false);
  }

  if (!d) return <p className="text-sm text-muted-foreground">No draft.</p>;
  const editable = lead.status === "DRAFT_READY" && d.decision === null;
  const final = d.edited_body ?? d.body;
  let decision = "";
  if (d.sent || lead.status === "SENT") decision = "Sent";
  else if (d.decision === "approved") decision = "Approved · sending…";
  else if (d.decision === "rejected") decision = `Rejected: ${d.reject_reason ?? ""}`;

  return (
    <div className="grid gap-3">
      <div className="text-sm font-medium">{d.subject}</div>
      {editable ? (
        <>
          <Textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={10}
            maxLength={10000}
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button
              disabled={busy || !text.trim()}
              onClick={() =>
                void post(`/v1/drafts/${d.id}/approve`, text !== d.body ? { edited_body: text } : {})
              }
            >
              {busy ? "Saving…" : "Approve"}
            </Button>
            <Button variant="outline" disabled={busy} onClick={() => setRejecting(true)}>
              Reject
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">The email is sent right after you approve.</p>
          {rejecting && (
            <div className="flex flex-wrap items-center gap-2">
              <Input
                className="flex-1"
                placeholder="Reason for rejecting"
                value={reason}
                maxLength={500}
                onChange={(e) => setReason(e.target.value)}
              />
              <Button
                variant="destructive"
                disabled={busy || !reason.trim()}
                onClick={() => void post(`/v1/drafts/${d.id}/reject`, { reason: reason.trim() })}
              >
                {busy ? "Saving…" : "Confirm reject"}
              </Button>
            </div>
          )}
        </>
      ) : (
        <>
          <p className="whitespace-pre-wrap text-sm">{final}</p>
          {decision && <Badge variant="secondary">{decision}</Badge>}
        </>
      )}
    </div>
  );
}

// Parent keys this by org id, so switching organization remounts and refetches.
function LeadDetail({ id }: { id: string }) {
  const { getToken } = useAuth();
  const [lead, setLead] = useState<Detail | null>(null);
  const [failed, setFailed] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const { data } = await apiFetch<Detail>(getToken, `/v1/leads/${id}`);
      setLead(data);
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [getToken, id]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  // After approval, poll (max 2 minutes) until the worker marks it SENT.
  const waiting = lead?.status === "APPROVED" && !lead.draft?.sent;
  useEffect(() => {
    if (!waiting) return;
    const started = Date.now();
    const t = setInterval(() => {
      if (Date.now() - started > 120000) clearInterval(t);
      else void load();
    }, 5000);
    return () => clearInterval(t);
  }, [waiting, load]);

  async function act(path: string) {
    setActionError(null);
    try {
      await apiFetch(getToken, `/v1/leads/${id}/${path}`, { method: "POST" });
    } catch (e) {
      setActionError(errorText(e));
    }
    await load();
  }

  if (failed && !lead) return <Badge variant="destructive">Could not load lead</Badge>;
  if (!lead) return <Badge variant="secondary">Loading...</Badge>;

  const a = lead.analysis;
  const details = a
    ? ([
        ["Intent", a.intent],
        ["Service", a.service],
        ["Budget", a.budget],
        ["Urgency", a.urgency],
        ["Reasoning", a.reasoning],
      ] as [string, string | null][]).filter(([, v]) => v)
    : [];
  const action = ACTION[lead.status];

  return (
    <>
      <Link href="/leads" className="text-sm text-muted-foreground hover:underline">
        ← Inbox
      </Link>
      <Card>
        <CardHeader>
          <CardTitle>{lead.name}</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-2 text-sm">
          {lead.company && <div>{lead.company}</div>}
          <div>{lead.email}</div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge
              variant="secondary"
              className={STATUS_STYLE[lead.status]}
              title={lead.last_error ?? undefined}
            >
              {lead.status}
            </Badge>
            <span>Fit: {lead.fit_score ?? "-"}</span>
            <span className="text-muted-foreground">
              Received {new Date(lead.created_at).toLocaleString()} ({timeAgo(lead.created_at)})
            </span>
          </div>
          {action && (
            <div className="flex items-center gap-2">
              <Button size="sm" variant="outline" onClick={() => void act(action.path)}>
                {action.label}
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Message</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="whitespace-pre-wrap text-sm">{lead.message}</p>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Extracted details</CardTitle>
        </CardHeader>
        <CardContent>
          {!a ? (
            <p className="text-sm text-muted-foreground">Not analysed yet</p>
          ) : (
            <dl className="grid gap-2 text-sm">
              {details.map(([k, v]) => (
                <div key={k}>
                  <dt className="text-muted-foreground">{k}</dt>
                  <dd className="whitespace-pre-wrap">{v}</dd>
                </div>
              ))}
            </dl>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Draft</CardTitle>
        </CardHeader>
        <CardContent>
          <DraftSection
            key={`${lead.draft?.id}-${lead.draft?.decision}-${lead.status}`}
            lead={lead}
            onDone={load}
            onError={setActionError}
          />
          {actionError && <p className="mt-2 text-sm text-destructive">{actionError}</p>}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Timeline</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="grid gap-2 text-sm">
            {lead.timeline.map((t) => (
              <li key={t.id} className="flex flex-wrap gap-x-3">
                <span className="text-muted-foreground">{new Date(t.at).toLocaleString()}</span>
                <span>{actor(t.actor)}</span>
                <span>{t.from_state && t.to_state ? `${t.from_state} → ${t.to_state}` : t.action}</span>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </>
  );
}

export default function LeadPage() {
  const { id } = useParams<{ id: string }>();
  const { isLoaded, organization } = useOrganization();
  return (
    <div className="mx-auto flex min-h-screen w-full max-w-3xl flex-col gap-6 p-6">
      <AppHeader />
      {!isLoaded ? null : organization ? (
        <LeadDetail key={organization.id} id={id} />
      ) : (
        <p className="text-muted-foreground">Create an organization on the Home page to continue.</p>
      )}
    </div>
  );
}
