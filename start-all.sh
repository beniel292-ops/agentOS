#!/bin/sh
# Start the whole Personal OS after a reboot, in order:
# Docker -> OmniRoute + Jarvis/Fury + call relay (auto-restart containers) -> AgentOS DB/Redis
# -> local voice + answering service -> AgentOS app (API, worker, bot, tools, web) in this terminal.
# Usage: ./start-all.sh        (Ctrl+C stops the AgentOS app; containers keep running)
cd "$(dirname "$0")" || exit 1

echo "1/5 Docker..."
docker info >/dev/null 2>&1 || open -a Docker
until docker info >/dev/null 2>&1; do sleep 3; done

echo "2/5 OmniRoute, Jarvis, Fury, call relay..."
docker start omniroute >/dev/null 2>&1
(cd jarvis && docker compose up -d >/dev/null 2>&1)
(cd ~/Documents/Archive/Desktop/agentring/deploy && \
  docker compose -p agentring -f docker-compose.local.yml --env-file .env.local up -d >/dev/null 2>&1)

echo "3/5 AgentOS database + Redis..."
docker compose up -d --wait >/dev/null 2>&1

IP=$(ipconfig getifaddr en0)
WANT=$(grep '^AGENTRING_HOST_IP=' ~/Documents/Archive/Desktop/agentring/deploy/.env.local | cut -d= -f2)
[ "$IP" != "$WANT" ] && echo "WARNING: Wi-Fi IP is $IP but the relay expects $WANT — calls won't reach the phone. Ask Claude Code to update it."

echo "4/5 Local voice + answering phone calls (MCP tools come up with the app)..."
until curl -s -m 3 "http://$WANT:8080/healthz" >/dev/null; do sleep 2; done  # the answering service needs the relay up
./jarvis/cos/voice.sh start >/dev/null 2>&1
# The answering service needs the task tools (started by make dev): start it again once they answer
# (any HTTP code means up; the real path is secret). voice.sh start skips parts already running.
(until [ "$(curl -s -o /dev/null -m 3 -w '%{http_code}' http://127.0.0.1:8787/)" != "000" ]; do sleep 3; done
 sleep 5; ./jarvis/cos/voice.sh start >/dev/null 2>&1) &

echo "5/5 AgentOS app (API, worker, Telegram bot, task tools, web)..."
echo "    Status in another terminal: ./jarvis/jarvis.sh status && ./jarvis/cos/voice.sh status"
exec make dev
