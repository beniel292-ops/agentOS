#!/bin/sh
# Fill in secrets that .env needs and that are not ours to ask for: REDIS_PASSWORD and
# VOICE_MCP_TOKEN. Only missing values are generated; nothing is printed.
cd "$(dirname "$0")/.." || exit 1
[ -f .env ] || exit 0

ensure() { # ensure KEY -> writes KEY=<random hex> unless it already has a value
  grep -q "^$1=." .env && return 1
  sed -i.bak "/^$1=/d" .env && rm -f .env.bak
  printf '%s=%s\n' "$1" "$(openssl rand -hex 24)" >> .env
}

ensure VOICE_MCP_TOKEN
if ensure REDIS_PASSWORD; then
  pw=$(grep '^REDIS_PASSWORD=' .env | cut -d= -f2)
  # point REDIS_URL at the password (a placeholder or a password-less URL only)
  if ! grep -q '^REDIS_URL=redis://:[^@]*@' .env || grep -q '^REDIS_URL=.*<REDIS_PASSWORD>' .env; then
    sed -i.bak "/^REDIS_URL=/d" .env && rm -f .env.bak
    printf 'REDIS_URL=redis://:%s@localhost:6380/0\n' "$pw" >> .env
  fi
fi
