# Fleet integration

How ollafim plugs into the local AI fleet alongside `openclaw`, `hermes`,
and the rest of the Ollama ecosystem.

## Topology

```
┌──────────────────────────┐                         ┌────────────────────────┐
│  MacBook Pro (local)     │                         │  Mac Mini (remotework) │
│  100.120.67.88           │                         │  100.70.244.85         │
│                          │                         │                        │
│  ollafim :11435          │                         │  ollafim :11435        │
│   ↳ ollama localhost     │                         │   ↳ ollama at corsairai│
│   ↳ qwen2.5-coder:1.5b   │   Tailscale fallback    │   ↳ qwen3-coder:latest │
│   (small, fast tab)      │  ───────────────────►   │   (big, fleet-shared)  │
│                          │                         │                        │
│  Editor (Continue,       │                         │  openclaw gateway      │
│  Zed, twinny, …)         │                         │   :18789  (chat)       │
│   ↳ http://127:11435/v1  │                         │  hermes gateway        │
│   ↳ fallback:            │                         │   (chat / Codex)       │
│      http://100.70:11435 │                         │                        │
└──────────────────────────┘                         └────────────────────────┘
                                                                │
                                                                ▼
                                                     ┌────────────────────────┐
                                                     │  corsairai (GPU)       │
                                                     │  100.94.117.48:11434   │
                                                     │  qwen3-coder:latest    │
                                                     └────────────────────────┘
```

## Two-instance deployment

ollafim runs as **two independent launchd services**, neither talks to the other:

| Where      | Bind                  | Ollama backend            | Default model              | Use                       |
| ---------- | --------------------- | ------------------------- | -------------------------- | ------------------------- |
| MacBook    | `127.0.0.1:11435`     | `localhost:11434`         | `qwen2.5-coder:1.5b-base`  | Editor tab-complete (fast)|
| Mac Mini   | `100.70.244.85:11435` | `100.94.117.48:11434`     | `qwen3-coder:latest`       | Fleet-shared FIM endpoint |

This is intentional: short-latency local model for typing, larger fleet model for richer completions or for when the MacBook is offline / closed lid / on battery.

## Why ollafim is NOT registered as an `openclaw` provider

`openclaw` (the gateway on the mac mini) is a **chat-completions** router. Its provider model expects `/v1/chat/completions` with messages, system prompts, tool calls, and streaming chunks shaped for chat UIs.

ollafim speaks the **legacy `/v1/completions`** API — `prompt` + `suffix`, no messages, no tool calls. That format went out of OpenAI's main SDK in 2023 and is preserved only because FIM-aware editor plugins still target it. Adding ollafim as a `chat`-style provider in openclaw would do nothing useful: no openclaw agent calls `/v1/completions`, and shoehorning a chat envelope around a FIM completion strips the prefix/suffix structure that makes FIM work.

So ollafim sits **alongside** openclaw, not behind it. They share infrastructure (Tailscale, launchd, the fleet's Ollama instances) but have separate request paths:

```
Editor → ollafim → Ollama       (FIM autocomplete)
Telegram → openclaw → Ollama    (chat agents, tools)
Codex   → hermes   → OpenAI     (Codex OAuth flows)
```

Memory note: per `openclaw_minimax_hang_pattern.md`, modifying `~/.openclaw/openclaw.json` while the gateway is running can be silently reverted at the next save. Even if a future use-case justified registering ollafim there, the change procedure must be: `openclaw gateway stop` → modify with `jq` (path is `plugins.entries.<name>.config.*`, not `plugins[<name>]`) → `sleep 4` → `openclaw gateway start`.

## launchd labels (matches the fleet pattern)

| Host      | Label         | Plist                                         |
| --------- | ------------- | --------------------------------------------- |
| MacBook   | `com.ollafim` | `~/Library/LaunchAgents/com.ollafim.plist`    |
| Mac Mini  | `com.ollafim` | `/Users/remotework/Library/LaunchAgents/com.ollafim.plist` |

Status from the MacBook:

```bash
# local
launchctl list | grep ollafim
curl -s http://127.0.0.1:11435/health

# fleet
ssh macmini 'launchctl list | grep ollafim'
curl -s http://100.70.244.85:11435/health
```

Restart:

```bash
# local
launchctl unload ~/Library/LaunchAgents/com.ollafim.plist
launchctl load   -w ~/Library/LaunchAgents/com.ollafim.plist

# fleet
ssh macmini 'launchctl unload ~/Library/LaunchAgents/com.ollafim.plist; \
             launchctl load -w ~/Library/LaunchAgents/com.ollafim.plist'
```

## Editor configuration with fleet fallback

Most FIM plugins accept only one endpoint. Two patterns work:

### Pattern A — local-only with auto-recover

Use the local instance (`127.0.0.1:11435`). It's auto-restarted by launchd's `KeepAlive=true`, so a crash recovers in seconds. Simplest, recommended for daily use.

### Pattern B — point at the fleet instance

When you want completions from the bigger `qwen3-coder:latest` (e.g. closing the MacBook lid and continuing on another machine, or comparing quality), change the editor base URL to `http://100.70.244.85:11435/v1`.

### Pattern C — `socat` failover (advanced)

Run a tiny TCP forwarder locally on `:11440` that prefers `:11435` and falls through to the Tailscale instance if the local one is down. Optional; most users don't need it.

## Health probes for fleet monitoring tools

ollafim exposes the same shape across both instances:

```
GET /health        → {"ok": true, "ollama": "...", "model": "...", "version": "0.1.0"}
GET /v1/models     → OpenAI-compatible model list with fim_supported flag
```

If the fleet has a status dashboard that polls services by URL, add these two:

```
http://127.0.0.1:11435/health
http://100.70.244.85:11435/health
```

`ok: false` means the underlying Ollama is unreachable; the ollafim process itself responding implies the wrapper is fine and the issue is downstream.

## Why corsairai is the chosen Ollama backend on the mac mini

The mac mini's *own* Ollama (`100.70.244.85:11434`) has `gpt-oss:20b`, `gemma4:31b`, `qwen3:32b` — none of which use the qwen FIM token vocabulary, so they refuse `<|fim_prefix|>` correctly.

corsairai (`100.94.117.48`, AMD Strix Halo + ROCm) has `qwen3-coder:latest` which does. Its `/api/tags` responds in <10 ms over Tailscale, and it's a Windows machine with no other AI workloads competing for VRAM during typing-time.

If corsairai goes offline, the mac mini ollafim's `/health` will return `ok: false` and the editor falls back to the local instance (Pattern A). To swap to the mac mini's own Ollama temporarily:

```bash
ssh macmini 'launchctl unload ~/Library/LaunchAgents/com.ollafim.plist'
ssh macmini "sed -i '' 's|http://100.94.117.48:11434|http://localhost:11434|' \
            ~/Library/LaunchAgents/com.ollafim.plist"
# also pick a non-FIM model temporarily, e.g. qwen3:32b, and accept poor FIM quality
ssh macmini "sed -i '' 's|qwen3-coder:latest|qwen3:32b|' \
            ~/Library/LaunchAgents/com.ollafim.plist"
ssh macmini 'launchctl load -w ~/Library/LaunchAgents/com.ollafim.plist'
```

## Coexistence with hermes

`hermes` is the Codex-OAuth gateway and has nothing to do with FIM. They share the mac mini host but never talk to each other. The only operational interaction is: if the mac mini reboots, both come back via their respective `KeepAlive` launchd jobs, in no particular order.
