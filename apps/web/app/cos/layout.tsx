"use client";

import { useAuth, useOrganization } from "@clerk/nextjs";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { AppHeader } from "@/components/app-header";
import { Badge } from "@/components/ui/badge";
import { apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";

import { CosContext, StopCard, type CosSettings, type Role } from "./cos-context";

const links = [
  { href: "/cos", label: "Overview", owner: false },
  { href: "/cos/tasks", label: "Tasks", owner: false },
  { href: "/cos/opportunities", label: "Opportunities", owner: false },
  { href: "/cos/calls", label: "Calls", owner: true },
  { href: "/cos/activity", label: "Activity", owner: true },
];

function CosShell({ children }: { children: React.ReactNode }) {
  const { getToken } = useAuth();
  const pathname = usePathname();
  const [role, setRole] = useState<Role | null>(null);
  const [settings, setSettings] = useState<CosSettings | null>(null);
  const [failed, setFailed] = useState(false);

  const [n, setN] = useState(0);
  const reload = () => setN((x) => x + 1);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [me, s] = await Promise.all([
          apiFetch<{ user: { role: Role } }>(getToken, "/v1/me"),
          apiFetch<CosSettings>(getToken, "/v1/cos/settings"),
        ]);
        if (cancelled) return;
        setRole(me.data.user.role);
        setSettings(s.data);
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
    <CosContext.Provider value={{ role, settings, reload }}>
      <StopCard />
      <nav className="flex flex-wrap gap-4 text-sm">
        {links
          .filter((l) => !l.owner || role === "owner")
          .map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className={cn(
                "text-muted-foreground hover:text-foreground",
                pathname === l.href && "font-medium text-foreground",
              )}
            >
              {l.label}
            </Link>
          ))}
      </nav>
      {children}
    </CosContext.Provider>
  );
}

export default function CosLayout({ children }: { children: React.ReactNode }) {
  const { isLoaded, organization } = useOrganization();
  return (
    <div className="mx-auto flex min-h-screen w-full max-w-3xl flex-col gap-6 p-6">
      <AppHeader />
      {!isLoaded ? null : organization ? (
        <CosShell key={organization.id}>{children}</CosShell>
      ) : (
        <p className="text-muted-foreground">Create an organization on the Home page to continue.</p>
      )}
    </div>
  );
}
