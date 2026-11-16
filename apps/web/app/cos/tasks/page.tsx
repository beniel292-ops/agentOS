"use client";

import { useAuth } from "@clerk/nextjs";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { apiFetch } from "@/lib/api";

type Task = {
  id: string;
  title: string;
  notes: string | null;
  status: string;
  priority: number;
  due_date: string | null;
  project: string | null;
  source: string | null;
};

const FILTERS = ["all", "pending", "in_progress", "done", "dropped"];

export default function TasksPage() {
  const { getToken } = useAuth();
  const [filter, setFilter] = useState("all");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [n, setN] = useState(0);
  const load = () => setN((x) => x + 1);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const q = filter === "all" ? "" : `status=${filter}&`;
        const r = await apiFetch<Task[]>(getToken, `/v1/cos/tasks?${q}limit=200`);
        if (cancelled) return;
        setTasks(r.data);
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

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      load();
    } catch (e) {
      setError(e instanceof Error && e.message === "423" ? "Chief of Staff is stopped" : "Could not save.");
    } finally {
      setBusy(false);
    }
  }

  const setStatus = (id: string, status: string) =>
    run(() => apiFetch(getToken, `/v1/cos/tasks/${id}`, { method: "PATCH", body: JSON.stringify({ status }) }));

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
        <CardTitle>Tasks</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-wrap gap-2">
          {FILTERS.map((f) => (
            <Button key={f} size="sm" variant={filter === f ? "default" : "outline"} onClick={() => setFilter(f)}>
              {f === "all" ? "All" : f.replace("_", " ")}
            </Button>
          ))}
        </div>
        {error && <Badge variant="destructive">{error}</Badge>}
        {loadFailed && <Badge variant="destructive">Something went wrong. Try again.</Badge>}
        {!loaded && !loadFailed ? (
          <Badge variant="secondary">Loading...</Badge>
        ) : tasks.length === 0 ? (
          <p className="text-sm text-muted-foreground">No tasks.</p>
        ) : (
          <ul className="flex flex-col divide-y">
            {tasks.map((t) => (
              <li key={t.id} className="flex flex-wrap items-start justify-between gap-2 py-2">
                <div className="min-w-0 flex-1">
                  <p className={t.status === "done" ? "line-through" : "font-medium"}>{t.title}</p>
                  {t.notes && <p className="line-clamp-3 whitespace-pre-wrap text-sm text-muted-foreground">{t.notes}</p>}
                  <p className="text-xs text-muted-foreground">
                    P{t.priority} · {t.status.replace("_", " ")}
                    {t.due_date && ` · due ${t.due_date}`}
                    {t.project && ` · ${t.project}`}
                    {t.source && ` · ${t.source}`}
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button size="sm" variant="outline" disabled={busy} onClick={() => setStatus(t.id, "done")}>
                    Done
                  </Button>
                  <Button size="sm" variant="ghost" disabled={busy} onClick={() => setStatus(t.id, "pending")}>
                    Pending
                  </Button>
                  <Button size="sm" variant="ghost" disabled={busy} onClick={() => setStatus(t.id, "dropped")}>
                    Drop
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
