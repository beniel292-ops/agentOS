"use client";

import { OrganizationSwitcher, UserButton } from "@clerk/nextjs";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const links = [
  { href: "/", label: "Home" },
  { href: "/leads", label: "Leads" },
  { href: "/cos", label: "Chief of Staff" },
  { href: "/settings", label: "Settings" },
];

export function AppHeader() {
  const pathname = usePathname();
  return (
    <header className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">AgentOS</h1>
          <p className="text-muted-foreground">Lead reply MVP</p>
        </div>
        <div className="flex items-center gap-3">
          <OrganizationSwitcher hidePersonal />
          <UserButton />
        </div>
      </div>
      <nav className="flex gap-4 text-sm">
        {links.map((l) => (
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
    </header>
  );
}
