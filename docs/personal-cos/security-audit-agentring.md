# agentring security audit (read-only)

- Repo: github.com/beniel292-ops/agentring (fork of mutonby/agentring), commit f4b7ab8, identical to upstream (0 fork-only commits).
- Method: manual read of first-party source (daemon, relay, Android manifests/Gradle/Kotlin grep, deploy, workflows, plugins, skills), lockfiles parsed, git history scanned for secrets and large blobs. Nothing from the repo was installed, built or run.
- Date: 2026-11-14.

## Verdict

No malware, backdoor, obfuscation, telemetry or credential theft was found in a manual read of the first-party source. **This is not a guarantee the project is malware-free.** Not covered: transitive dependency code (e.g. the `livekit` Python package ships a native binary), Docker base images, the LiveKit/Caddy images, the Play Store APK, the PyPI wheel, and anything that needs running code to observe.

The main risk is design, not malware: in its default "consult agents" setup, the voice AI forwards free natural-language tasks to full-power local agents (OpenClaw, Hermes, `claude -p`). Commands are argv lists (no shell injection), but whoever controls the call or the text the model reads can direct those agents.

## Findings

| Sev | Location | Finding | Mitigation (our plan) |
|---|---|---|---|
| High | daemon `voice/agents.py:211-259`, `voice/quick.py:95-99`, `cli.py:59-71,806-810` | `consult_agent` sends any task text to every agent in `agents.yaml`; `serve --ai` auto-loads it; inbound calls have no approval gate. Voice isn't authenticated. | **No command agents.** Run `serve --ai --agents none`; the voice AI only gets our AgentOS MCP server with an explicit `tools:` allowlist. |
| High | `connect.py:283-300,345-366` | `agentring connect openclaw/hermes` adds them as consult agents by default. | Never run `connect` with consult; we don't use `connect` at all (our agents run in Docker). |
| Medium | relay `auth.ts:44-46`, `context.ts:53-60`, `routes/pairing.ts:77-90` | One unscoped `ak_` key can also pair new devices. | Only the AgentOS worker holds `ak_` (to place scheduled calls). OpenClaw/Hermes never get it. Regular `agentring devices` review; lock pairing after your phone. |
| Medium | `voice/gemini.py:85-87`, `voice/openai_realtime.py:94-98` | Untrusted agent/tool output reaches Gemini/OpenAI as a plain user turn (only Phonic fences it). | Our MCP tools return structured data only; patch these backends to fence untrusted text before enabling Gemini. |
| Medium | `mcp_server.py:254-265`, `voice/quick.py:26-30` | Calls started over MCP put the caller's `context` into the system prompt and load `agents.yaml`. | Calls are started by the AgentOS worker with fixed context text; separate `AGENTRING_HOME` for `agentring mcp`; no `agents.yaml` anywhere. |
| Medium | `agents.py:75` | Child processes inherit the full environment. | Not applicable once there are no command agents; daemon started from a minimal env. |
| Medium | relay `auth.ts:28-36` | Device JWTs never expire. | Revoke lost devices; optional patch to add expiry. |
| Medium | `.github/workflows/android.yml:110-131`, `app/debug.keystore` | Preview APK signed with the public debug key. | Don't sideload it: use the Play build or our own signed build. |
| Low | `agents.py:56-70` | `{task}` substituted unescaped inside longer args. | Not applicable (no command agents). |
| Low | `voice/tools.py:115,160-175` | MCP servers without `tools:` expose every tool. | Always pass an explicit tools allowlist. |
| Low | relay `main.ts:29-32` | Generated API key printed to stdout/docker logs. | Set `AGENTRING_API_KEY` explicitly; purge logs. |
| Low | `docker-compose.local.yml`, `network_security_config.xml` | Local mode is cleartext HTTP on the LAN. | Use Tailscale (WireGuard-encrypted) or TLS. |
| Low | `voice/providers.py:105-107` | Local provider accepts `ws://` to any host. | Keep it at 127.0.0.1. |
| Low | `agents.py:262,269`, `session.py:345` | INFO logs contain task/answer snippets. | Logs kept local, rotated, mode 600. |
| Low | `skills/agentring/references/setup.md:62` | Install docs use unpinned `git+` installs. | Install from the audited clone with `uv sync --frozen` (commit f4b7ab8). |
| Info | — | Workflows SHA-pinned, no `pull_request_target`; Gradle wrapper checksum matches official 8.14.6; JitPack used only for `audioswitch`; relay image non-root, read-only FS, caps dropped, base images pinned by digest; Android `allowBackup=false`, Keystore-backed secrets; no analytics/ads/crash SDKs; no contacts/SMS/location/accessibility permissions. | Disable publish/release workflows on the fork. |

## Network endpoints (from source)

Your relay (all API/WS/pairing) · LiveKit media (TCP 7881, UDP 50000-50100) · Let's Encrypt (only with the TLS compose) · `fcm.googleapis.com` / `oauth2.googleapis.com` via **your own** Firebase project (optional; without it calls ring only while the app is open) · voice AI provider of your choice (`--provider local` avoids all cloud voice calls) · `agentring.io` appears only in UI buttons, the plugin's `.mcp.json` and help text — **no automatic phone-home, telemetry or update checks**.

## Data sent to a cloud voice provider

System instructions, full call audio (and transcription), tool schemas and tool results. With `--provider local`, the voice model stays on this Mac.

## Android permissions

INTERNET, POST_NOTIFICATIONS, RECORD_AUDIO, MODIFY_AUDIO_SETTINGS, FOREGROUND_SERVICE (+ PHONE_CALL, MICROPHONE), MANAGE_OWN_CALLS, USE_FULL_SCREEN_INTENT, WAKE_LOCK, VIBRATE, ACCESS_NOTIFICATION_POLICY, REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, BLUETOOTH_CONNECT — all explained by calling. No contacts, SMS, phone state, location, accessibility, package-install permissions.
