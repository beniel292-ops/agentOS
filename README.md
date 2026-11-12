# AgentOS

Multi-tenant SaaS that helps small marketing agencies reply to leads faster, with a human approving every outgoing message.

## Prerequisites

macOS/Linux with Docker, [uv](https://docs.astral.sh/uv/) (installs Python 3.12), Node 24 and pnpm.

## Run

```bash
make setup   # creates .env from .env.example, installs api + web deps
make dev     # starts Postgres + Redis (Docker), API, worker, web
```

Open http://localhost:3000 and you should see **API: ok**.

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
