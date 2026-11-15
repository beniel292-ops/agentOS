# Jarvis MVP

A personal AI team running free on this Mac.

```
You ──► OpenClaw (Jarvis: chat, tools, memory) ──delegates──► Hermes (background jobs, cron)
              │                                                    │
              └──────────────► OmniRoute (free models) ◄───────────┘
Claude Code builds AgentOS separately.
```

| Part | Address | Role |
|---|---|---|
| OmniRoute | http://localhost:20128 | Free model gateway (v3.8.53, data in Docker volume `omniroute-data`). Main model `agy/gemini-3.8-flash-high`; backups `kr/claude-haiku-4.5`, `agy/gemini-3.8-flash-medium`, `agy/claude-sonnet-4-6` |
| OpenClaw | http://127.0.0.1:18789 | Jarvis: you chat with it |
| Hermes | http://127.0.0.1:8642 (API only) | Background worker OpenClaw hands long jobs to |
| Ollama (local AI) | http://localhost:11434 | `qwen-agent` = Qwen3.5 9B, 4-bit (Unsloth GGUF), 64k context, ~20 tokens/s; last fallback for Hermes and OpenClaw. `ministral-voice` = Ministral 3B for the phone voice |

## Every time you turn on the laptop

**One command starts everything** (Docker, OmniRoute, Jarvis, Fury, the call relay, the AgentOS database, the local voice, the answering service and the AgentOS app):

```bash
cd ~/Documents/Archive/Desktop/AgentOS && ./start-all.sh
```

Leave that terminal open (Ctrl+C stops the AgentOS app). The older step-by-step way is below.

1. Open **Docker Desktop** and wait until it says "Engine running".
2. In Terminal:
   ```bash
   cd ~/Documents/Archive/Desktop/AgentOS/jarvis
   ./jarvis.sh start
   ```
   You should see three `OK` lines.

## Chat with Jarvis in the browser (first time)

1. `./jarvis.sh token` copies the gateway token to your clipboard (it is never printed).
2. Open http://127.0.0.1:18789 in Chrome or Safari.
3. Click the **Gateway secret** box, press **Cmd+V**, click **Connect**.
4. If it says *pairing required*, run `./jarvis.sh approve`, then click **Connect** again.
5. You're in the chat. First message: Jarvis asks what to call it, so answer "Jarvis" and tell it about yourself.

The browser remembers the token; next time just open the page.

## Chat from Terminal (no browser)

```bash
./jarvis.sh chat "What can you do?"
./jarvis.sh chat "Ask Hermes to research the top 5 lead-gen tools for small agencies and summarise them"
```

## All helper commands

| Command | What it does |
|---|---|
| `./jarvis.sh start` | Start OmniRoute, OpenClaw and Hermes, then show status |
| `./jarvis.sh status` | Is each part up? |
| `./jarvis.sh token` | Copy the OpenClaw gateway token to the clipboard |
| `./jarvis.sh approve` | Approve a browser waiting for pairing |
| `./jarvis.sh chat "..."` | Send Jarvis a message from Terminal |
| `./jarvis.sh logs` | Live logs (Ctrl+C to exit) |
| `./jarvis.sh stop` | Stop the agents |

## Files

- `.env`: secrets (OmniRoute key, gateway tokens, Telegram tokens). Git-ignored.
- `data/openclaw/`: OpenClaw config (`openclaw.json`) and workspace (`AGENTS.md`, `SOUL.md`, memory).
- Hermes data lives in the Docker volume `jarvis-hermes-data` (a macOS folder broke Hermes after a reboot). Edit its config with:
  `docker exec -it jarvis-hermes sh -c 'vi /opt/data/config.yaml'` then `docker restart jarvis-hermes`.
  `data/hermes/` is only the old copy.

## If something breaks

| Symptom | Fix |
|---|---|
| `DOWN` in status | `./jarvis.sh start` |
| "Maximum combo retry limit reached" | A free provider is busy; wait a minute and retry. Backups kick in automatically |
| OpenClaw page asks for the token again | `./jarvis.sh token`, paste, Connect |
| Anything else | `./jarvis.sh logs` and show Claude Code the error |

OmniRoute's logins live in the Docker volume `omniroute-data` (backup: `~/omniroute-backup-2026-11-12`). The old 3.8.50 container is kept stopped as `omniroute-old-3.8.50`; delete it once you're happy.

Free models change over time. If replies keep failing, ask Claude Code to re-test the free models and switch.

## Local model (Qwen3.5 9B)

- Hermes (Fury) runs on free cloud models (Claude Sonnet 4.5 via Kiro → Haiku 4.5 → Gemini) with the local model as the last fallback. The local model overflowed on long Telegram chats (64k limit) and was unreliable with tools, so it's no longer first.
- Scheduled Hermes jobs use the named provider `omniroute` (`--provider omniroute --model kr/claude-sonnet-4.5`). `--provider custom` would send them to the main model's address instead.
- Hermes cron jobs that should message you must use `--deliver telegram:8371851507` (no default Telegram chat is set).
- OpenClaw (Jarvis) stays on the faster cloud models; the local model is its last fallback (a simple reply takes about 80 s locally).
- Thinking is turned off for speed (`agent.reasoning_effort: none` in Hermes).
- Recreate the profile: `ollama create qwen-agent -f Modelfile` with `FROM hf.co/unsloth/Qwen3.5-9B-GGUF:Q4_K_M` and `PARAMETER num_ctx 65536` (Hermes needs at least 64k).
- Ollama must be running (the Ollama app in the menu bar).

## Telegram

| Bot | Agent | Use it for |
|---|---|---|
| @BeniJarvisBot | OpenClaw (Jarvis) | Everyday chat; "ask Fury to…" hands jobs to Hermes |
| @BenifuryBot | Hermes (Fury) | Talk to the worker directly; scheduled reports arrive here |

Both bots ignore strangers until you approve them (pairing):

1. Send `hi` to each bot in Telegram. Each replies with a pairing code.
2. Approve them:
   ```bash
   ./jarvis.sh pairs                 # shows pending requests and codes
   ./jarvis.sh pair-jarvis CODE      # approve yourself on Jarvis
   ./jarvis.sh pair-fury CODE        # approve yourself on Fury
   ```
3. Message again; they now answer.

Tokens live in `.env` (Jarvis) and Hermes's volume `.env` (Fury). If a token leaks, send `/revoke` to @BotFather, then ask Claude Code to swap in the new one.

## Google Drive (read-only)

Jarvis and Fury can search your Drive, read Docs/Sheets/Slides as text, and list files you changed recently (to spot unfinished work). They can't edit, share or delete anything; every read is audited.

- Setup (done once): Google Cloud OAuth client "Desktop app" saved as `secrets/google-oauth-client.json`, then `make drive-auth` (signs you in; the token goes to `secrets/google-drive-token.json`, mode 600, git-ignored).
- Re-run `make drive-auth` if Google asks you to sign in again (Testing-mode apps can expire sign-ins after 7 days).
- API: `GET /v1/cos/drive/search|recent|files/<id>/text` (scope `drive:read`). Fury's helper: `/opt/data/bin/agentos_drive.py` (copy in `jarvis/hermes/`).
