#!/usr/bin/env python3
"""Fixed helper for Hermes: ask the owner a question by phone and wait for the answer.

  agentos_ask.py "Which city should I target first?" "context: website leads task"
  agentos_ask.py --wait <question_id>        # keep waiting if interrupted
Prints the answer, or tells you to ask on Telegram instead (quiet hours, limits, missed call).
"""
import json
import os
import sys
import urllib.request

BASE = os.environ["AGENTOS_API_URL"].rstrip("/") + "/v1/cos/questions"
HDRS = {"X-Agent-Key": os.environ["AGENTOS_KEY"], "Content-Type": "application/json"}


def call(method, url, body=None, timeout=30):
    req = urllib.request.Request(url, method=method, headers=HDRS,
                                 data=json.dumps(body).encode() if body else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, {"detail": e.read().decode()[:200]}


def wait(qid):
    for _ in range(2):  # up to ~20 minutes
        status, data = call("GET", f"{BASE}/{qid}?wait=600", timeout=620)
        if status != 200:
            return f"error {status}: {data.get('detail')}"
        if data["status"] == "answered":
            return f"ANSWER: {data['answer']}"
        if data["status"] in ("unanswered", "cancelled"):
            return "No answer by phone. Ask the same question on Telegram instead."
    return f"Still waiting (question {qid}). Run: agentos_ask.py --wait {qid}"


if len(sys.argv) == 3 and sys.argv[1] == "--wait":
    print(wait(sys.argv[2]))
elif 2 <= len(sys.argv) <= 3:
    body = {"question": " ".join(sys.argv[1].split())[:300]}
    if len(sys.argv) == 3:
        body["context"] = " ".join(sys.argv[2].split())[:1000]
    status, data = call("POST", BASE, body)
    if status in (200, 201):
        print(f"Calling the owner (question {data['id']})...", flush=True)
        print(wait(data["id"]))
    else:
        print(f"Can't call now ({status}: {data.get('detail')}). Ask on Telegram instead.")
else:
    sys.exit('usage: agentos_ask.py "<question>" ["<context>"] | --wait <id>')
