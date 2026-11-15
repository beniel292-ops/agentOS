#!/bin/sh
# Chief of Staff calling stack: agentring relay (Docker) + local voice server (speech-to-speech).
# Usage: ./voice.sh start|stop|status
AR=~/Documents/Archive/Desktop/agentring
V=~/Documents/Archive/Desktop/agentring-voice
case "$1" in
  start)
    (cd "$AR/deploy" && docker compose -p agentring -f docker-compose.local.yml --env-file .env.local up -d)
    # Call "brain": cloud = free Claude Haiku via OmniRoute (only text leaves the Mac; audio stays local),
    # local = Ministral 3B in Ollama (fully offline, weaker with tools). Override: VOICE_BRAIN=local ./voice.sh start
    if [ "${VOICE_BRAIN:-cloud}" = "local" ]; then
      LLM_URL=http://127.0.0.1:11434/v1; LLM_KEY=none; LLM_MODEL=ministral-voice
    else
      LLM_URL=http://127.0.0.1:20128/v1; LLM_MODEL=no-think/kr/claude-haiku-4.5
      LLM_KEY=$(grep '^OMNIROUTE_API_KEY=' "$(cd "$(dirname "$0")/../.." && pwd)/.env" | cut -d= -f2)
    fi
    if ! lsof -nP -iTCP:8765 -sTCP:LISTEN >/dev/null 2>&1; then
      (cd "$V" && VIRTUAL_ENV="$V/s2s" PATH="$V/s2s/bin:$PATH" nohup s2s/bin/speech-to-speech serve \
        --mac-optimal-settings --tts kokoro --no_enable_live_transcription --llm_backend chat-completions \
        --responses_api_base_url "$LLM_URL" --responses_api_api_key "$LLM_KEY" \
        --model_name "$LLM_MODEL" --parakeet_tdt_language en \
        --host 127.0.0.1 --port 8765 > s2s.log 2>&1 &)
      echo "voice server starting (about 1 minute)..."
    fi
    # Answer calls from the phone: voice AI with ONLY the 5 AgentOS task tools, no command agents.
    if ! pgrep -f "agentring serve" >/dev/null; then
      P="$(cd "$(dirname "$0")/../.." && pwd)/apps/api/app/cos/prompts"
      I=~/.agentring/inbound-instructions.md
      { sed "s/{now}/$(date '+%A %d %B %Y, %H:%M') (Asia\/Kolkata)/" "$P/common.md"; echo; cat "$P/inbound.md"; } > "$I"
      chmod 600 "$I"
      env -i PATH=/usr/bin:/bin HOME="$HOME" nohup "$AR/daemon/.venv/bin/agentring" serve --ai --provider local \
        --agents none --mcp http://127.0.0.1:8787/mcp \
        --tools list_tasks,add_task,update_task,schedule_callback,ask_jarvis,answer_jarvis \
        --instructions "@$I" > "$V/serve.log" 2>&1 &
      echo "answering calls from the phone (agentring serve)"
    fi ;;
  stop)
    pkill -f "agentring serve"
    pkill -f "speech-to-speech serve"
    (cd "$AR/deploy" && docker compose -p agentring -f docker-compose.local.yml --env-file .env.local stop) ;;
  status)
    IP=$(grep '^AGENTRING_HOST_IP=' "$AR/deploy/.env.local" | cut -d= -f2)
    curl -s -m 3 "http://$IP:8080/healthz" >/dev/null && echo "OK    agentring relay ($IP)" || echo "DOWN  agentring relay"
    lsof -nP -iTCP:8765 -sTCP:LISTEN >/dev/null 2>&1 && echo "OK    local voice server" || echo "DOWN  local voice server"
    pgrep -f "agentring serve" >/dev/null && echo "OK    answering phone calls" || echo "DOWN  answering phone calls"
    lsof -nP -iTCP:8787 -sTCP:LISTEN >/dev/null 2>&1 && echo "OK    task tools (MCP)" || echo "DOWN  task tools (MCP, starts with make dev)"
    "$AR/daemon/.venv/bin/agentring" status 2>/dev/null | grep -E "Devices|Push" ;;
  *) echo "Usage: ./voice.sh start|stop|status" ;;
esac
