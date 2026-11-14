#!/usr/bin/env python3
"""Fixed helper for Hermes: talks ONLY to the AgentOS opportunities API with Hermes's scoped key.

  agentos_opps.py list            -> prints saved opportunity titles + urls (to avoid duplicates)
  agentos_opps.py save < opp.json -> saves one opportunity (JSON object on stdin)
"""
import json
import os
import sys
import urllib.request

BASE = os.environ["AGENTOS_API_URL"].rstrip("/") + "/v1/cos/opportunities"
FIELDS = {"title", "source", "url", "description", "relevance", "estimated_value", "deadline",
          "next_action", "verification_status"}


def call(method, url, body=None):
    req = urllib.request.Request(url, method=method, data=body, headers={
        "X-Agent-Key": os.environ["AGENTOS_KEY"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, {"detail": e.read().decode()[:300]}


if sys.argv[1:] == ["list"]:
    status, data = call("GET", BASE + "?limit=200")
    for o in data if status == 200 else []:
        print(f"- {o['title']} | {o.get('url') or ''}")
    print(f"({status})")
elif sys.argv[1:] == ["save"]:
    opp = {k: v for k, v in json.load(sys.stdin).items() if k in FIELDS}
    status, data = call("POST", BASE, json.dumps(opp).encode())
    print({201: "saved", 200: "already saved (duplicate)", 423: "Chief of Staff is stopped"}.get(
        status, f"error {status}: {data.get('detail')}"))
else:
    sys.exit("usage: agentos_opps.py list | save < opp.json")
