#!/bin/sh
# Chief of Staff calling stack: agentring relay (Docker) + local voice server (speech-to-speech).
# Usage: ./voice.sh start|stop|status
AR=~/Documents/Archive/Desktop/agentring
V=~/Documents/Archive/Desktop/agentring-voice
case "$1" in
  start)
    (cd "$AR/deploy" && docker compose -p agentring -f docker-compose.local.yml --env-file .env.local up -d)
    if ! lsof -nP -iTCP:8765 -sTCP:LISTEN >/dev/null 2>&1; then
      (cd "$V" && VIRTUAL_ENV="$V/s2s" PATH="$V/s2s/bin:$PATH" nohup s2s/bin/speech-to-speech serve \
        --mac-optimal-settings --tts kokoro --llm_backend chat-completions \
        --responses_api_base_url http://127.0.0.1:11434/v1 --responses_api_api_key none \
        --model_name ministral-voice --parakeet_tdt_language en \
        --host 127.0.0.1 --port 8765 > s2s.log 2>&1 &)
      echo "voice server starting (about 1 minute)..."
    fi ;;
  stop)
    pkill -f "speech-to-speech serve"
    (cd "$AR/deploy" && docker compose -p agentring -f docker-compose.local.yml --env-file .env.local stop) ;;
  status)
    IP=$(grep '^AGENTRING_HOST_IP=' "$AR/deploy/.env.local" | cut -d= -f2)
    curl -s -m 3 "http://$IP:8080/healthz" >/dev/null && echo "OK    agentring relay ($IP)" || echo "DOWN  agentring relay"
    lsof -nP -iTCP:8765 -sTCP:LISTEN >/dev/null 2>&1 && echo "OK    local voice server" || echo "DOWN  local voice server"
    "$AR/daemon/.venv/bin/agentring" status 2>/dev/null | grep -E "Devices|Push" ;;
  *) echo "Usage: ./voice.sh start|stop|status" ;;
esac
