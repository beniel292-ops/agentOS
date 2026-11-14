# Personal AI Chief of Staff — Phase 1: infrastructure audit and architecture

Status: **proposal, waiting for approval**. Nothing here is built yet. Date: 2026-11-14.

## 1. What already exists (reuse, don't duplicate)

| Area | What exists today | Where |
|---|---|---|
| Database | Neon Postgres (`neondb`), 6 AgentOS tables + Alembic migrations; local Docker Postgres for tests | `apps/api/app/models`, `migrations/` |
| Vector DB | **None yet.** pgvector is available on Neon but not enabled | — |
| Memory | Hermes: `USER.md` + session search (FTS5). OpenClaw: workspace memory files + nightly "dreaming" consolidation (03:00). AgentOS: append-only `audit_events` | Docker volume `jarvis-hermes-data`, `jarvis/data/openclaw/workspace` |
| Scheduling | AgentOS arq worker (cron each minute); OpenClaw cron (heartbeat 30 min, dreaming 03:00); Hermes cron scheduler (no jobs) | `apps/api/app/jobs/worker.py` |
| Agents | OpenClaw = Jarvis front door; Hermes = background worker; OpenClaw → Hermes delegation over HTTP | `jarvis/` |
| Models | OmniRoute (free cloud: Gemini 3.8 Flash, Claude Haiku 4.5 via Kiro, …) + Ollama `qwen-agent` (Qwen3.5 9B, local). **No OpenRouter, no paid Anthropic key** | OmniRoute container, Ollama app |
| Telegram | @BeniJarvisBot (OpenClaw), @BenifuryBot (Hermes), both paired to your user ID 8371851507. Approvals bot planned (6b), token not yet provided | `jarvis/.env`, Hermes volume |
| Auth / API | FastAPI with Clerk JWT, per-tenant intake keys (hashed), tenant scoping everywhere, audit trail | `apps/api/app/auth.py` |
| Dashboard | Next.js app (Leads, Settings) with Clerk login | `apps/web` |
| Google Drive | Not connected for the agents | — |

## 2. The calling engine: agentring (your fork of mutonby/agentring)

- VoIP calls to an **Android** app that rings like a real call even when the phone is locked. No phone number, no Twilio, no per-minute fees.
- Parts: `relay` (TypeScript, self-hosted in Docker with LiveKit for audio), `daemon` (Python CLI + MCP server + voice AI), Android app (Kotlin), Firebase (free) for the push that wakes the phone.
- Voice AI: Gemini Live (free tier, cloud) by default; OpenAI/xAI/Phonic (paid); or experimental fully-local.
- "Call only my number" is built in: calls go only to the **paired** app (pairing by QR code = the allowlist). Cellular calls to a real number would need a paid, KYC'd provider (Exotel/Twilio) — not proposed for now.
- Your fork is identical to upstream (commit f4b7ab8, 0 extra commits). Security audit: see section 6.

## 3. Proposed architecture

```
                    ┌──────────── your Android phone (agentring app, paired) ────────────┐
                    │ rings for check-ins / callbacks; you talk; you can also call Jarvis │
                    └──────────────▲─────────────────────────────────────────────────────┘
                                   │ FCM push + LiveKit audio (relay on this Mac, phone via Wi-Fi/Tailscale)
 ┌─────────────── this Mac ────────┴──────────────────────────────────────────────────────┐
 │ agentring daemon (voice AI, --agents none) ──MCP, tools allowlist──▶ AgentOS `cos` MCP:  │
 │   list_tasks · add_task · update_task · schedule_callback (voice-scoped key, no shell)   │
 │                                                                                          │
 │ AgentOS API (FastAPI) = CENTRAL PERMISSION LAYER                                         │
 │   tasks · check-ins · scheduled calls · opportunities · memory notes · kill switch       │
 │   every agent gets its own scoped key; every action is audited                           │
 │        ▲            ▲                 ▲                    │                            │
 │   OpenClaw       Hermes            dashboard            arq worker: schedules calls      │
 │   (Jarvis bot)   (Fury bot,        (Next.js)            (morning/evening/callbacks,      │
 │   reads/plans    research, opps)   STOP button          dedup, quiet hours, max/day),    │
 │                                                         sends Telegram summaries         │
 │ Models: OmniRoute (free cloud, "Claude"/Gemini for reasoning) · Ollama qwen-agent (local)│
 └──────────────────────────────────────────────────────────────────────────────────────────┘
          Neon Postgres (+ pgvector later) — single source of truth for tasks and memory
```

### Responsibilities (each agent has one job and its own key)

| Component | Allowed | Not allowed |
|---|---|---|
| Voice AI (agentring) | Only 4 MCP tools: list tasks, add task, update task status, schedule a callback (10/20/30/custom min) | Command agents (`agents.yaml` is never created), shell, files, OpenClaw/Hermes, the relay `ak_` key |
| OpenClaw (Jarvis) | Read tasks/summaries; add tasks; schedule a callback; answer you on Telegram | Approve/send outreach; change security settings |
| Hermes (Fury) | Research; write **opportunities** (title, source, description, relevance, value, deadline, next action, verification status); read tasks | Contact anyone; modify tasks |
| AgentOS worker | Place scheduled calls; send Telegram summaries/transcripts/alerts | Anything outside the schedule rules |
| Local Qwen | Light jobs: summarising transcripts, dedup of memory notes, classifying opportunities | — |
| Cloud models (OmniRoute) | Complex reasoning, drafting | — |

### New data (Neon, tenant-scoped, migrations)
`tasks` (title, status, priority, due, project, source) · `checkins` (type morning/evening/callback, transcript, summary) · `scheduled_calls` (kind, due_at, status, **unique per (tenant, kind, due window)** for dedup) · `opportunities` (fields above, unique on source URL) · `memory_notes` (kind preference/project/decision/deadline, text, source, dedup hash; pgvector embedding later) · `system_flags` (kill switch, quiet hours, max calls/day).

### Daily flow
1. **Morning call** (your time, e.g. 08:30 IST): "What do you need to get done today?" → tasks saved, deadlines captured, yesterday's unfinished tasks raised.
2. "Call me again in 20 minutes" → one `scheduled_calls` row (dedup: an existing pending callback is moved, not duplicated).
3. **Callback**: what's done, what's pending, did priorities change → tasks updated.
4. **Evening call** (e.g. 21:00): accountability recap; summary + transcript to Telegram.
5. Opportunities found by Hermes → Telegram digest + mentioned on the next call. Nothing is ever sent to other people automatically.

## 4. Security design (mandatory controls)

| Control | How |
|---|---|
| Call allowlist | Only the paired agentring device; only the AgentOS worker holds the relay key (it can pair devices), never OpenClaw/Hermes; paired devices reviewed |
| Telegram allowlist | Only user ID 8371851507 / your private chat; groups rejected |
| No arbitrary execution | agentring runs with `--agents none` (no command agents at all, per the audit); the voice AI's only tools are 4 AgentOS MCP tools with an explicit allowlist; its words are data (JSON), never commands |
| Scoped keys | Separate hashed keys per agent (voice, openclaw, hermes) with per-route permissions; no shared credentials |
| Untrusted input | Transcripts, web pages, Drive docs = data. Prompts wrap them in tags; agents can't turn them into actions without the permission layer |
| Rate limits / call limits | Max calls per day, minimum gap between calls, quiet hours (e.g. 22:30–07:30), callback dedup |
| Kill switch | `system_flags.stop`: dashboard STOP button, Telegram `/stop`, and `./jarvis.sh stop` — worker refuses all calls/sends while set |
| Audit | Every call, task change, opportunity and permission decision in append-only `audit_events` |
| Network | Relay/LiveKit bound to this Mac + Tailscale (no public ports); Postgres/Redis localhost only (already) |
| Google Drive | OAuth `drive.readonly`, your account only, token file mode 600; no write scopes |
| Secrets | `.env` files only (git-ignored), never logged or sent to models |

## 5. What I need from you

1. **An Android phone** for the agentring app (no iPhone app exists). If you only have an iPhone, the fallback is Telegram voice notes + summaries (no ringing calls).
2. **A free Firebase project** (makes the locked phone ring) — I'll walk you through it.
3. **A Gemini API key** (free, aistudio.google.com) for the voice — or we try the experimental fully-local voice first.
4. **Tailscale** (free) on the Mac and phone, so calls work outside home Wi-Fi.
5. Your **schedule**: morning and evening call times, quiet hours (default 22:30–07:30 IST), max calls per day.
6. Later (Drive phase): a Google Cloud OAuth client for Drive read-only.
7. Still pending from AgentOS 6b: the approvals bot token.

## 6. agentring security audit

Full report: `security-audit-agentring.md`. Summary: **no malware, backdoor, telemetry or credential theft found in a manual read** (not a guarantee: dependencies' code, Docker images and the Play APK weren't reviewed). The real risk is design: by default the voice AI can hand free-form tasks to OpenClaw/Hermes/Claude Code. Our design removes that path entirely (`--agents none`, MCP tools allowlist, relay key only in the AgentOS worker), installs the daemon from the audited commit with `uv sync --frozen`, uses Tailscale instead of cleartext LAN, and avoids the debug-signed preview APK.

## 7. Proposed phase order

| Phase | Deliverable |
|---|---|
| 2 | Central tasks / check-ins / scheduled calls / memory notes / kill switch tables + API + scoped agent keys + tests |
| 3 | agentring self-hosted on this Mac (relay + LiveKit in Docker, daemon), Android paired, one test call |
| 4 | Morning/evening check-ins, callbacks (10/20/30/custom), dedup, quiet hours, transcripts saved |
| 5 | Telegram: summaries, transcripts, callback confirmations, `/stop`; approvals bot (AgentOS 6b) |
| 6 | Google Drive read-only: search, summarise, unfinished-work detection |
| 7 | OpenClaw / Hermes scoped keys + skills; local Qwen for light jobs |
| 8 | Opportunity detection (Hermes cron + web search) |
| 9 | Security testing (prompt-injection, abuse, rate limits, kill switch drills) |
| 10 | Dashboard pages (tasks, calls, transcripts, opportunities, agent activity, security log, STOP) |

## 8. Decisions (approved 2026-11-14)

- Architecture approved as proposed.
- Phone: Android (agentring app, ringing calls even when locked).
- Order: AgentOS 6b (Telegram approvals bot) + Chief of Staff Phase 2 (central tasks/memory/scheduled calls/opportunities/kill switch + scoped agent keys) first.
- Voice AI: **fully local** (`--provider local`, experimental) — nothing leaves the Mac; Gemini Live stays a possible fallback later (only after patching untrusted-text fencing).
