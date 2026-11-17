"""Checks a no-thinking Qwen model on Ollama's Anthropic-style /v1/messages (what Claude Code uses)."""
import json
import sys
import time
import urllib.request

MODEL = sys.argv[1] if len(sys.argv) > 1 else "qwen-atticus-nothink"


def post(body):
    req = urllib.request.Request("http://localhost:11434/v1/messages", json.dumps(body).encode(),
                                 {"content-type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.load(r)
    return time.time() - t, data


# 1. plain answer, two system blocks, no thinking field
secs, d = post({"model": MODEL, "max_tokens": 200,
                "system": [{"type": "text", "text": "You are Atticus."}, {"type": "text", "text": "Answer briefly."}],
                "messages": [{"role": "user", "content": "Reply with exactly: ok"}]})
print(f"1 plain: {secs:.1f}s types={[b['type'] for b in d['content']]} text={[b.get('text') for b in d['content']]}")

# 2. tool call
tools = [{"name": "open_app", "description": "Open a Mac app by name.",
          "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}]
secs, d = post({"model": MODEL, "max_tokens": 300, "tools": tools,
                "messages": [{"role": "user", "content": "Open Spotify."}]})
calls = [(b["name"], b["input"]) for b in d["content"] if b["type"] == "tool_use"]
print(f"2 tool: {secs:.1f}s types={[b['type'] for b in d['content']]} calls={calls} stop={d.get('stop_reason')}")

# 3. continue after the tool result
if calls:
    tool_id = next(b["id"] for b in d["content"] if b["type"] == "tool_use")
    secs, d2 = post({"model": MODEL, "max_tokens": 200, "tools": tools, "messages": [
        {"role": "user", "content": "Open Spotify."},
        {"role": "assistant", "content": d["content"]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "Spotify opened."}]}]})
    print(f"3 after tool: {secs:.1f}s text={[b.get('text') for b in d2['content'] if b['type'] == 'text']}")
