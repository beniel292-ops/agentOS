#!/bin/sh
# Jarvis helper. Usage: ./jarvis.sh start|stop|status|token|approve|pairs|pair-jarvis CODE|pair-fury CODE|chat "message"|logs
cd "$(dirname "$0")" || exit 1
oc() { docker exec jarvis-openclaw node openclaw.mjs "$@"; }

case "$1" in
  start)
    docker start omniroute >/dev/null 2>&1
    docker compose up -d
    echo "Waiting for agents..."
    for i in $(seq 1 40); do
      o=$(curl -s -m 2 -o /dev/null -w "%{http_code}" http://127.0.0.1:18789/healthz)
      h=$(curl -s -m 2 -o /dev/null -w "%{http_code}" http://127.0.0.1:8642/health)
      [ "$o" = 200 ] && [ "$h" = 200 ] && break; sleep 3
    done
    "$0" status ;;
  stop)
    docker compose stop ;;
  status)
    for s in "OmniRoute http://localhost:20128/" "OpenClaw http://127.0.0.1:18789/healthz" "Hermes http://127.0.0.1:8642/health" "Ollama http://localhost:11434/api/version"; do
      set -- $s; c=$(curl -s -m 3 -o /dev/null -w "%{http_code}" "$2")
      case "$c" in 200|307) echo "OK    $1" ;; *) echo "DOWN  $1 (run ./jarvis.sh start)" ;; esac
    done ;;
  token)
    grep '^OPENCLAW_GATEWAY_TOKEN=' .env | cut -d= -f2 | tr -d '\n' | pbcopy
    echo "Gateway token copied to your clipboard. Paste it into the OpenClaw page (Cmd+V)." ;;
  approve)
    oc devices list
    oc devices approve --latest ;;
  pairs)
    echo "== Jarvis (OpenClaw) =="; oc pairing list telegram
    echo "== Fury (Hermes) ==";     docker exec jarvis-hermes hermes pairing list ;;
  pair-jarvis)
    oc pairing approve telegram "$2" ;;
  pair-fury)
    docker exec jarvis-hermes hermes pairing approve telegram "$2" ;;
  chat)
    shift; oc agent --agent main -m "$*" ;;
  logs)
    docker compose logs -f --tail 50 ;;
  *)
    echo "Usage: ./jarvis.sh start|stop|status|token|approve|pairs|pair-jarvis CODE|pair-fury CODE|chat \"message\"|logs" ;;
esac
