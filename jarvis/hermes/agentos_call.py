#!/usr/bin/env python3
"""Fixed helper for Hermes: asks AgentOS to ring the owner's phone (it never dials itself).

  agentos_call.py "Your 5 Chennai leads are ready in Telegram."
AgentOS applies quiet hours, call limits and the STOP switch.
"""
import json
import os
import sys
import urllib.request

if len(sys.argv) != 2 or not sys.argv[1].strip():
    sys.exit('usage: agentos_call.py "<one-sentence reason>"')
reason = " ".join(sys.argv[1].split())[:300]
req = urllib.request.Request(
    os.environ["AGENTOS_API_URL"].rstrip("/") + "/v1/cos/calls/now", method="POST",
    data=json.dumps({"reason": reason}).encode(),
    headers={"X-Agent-Key": os.environ["AGENTOS_KEY"], "Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        print("Call requested: the phone will ring within a minute." if r.status in (200, 201) else r.status)
except urllib.error.HTTPError as e:
    detail = e.read().decode()[:200]
    print({409: "Not calling now (quiet hours or too soon): ", 429: "Daily call limit reached: ",
           423: "Chief of Staff is stopped: "}.get(e.code, f"error {e.code}: ") + detail)
