"use client";

import { useAuth, useOrganization } from "@clerk/nextjs";
import { useEffect, useState } from "react";

import { AppHeader } from "@/components/app-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiFetch } from "@/lib/api";

type Role = "owner" | "member";
type Task = { id: string; title: string; status: string; priority: number; due_date: string | null };
type Call = { id: string; kind: string; status: string; due_at: string; reason: string | null };
type CosSettings = { stopped: boolean; stopped_at: string | null; stopped_by: string | null };
type AgentKey = { id: string; name: string; scopes: string[]; key_prefix: string; revoked_at: string | null };

const SCOPES = [
  "tasks:read",
  "tasks:write",
  "callbacks:write",
  "checkins:write",
  "opportunities:read",
  "opportunities:write",
  "memory:read",
  "memory:write",
  "stop",
];

const fmt = (iso: string) => new Date(iso).toLocaleString();

function StopCard({
  settings,
  role,
  onChange,
}: {
  settings: CosSettings;
  role: Role | null;
  onChange: () => void;
}) {
  const { getToken } = useAuth();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function post(path: string) {
    setBusy(true);
    setFailed(false);
    try {
      await apiFetch(getToken, `/v1/cos/${path}`, { method: "POST" });
      onChange();
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

function TasksCard({ tasks, onChange }: { tasks: Task[]; onChange: () => void }) {
  const { getToken } = useAuth();
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setFailed(false);
    try {
      await fn();
      onChange();
    } catch {
      setFailed(true); // includes 423 while stopped
    } finally {
      setBusy(false);
    }
  }

  const setStatus = (id: string, status: string) =>
    run(() =>
      apiFetch(getToken, `/v1/cos/tasks/${id}`, { method: "PATCH", body: JSON.stringify({ status }) }),
    );

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;
    await run(async () => {
      await apiFetch(getToken, "/v1/cos/tasks", {
        method: "POST",
        body: JSON.stringify({ title: title.trim(), source: "dashboard" }),
      });
      setTitle("");
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Today&apos;s tasks</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {failed && <Badge variant="destructive">Could not save. Is Chief of Staff stopped?</Badge>}
        {tasks.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing due today.</p>
        ) : (
          <ul className="flex flex-col divide-y">
            {tasks.map((t) => (
              <li key={t.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <div className="min-w-0">
                  <p className={t.status === "done" ? "line-through" : "font-medium"}>{t.title}</p>
                  <p className="text-xs text-muted-foreground">
                    P{t.priority} · {t.status.replace("_", " ")}
                    {t.due_date && ` · due ${t.due_date}`}
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button size="sm" variant="outline" disabled={busy} onClick={() => setStatus(t.id, "done")}>
                    Done
                  </Button>
                  <Button size="sm" variant="ghost" disabled={busy} onClick={() => setStatus(t.id, "pending")}>
                    Pending
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
        <form onSubmit={add} className="flex gap-2">
          <Input
            aria-label="New task"
            maxLength={300}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Add a task"
          />
          <Button type="submit" disabled={busy || !title.trim()}>
            Add
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function CallsCard({ calls }: { calls: Call[] }) {
  const pending = calls.find((c) => c.kind === "callback" && c.status === "pending");
  return (
    <Card>
      <CardHeader>
        <CardTitle>Calls</CardTitle>
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
              <li key={c.id} className="flex items-center justify-between py-1.5">
                <span>
                  {c.kind} · {fmt(c.due_at)}
                </span>
                <Badge variant="secondary">{c.status}</Badge>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function KeysCard() {
  const { getToken } = useAuth();
  const [keys, setKeys] = useState<AgentKey[]>([]);
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>([]);
  const [newKey, setNewKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  const [n, setN] = useState(0);
  const load = () => setN((x) => x + 1);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await apiFetch<AgentKey[]>(getToken, "/v1/cos/agent-keys");
        if (!cancelled) setKeys(r.data);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getToken, n]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setFailed(false);
    try {
      const { data } = await apiFetch<{ key: string }>(getToken, "/v1/cos/agent-keys", {
        method: "POST",
        body: JSON.stringify({ name: name.trim(), scopes }),
      });
      setNewKey(data.key);
      setCopied(false);
      setName("");
      setScopes([]);
      load();
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: string) {
    try {
      await apiFetch(getToken, `/v1/cos/agent-keys/${id}/revoke`, { method: "POST" });
      load();
    } catch {
      setFailed(true);
    }
  }

  async function copy() {
    if (!newKey) return;
    await navigator.clipboard.writeText(newKey);
    setCopied(true);
  }

  const toggle = (s: string) =>
    setScopes((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]));

  return (
    <Card>
      <CardHeader>
        <CardTitle>Agent keys</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {failed && (
          <Badge variant="destructive">Something went wrong (names must be unique and lowercase).</Badge>
        )}
        {newKey && (
          <div className="flex flex-col gap-2 rounded-md border p-3">
            <p className="text-sm font-medium text-destructive">
              Copy this key now. It will not be shown again.
            </p>
            <div className="flex items-center gap-2">
              <code className="min-w-0 flex-1 break-all rounded bg-muted px-2 py-1 text-sm">{newKey}</code>
              <Button variant="outline" onClick={copy}>
                {copied ? "Copied" : "Copy"}
              </Button>
            </div>
          </div>
        )}
        <ul className="flex flex-col divide-y text-sm">
          {keys.length === 0 && <li className="py-2 text-muted-foreground">No keys yet.</li>}
          {keys.map((k) => (
            <li key={k.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <div className="min-w-0">
                <p className="font-medium">
                  {k.name} <code className="text-xs text-muted-foreground">{k.key_prefix}…</code>
                </p>
                <p className="text-xs text-muted-foreground">{k.scopes.join(", ")}</p>
              </div>
              {k.revoked_at ? (
                <Badge variant="secondary">revoked</Badge>
              ) : (
                <Button size="sm" variant="outline" onClick={() => revoke(k.id)}>
                  Revoke
                </Button>
              )}
            </li>
          ))}
        </ul>
        <form onSubmit={create} className="flex flex-col gap-3 border-t pt-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="key-name">New key name</Label>
            <Input
              id="key-name"
              maxLength={50}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="hermes"
            />
          </div>
          <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
            {SCOPES.map((s) => (
              <label key={s} className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={scopes.includes(s)} onChange={() => toggle(s)} />
                {s}
              </label>
            ))}
          </div>
          <div>
            <Button type="submit" disabled={busy || !name.trim() || scopes.length === 0}>
              Create key
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function CosPanel() {
  const { getToken } = useAuth();
  const [role, setRole] = useState<Role | null>(null);
  const [settings, setSettings] = useState<CosSettings | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [calls, setCalls] = useState<Call[]>([]);
  const [failed, setFailed] = useState(false);

  const [n, setN] = useState(0);
  const load = () => setN((x) => x + 1);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [me, s, t, c] = await Promise.all([
          apiFetch<{ user: { role: Role } }>(getToken, "/v1/me"),
          apiFetch<CosSettings>(getToken, "/v1/cos/settings"),
          apiFetch<Task[]>(getToken, "/v1/cos/tasks?due=today"),
          apiFetch<Call[]>(getToken, "/v1/cos/calls?limit=10"),
        ]);
        if (cancelled) return;
        setRole(me.data.user.role);
        setSettings(s.data);
        setTasks(t.data);
        setCalls(c.data);
        setFailed(false);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getToken, n]);

  if (failed) return <Badge variant="destructive">Something went wrong. Try again.</Badge>;
  if (!settings) return <Badge variant="secondary">Loading...</Badge>;
  return (
    <>
      <StopCard settings={settings} role={role} onChange={load} />
      <TasksCard tasks={tasks} onChange={load} />
      <CallsCard calls={calls} />
      {role === "owner" && <KeysCard />}
    </>
  );
}

export default function CosPage() {
  const { isLoaded, organization } = useOrganization();
  return (
    <div className="mx-auto flex min-h-screen w-full max-w-3xl flex-col gap-6 p-6">
      <AppHeader />
      {!isLoaded ? null : organization ? (
        <CosPanel key={organization.id} />
      ) : (
        <p className="text-muted-foreground">Create an organization on the Home page to continue.</p>
      )}
    </div>
  );
}
