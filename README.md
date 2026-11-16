# AgentOS

Multi-tenant SaaS that helps small marketing agencies reply to leads faster, with a human approving every outgoing message.

## Prerequisites

macOS/Linux with Docker, [uv](https://docs.astral.sh/uv/) (installs Python 3.12), Node 24 and pnpm.

## Run

```bash
make setup   # creates .env from .env.example, installs api + web deps
make dev     # starts Postgres + Redis (Docker), API, worker, Telegram bot, voice MCP server, web
```

Open http://localhost:3000 and you should see **API: ok**.

The Telegram approvals bot (`cd apps/api && uv run python -m app.telegram.bot`) also starts with `make dev`.

Voice calls: the worker places scheduled calls (morning, evening, callbacks, "call me now") through
agentring. The voice AI's 5 task tools are served by `cd apps/api && uv run python -m app.mcp_server`
(127.0.0.1:8787, also in `make dev`). It needs `VOICE_AGENT_KEY` and `VOICE_MCP_TOKEN` (the endpoint is `/mcp/<token>`; `make setup` generates the token; see `.env.example`).
Set `TELEGRAM_APPROVALS_BOT_TOKEN`, `TELEGRAM_OWNER_USER_ID` and `TELEGRAM_OWNER_TENANT_ID` in `.env` to
turn it on; with an empty token it idles. Commands: `/status`, `/tasks`, `/stop`, `/resume`, `/help`.

Other targets: `make test`, `make migrate`, `make down`.

## Ports

| Service  | Host port |
|----------|-----------|
| Web      | 3000 |
| API      | 8000 |
| Postgres | 5434 |
| Redis    | 6380 |

## More

Rules and stack: [CLAUDE.md](CLAUDE.md). Full plan: [docs/blueprint.html](docs/blueprint.html).
