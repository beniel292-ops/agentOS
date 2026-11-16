# Phase 9 — Security testing (2026-11-16)

Three passes: an adversarial code review, 732 automated security tests (`apps/api/tests/test_security_*.py`),
and machine checks. Every finding below was fixed and is covered by a test. Suite: 1,058 tests passing.

## Found and fixed

| # | Severity | Problem | Fix |
|---|---|---|---|
| 1 | High | Anyone with `tasks:write` (a member, or a tricked Jarvis) could create a task marked "owner confirmed by voice" and start a Jarvis job | Only the voice key may start a hand-off (403 otherwise); hand-off tasks can't be retitled; hand-off cap counted under a lock; STOP re-checked just before sending; tolerant fence regex |
| 2 | High | The Jarvis/Fury containers could reach Redis (no password; jobs are unpickled → code execution on the Mac) and the voice tools server (no auth) | Redis `requirepass`; voice tools served only at a secret path (`/mcp/<VOICE_MCP_TOKEN>`), access log off |
| 3 | Medium | Missed calls didn't count toward the daily cap or the gap, so an agent could keep ringing | Missed calls count; never-dialled calls are `CANCELLED` |
| 4 | Medium | Call transcripts readable with `tasks:read` (agents, members) | New owner-only scope `checkins:read` |
| 5 | Medium | An agent could make the voice ask for a code/password and read the answer | Hard rule in the voice prompt; real asker named; Telegram FYI when a question is queued; agents read only their own questions; agents can't overwrite each other's call notes |
| 6 | Medium | Task titles reached the voice model as possible instructions | Titles labelled as data; prompt rule |
| 7 | Low | Drive reads ignored STOP, no rate limit | 423 while stopped; 120 requests/hour; strict file-id match |
| 8 | Low | Agents could mark their own opportunities verified; link host hidden | Verification is owner-only; Telegram shows the link's host |
| 9 | Low | Long approval drafts could be cut off in Telegram while Approve sent the full text; lead names could fake header lines | Long messages split, nothing dropped; names collapsed to one line |
| 10 | Low | Call lifecycle not audited; audit table could be truncated | `call_placed/missed/cancelled/completed` audit events; `BEFORE TRUNCATE` trigger |
| 11 | Low | Voice key held an unused `memory:write` scope | Re-issued without it |

## Checked and solid

Owner-only routes (keys, resume, settings) unreachable by agents; every query tenant-filtered; Telegram checks
the sender, chat and button data; keys are 256-bit, stored as SHA-256; no secrets in logs or git history;
agentring runs as an argument list (no shell) with `--agents none` and a tools allowlist; prompt fences strip
injected tags; worker re-checks STOP, quiet hours, cap and gap under a lock.

## Machine checks

All app ports bound to 127.0.0.1; call relay on the Wi-Fi IP only (needed by the phone); secret files mode 600;
no container privileged, on the host network or with the Docker socket; `~/.agentring/config.yaml` and
agentring `deploy/secrets/` tightened to 600/700.

## Known limits (accepted)

- The voice tools token is visible in `agentring serve`'s process arguments to local users of this Mac.
- The call gap is measured from the scheduled time, not the dial time.
- STOP blocks new actions but does not hang up a call already in progress.
- Unicode look-alike tags (e.g. full-width `＜/request＞`) are not stripped.
- Exposed secrets from earlier chats (Neon, Clerk, bot tokens, Firebase key, Google client secret) still need rotating.
