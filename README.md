# ollafim

Fill-In-the-Middle (FIM) HTTP server for Ollama. It accepts source-code prefixes and suffixes at `/v1/completions`. Editor integration must be verified against the specific client and version; an OpenAI-compatible chat setting is not sufficient.

**The missing piece**: Ollama supports a `suffix` field in `/api/generate`, but only when the pulled model's Modelfile defines a `Template` block with `<INSERT>`. Most community models don't. ollafim wraps the FIM tokens correctly per model family (`qwen-coder`, `deepseek-coder`, `codestral`, `codellama`, `starcoder`, `codegemma`) and uses raw mode — works with any model that knows the tokens.

Zero dependencies beyond Python stdlib.

## Quickstart

```bash
# 1. Have Ollama running with at least one FIM-capable model
ollama pull qwen2.5-coder:1.5b-base

# 2. List which of your local models are FIM-capable
python3 ollafim.py list

# 3. Start the server (auto-picks the smallest code model)
python3 ollafim.py serve

# 4. Smoke-test in another terminal
python3 ollafim.py test
```

Output:

```
  ollafim v0.1.0
  listening on http://127.0.0.1:11435
  ollama:        http://localhost:11434
  default model: qwen2.5-coder:1.5b-base
  family:        qwen
```

## Endpoints

| Method | Path               | Notes                                                           |
| ------ | ------------------ | --------------------------------------------------------------- |
| POST   | `/v1/completions`  | OpenAI-legacy completions API. Accepts `prompt` + `suffix`.     |
| GET    | `/v1/models`       | Lists local Ollama models with `fim_supported` flag.            |
| GET    | `/health`          | Liveness check.                                                 |

### `POST /v1/completions`

Request body (OpenAI-compatible):

```json
{
  "model": "qwen2.5-coder:1.5b-base",
  "prompt": "def fibonacci(n):\n    ",
  "suffix": "\n    return result",
  "max_tokens": 64,
  "temperature": 0.2,
  "stream": false,
  "stop": ["\n\n"]
}
```

Sync response:

```json
{
  "id": "cmpl-...",
  "object": "text_completion",
  "model": "qwen2.5-coder:1.5b-base",
  "choices": [{
    "text": "result = []\n    a, b = 0, 1\n    while n > 0: ...",
    "index": 0,
    "finish_reason": "stop"
  }]
}
```

Streaming: `Content-Type: text/event-stream` with `data: {...}` chunks and a final `data: [DONE]`.

## Editor integration

ollafim accepts `prompt` (source before the cursor) and `suffix` (source after it) at `POST /v1/completions`. It does not implement chat completions. Confirm that the client's autocomplete path uses this request format before changing any provider settings.

| Client | What to verify |
| --- | --- |
| Cursor | Cursor Tab uses its specialized built-in models. Overriding the OpenAI chat base URL does not connect Tab to ollafim, and ollafim cannot serve Cursor's chat requests. Keep chat-provider settings separate. [Cursor API keys](https://docs.cursor.com/settings/api-keys). |
| Zed | Edit prediction has its own provider configuration. A `language_models.openai` entry configures other AI features, not proof of autocomplete integration. Consult [Zed edit prediction](https://zed.dev/docs/ai/edit-prediction) and validate its request payload before using this wrapper. |
| Continue, twinny or another FIM extension | Confirm the installed version, autocomplete provider, endpoint and transmitted prefix/suffix. No version-specific copy-and-paste configuration is validated by this repository's tests. |

If a client already inserts FIM tokens into `prompt`, wrapping that prompt again can produce an incorrect context. Check one synthetic request and the actual inserted result. A successful `/health` request or a generated string does not prove editor integration, syntax validity or useful completion quality.

Use a locally installed FIM-trained model. An unknown model name is not evidence of FIM capability merely because template detection returns a token family. See [INTEGRATION.md](INTEGRATION.md) for service and model verification boundaries.

## CLI reference

```
python3 ollafim.py serve [--port 11435] [--ollama URL] [--model NAME]
                         [--max-tokens 128] [--temperature 0.2]
python3 ollafim.py list   [--ollama URL]
python3 ollafim.py test   [--port 11435] [--model NAME]
```

Environment variables:

| Var               | Default                  |
| ----------------- | ------------------------ |
| `OLLAFIM_PORT`    | `11435`                  |
| `OLLAFIM_OLLAMA`  | `http://localhost:11434` |

## Supported model families

| Family           | Trigger keywords in name | FIM tokens                                                          |
| ---------------- | ------------------------ | ------------------------------------------------------------------- |
| `qwen`           | `qwen`, `*coder*` (fallback) | `<\|fim_prefix\|>` `<\|fim_suffix\|>` `<\|fim_middle\|>`           |
| `deepseek-coder` | `deepseek-coder`         | `<｜fim▁begin｜>` `<｜fim▁hole｜>` `<｜fim▁end｜>`                |
| `codestral`      | `codestral`              | `[SUFFIX]…[PREFIX]…` (suffix-first ordering)                        |
| `codellama`      | `codellama`              | `<PRE>` `<SUF>` `<MID>`                                             |
| `starcoder`      | `starcoder`              | `<fim_prefix>` `<fim_suffix>` `<fim_middle>`                        |
| `codegemma`      | `codegemma`              | `<\|fim_prefix\|>` `<\|fim_suffix\|>` `<\|fim_middle\|>`            |

Adding a new family is a single tuple entry in `TEMPLATES`.

## Why not just use Ollama's `suffix` field?

Ollama’s `/api/generate` accepts a `suffix` field, but it relies on the model's Modelfile having a `Template` block with `<INSERT>`. Try it on a typical pulled model:

```bash
$ curl -s http://localhost:11434/api/generate -d '{
    "model": "qwen3-coder-next:q4_K_M",
    "prompt": "def fib(n):\n  ",
    "suffix": "\n  return r"
  }'
{"error": "registry.ollama.ai/library/qwen3-coder-next:q4_K_M does not support insert"}
```

ollafim sidesteps that by injecting the FIM tokens in raw mode (`raw: true`), which the model handles natively.

## Roadmap

- [ ] `/v1/chat/completions` shim that converts chat → FIM context
- [ ] Auto-debounce identical prompts (cache last completion for N ms)
- [ ] Per-language temperature/max-tokens profiles
- [ ] `--watch` mode that hot-reloads templates from a YAML file
- [ ] Detect and route `tab-autocomplete-model` headers to switch model on the fly
- [ ] Token-stream metrics endpoint (TTFT, tokens/sec)

## License

MIT
