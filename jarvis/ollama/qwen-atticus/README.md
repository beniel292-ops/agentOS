# qwen-atticus

Local Qwen3.5 9B for Atticus (and anything else that reaches Ollama through Claude Code's Anthropic-style API).
Same weights as `qwen-agent`, so no extra download or disk.

Why a separate model: `qwen-agent`'s built-in template rejects Claude Code's requests ("System message must be at the
beginning"), and Ollama's own Qwen3.5 renderer thinks by default (17-22 s per answer) because Claude Code never sends
"thinking: disabled". `template.gotmpl` merges all system messages and always starts the answer with an empty think
block, like Qwen3.5's own template does by default. `PARSER qwen3-coder` reads the same XML tool calls without
expecting a thinking section.

Rebuild (after an Ollama reset):

    python3 build-model.py && ollama create qwen-atticus -f Modelfile.nothink
    python3 check.py qwen-atticus   # plain answer, tool call, answer after the tool: each about 2 s warm

Atticus uses it via Settings -> Intelligence -> Custom server (`http://localhost:11434`, model `qwen-atticus`).
Known limit (2026-11-17): dictation cleanup copies text that is already before the cursor; fix later in the Atticus fork.
