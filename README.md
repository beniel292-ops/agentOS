# AgentOS

AgentOS helps small marketing agencies answer new leads faster. A lead comes in, the AI pulls out the
details, scores the lead and drafts a reply, and a human approves it (on the web or on Telegram) before
anything is sent. Nothing goes out without approval. Each agency is its own tenant. A Chief of Staff
layer (Telegram bot, phone calls, Jarvis agents) sits on top.

## Prerequisites

macOS with Docker, [uv](https://docs.astral.sh/uv/) (installs Python 3.12), Node 24 and pnpm.
Ollama (local AI) and OmniRoute (free AI gateway) are used for the AI calls.

## First-time setup

```bash
make setup   # creates .env from .env.example, generates secrets, installs api + web deps
```

Then open `.env` and fill in the values you need (list below). `.env` is git-ignored; never commit it.

## Environment variables (`.env`)

| Variable | Meaning |
|---|---|
| `ANTHROPIC_API_KEY` | Optional, later: Claude API key |
| `DATABASE_URL` | Postgres connection (default points at the Docker database on port 5434) |
| `REDIS_PASSWORD` | Redis password (`make setup` generates it) |
| `REDIS_URL` | Redis connection; must contain the same password |
| `OMNIROUTE_BASE_URL` | OmniRoute OpenAI-compatible endpoint |
| `OMNIROUTE_API_KEY` | Key for OmniRoute |
| `AI_MODEL` | First model to try for extract/score/draft (local Qwen via Ollama) |
| `AI_FALLBACK_MODELS` | JSON list of models to try if the first one fails |
| `RESEND_API_KEY` | Resend key for sending approved emails |
| `RESEND_FROM` | Sender address for outgoing emails |
| `CLERK_SECRET_KEY` | Clerk backend key (login; organisation = tenant) |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Clerk key for the web app |
| `NEXT_PUBLIC_CLERK_SIGN_IN_URL` / `NEXT_PUBLIC_CLERK_SIGN_UP_URL` | Sign-in and sign-up page paths |
| `SENTRY_DSN` | Sentry address for the API; setting it turns on error tracking (see below) |
| `TELEGRAM_APPROVALS_BOT_TOKEN` | Token of the approvals bot; empty means Telegram is off |
| `TELEGRAM_OWNER_USER_ID` | Your Telegram user id; only you may use the bot |
| `TELEGRAM_OWNER_TENANT_ID` | The tenant the bot acts for |
| `WEB_APP_URL` | Public address of the web app, used in links the bot sends |
| `OPENCLAW_HOOKS_URL` | OpenClaw inbound webhook for approved voice-call requests |
| `OPENCLAW_HOOKS_TOKEN` | Token for that webhook; empty means off |
| `VOICE_AGENT_KEY` | Scoped API key the voice tools use to call the API |
| `VOICE_PROVIDER` | Voice provider (`local`) |
| `VOICE_MCP_TOKEN` | Secret path segment of the voice tools server (`make setup` generates it) |
| `VOICE_AGENTOS_API_URL` | API address the voice tools call |
| `AGENTRING_BIN` | Path to the agentring program; empty uses the default clone |
| `NEXT_PUBLIC_API_URL` | API address the browser uses. Baked in when the web app is built, so change it and run `make prod` again (set it to the tunnel address later) |
| `JARVIS_MODEL` | Model name OmniRoute exposes for the Jarvis agents |
| `TELEGRAM_BOT_TOKEN_OPENCLAW` | Telegram bot token for OpenClaw (Jarvis front door) |
| `TELEGRAM_BOT_TOKEN_HERMES` | Telegram bot token for Hermes (automations, reports) |
| `TELEGRAM_ALLOWED_USER_ID` | Your Telegram id, the only user the Jarvis bots answer |
| `AGENTOS_API_URL` | API address as seen from Docker (`host.docker.internal`) |
| `AGENTOS_AGENT_KEY` | Scoped read + draft key for the Jarvis agents |
| `GOOGLE_OAUTH_CLIENT_PATH` / `GOOGLE_DRIVE_TOKEN_PATH` | Optional paths for read-only Google Drive (run `make drive-auth` once) |

`SENTRY_DSN` (API) and `NEXT_PUBLIC_SENTRY_DSN` (web) enable error tracking; leave them empty to turn it off.

## Run in development

```bash
./start-all.sh   # Docker, OmniRoute, Jarvis, voice, then `make dev` (Ctrl+C stops the app)
```

Or only the app: `make dev` (Postgres + Redis, API with reload, worker, Telegram bot, voice tools server, web).
Open http://localhost:3000 and you should see **API: ok**.

## Run in production (on this Mac)

```bash
./start-all.sh prod   # same start-up, then `make prod`
```

`make prod` applies database migrations, builds the web app, then runs the API, worker, Telegram bot,
voice tools server and web (`next start`) without reload. API and web listen on 127.0.0.1 only; expose
them later through a tunnel. If any of these processes stops, it is restarted after 5 seconds.
Ctrl+C stops everything.

Start at login:

```bash
deploy/install-login.sh     # installs a LaunchAgent that runs ./start-all.sh prod
deploy/uninstall-login.sh   # removes it
```

Logs go to `~/Library/Logs/agentos.log`. The Mac must stay awake (System Settings > Battery / Energy:
prevent sleep on power adapter, or run `caffeinate -s`), otherwise the worker and calls pause.

## Tests

`make test` runs pytest and ruff for the API and lint for the web app. CI (GitHub Actions) runs the
same on every push: `ruff` + `pytest` against Postgres 16 and Redis 7, then web `pnpm lint` and `pnpm build`.

## Ports

| Service  | Host port |
|----------|-----------|
| Web      | 3000 |
| API      | 8000 |
| Voice tools (MCP) | 8787 |
| Postgres | 5434 |
| Redis    | 6380 |
| OmniRoute | 20128 |
| OpenClaw | 18789 |
| Hermes   | 8642 |

## Other targets

`make migrate`, `make down` (stops Postgres + Redis), `make drive-auth`.

## More

Rules and stack: [CLAUDE.md](CLAUDE.md). Full plan: [docs/blueprint.html](docs/blueprint.html).
Jarvis (OpenClaw, Hermes) and voice calls: [jarvis/README.md](jarvis/README.md).
