# AgentOS

Multi-tenant SaaS that helps small marketing agencies reply to leads faster, with a human approving every outgoing message.
Full plan: `docs/blueprint.html` (PDF: `AgentOS_Blueprint.pdf`).

## MVP flow (the only feature right now)

lead intake → AI extract / score / draft → human approval → email sent → audit event

## Stack

| Area | Choice |
|---|---|
| Frontend | Next.js (App Router) + TypeScript + Tailwind + shadcn/ui — `apps/web` |
| Backend | FastAPI + Pydantic v2 + SQLAlchemy 2 + Alembic — `apps/api` (Python 3.12 via uv) |
| Database | PostgreSQL 16 (pgvector only from Phase 2) |
| Jobs | Redis 7 + arq worker (same codebase as the API, separate process) |
| AI | Free via OmniRoute (`http://localhost:20128/v1`, OpenAI-compatible), model chosen from what currently works (now `agy/gemini-3.8-flash-high`, fallback `kr/claude-haiku-4.5`); JSON output validated with Pydantic. Claude API later. |
| Email | Resend |
| Auth | Clerk (organisation = tenant) |
| Errors | Sentry |

## Rules (never break these)

1. Every table has `tenant_id`, and every query filters by it.
2. No external message is sent before a human approves it. Only the worker sends, and only for `APPROVED` leads.
3. Lead status changes only through `app/workflow/transitions.py::transition()`, which writes an audit event in the same transaction.
4. `audit_events` are append-only: never update or delete them.
5. AI output is validated with Pydantic before it is saved or shown. Invalid output → retry, then `FAILED`.
6. Intake and sends carry idempotency keys; a retry must never create duplicates.
7. Secrets live only in `.env` (git-ignored). Never hard-code or log keys.
8. Keep code minimal: no unused abstractions, no tools the current phase doesn't need.

## Lead states

`NEW → PROCESSING → DRAFT_READY → APPROVED → SENT`, plus `PROCESSING → FAILED` (retryable) and `DRAFT_READY → REJECTED`.

## External agents (later, Jarvis layer)

OpenClaw and Hermes Agent sit on top of AgentOS as an assistant layer. They talk to AgentOS **only through its HTTP API**, with their own scoped key. They may read leads and request drafts; they may **not** approve or send. Approval stays in the AgentOS UI.

Decided (2026-11-11):
- **OpenClaw** = Jarvis front door: chats with the owner on Telegram, answers "what's new?", asks AgentOS for summaries.
- **Hermes** = background automations: cron reports (daily lead digest), research, follow-up reminders. Separate Telegram bot.
- **Claude Code** = builds and maintains AgentOS.
- Both agents run in **Docker on this Mac** (sandboxed), and use **OmniRoute** (`host.docker.internal:20128/v1`) as their LLM gateway.
- No paid API keys (decided 2026-11-11): everything, including AgentOS's own AI calls, goes through OmniRoute's free models (OpenAI-compatible; free models change, so re-test before relying on one). Swap to the Claude API later by changing the base URL and key.
- Jarvis setup lives in `jarvis/` (see `jarvis/README.md`). OpenClaw delegates to Hermes via `http://hermes:8642/v1/chat/completions`.

## Commands

- `make setup` — first time: create `.env`, install API (uv) and web (pnpm) deps
- `make dev` — start Postgres + Redis (Docker), API, worker and web; Ctrl+C stops all
- `make test` — pytest + ruff, web lint
- `make migrate` — apply DB migrations
- `make down` — stop Postgres + Redis

Ports (chosen to avoid clashes on this Mac): web 3000, API 8000, Postgres 5434, Redis 6380, OmniRoute 20128, OpenClaw 18789, Hermes 8642.
