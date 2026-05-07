#!/usr/bin/env python3
"""
ollafim — Fill-In-the-Middle (FIM) HTTP server for Ollama.

Exposes an OpenAI-compatible /v1/completions endpoint that FIM-aware editor
plugins (Continue, Cursor Tab, Cody, Tabby, twinny, llama.vscode) can use for
local autocomplete via Ollama.

Why this exists:
    Ollama supports a `suffix` field in /api/generate, but only when the model's
    Modelfile defines a Template block with <INSERT>. Most pulled models lack
    that. ollafim wraps the FIM tokens manually for each model family
    (qwen-coder, deepseek-coder, codestral, starcoder, codegemma) and uses
    raw mode, which works with any model that knows the tokens.

Zero deps beyond Python stdlib.
"""
from __future__ import annotations

import argparse
import http.server
import json
import logging
import os
import socketserver
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Iterator

__version__ = "0.1.0"

DEFAULT_OLLAMA = os.environ.get("OLLAFIM_OLLAMA", "http://localhost:11434")
DEFAULT_PORT = int(os.environ.get("OLLAFIM_PORT", "11435"))


@dataclass(frozen=True)
class FIMTemplate:
    family: str
    prefix: str
    suffix: str
    middle: str
    stops: tuple[str, ...]


# Ordered: more-specific patterns first.
TEMPLATES: tuple[tuple[str, FIMTemplate], ...] = (
    # Qwen family (qwen2.5-coder, qwen3-coder, qwen3-coder-next)
    ("qwen", FIMTemplate(
        family="qwen",
        prefix="<|fim_prefix|>",
        suffix="<|fim_suffix|>",
        middle="<|fim_middle|>",
        stops=("<|fim_pad|>", "<|endoftext|>", "<|im_end|>", "<|repo_name|>", "<|file_sep|>"),
    )),
    # DeepSeek Coder family
    ("deepseek-coder", FIMTemplate(
        family="deepseek-coder",
        prefix="<｜fim▁begin｜>",
        suffix="<｜fim▁hole｜>",
        middle="<｜fim▁end｜>",
        stops=("<|EOT|>", "<｜end▁of▁sentence｜>"),
    )),
    # Codestral / Mistral code
    ("codestral", FIMTemplate(
        family="codestral",
        prefix="[PREFIX]",
        suffix="[SUFFIX]",
        middle="",  # codestral concats: [SUFFIX]suffix[PREFIX]prefix → response
        stops=("</s>", "[INST]"),
    )),
    # CodeLlama
    ("codellama", FIMTemplate(
        family="codellama",
        prefix="<PRE> ",
        suffix=" <SUF>",
        middle=" <MID>",
        stops=("<EOT>", "</s>"),
    )),
    # StarCoder / StarCoder2
    ("starcoder", FIMTemplate(
        family="starcoder",
        prefix="<fim_prefix>",
        suffix="<fim_suffix>",
        middle="<fim_middle>",
        stops=("<|endoftext|>", "<file_sep>"),
    )),
    # CodeGemma
    ("codegemma", FIMTemplate(
        family="codegemma",
        prefix="<|fim_prefix|>",
        suffix="<|fim_suffix|>",
        middle="<|fim_middle|>",
        stops=("<|file_separator|>", "<end_of_turn>"),
    )),
)


def detect_template(model_name: str) -> FIMTemplate | None:
    name = model_name.lower()
    for needle, tpl in TEMPLATES:
        if needle in name:
            return tpl
    # Heuristic: many "coder" models in the wild use qwen-style tokens.
    if "coder" in name or "code" in name:
        return TEMPLATES[0][1]
    return None


def build_fim_prompt(prefix: str, suffix: str, tpl: FIMTemplate) -> str:
    if tpl.family == "codestral":
        # Codestral wants suffix THEN prefix.
        return f"[SUFFIX]{suffix}[PREFIX]{prefix}"
    return f"{tpl.prefix}{prefix}{tpl.suffix}{suffix}{tpl.middle}"


def http_get_json(url: str, timeout: float = 5.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.load(r)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def list_local_models(ollama_url: str) -> list[dict]:
    data = http_get_json(f"{ollama_url}/api/tags")
    if not data:
        return []
    return data.get("models", [])


def pick_default_model(ollama_url: str) -> str | None:
    models = list_local_models(ollama_url)
    candidates = []
    for m in models:
        name = m.get("name", "")
        if detect_template(name) and ("coder" in name.lower() or "code" in name.lower()):
            candidates.append((m.get("size", 0), name))
    if not candidates:
        # Fallback: any model whose family is in TEMPLATES
        for m in models:
            if detect_template(m.get("name", "")):
                return m["name"]
        return None
    # Prefer smaller by default for low autocomplete latency.
    candidates.sort()
    return candidates[0][1]


def stream_ollama(
    ollama_url: str,
    model: str,
    prompt: str,
    stops: list[str],
    max_tokens: int,
    temperature: float,
    top_p: float,
) -> Iterator[dict]:
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "raw": True,
        "stream": True,
        "options": {
            "num_predict": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "stop": stops,
        },
    }).encode()
    req = urllib.request.Request(
        f"{ollama_url}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        for line in r:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = f"ollafim/{__version__}"
    config: dict  # injected by serve()

    def log_message(self, fmt: str, *args) -> None:
        logging.info("%s - %s", self.address_string(), fmt % args)

    # ---- helpers ----
    def _send_json(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict | None:
        n = int(self.headers.get("Content-Length", "0") or 0)
        if n <= 0:
            return None
        try:
            return json.loads(self.rfile.read(n))
        except json.JSONDecodeError:
            return None

    # ---- routes ----
    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            ok = http_get_json(f"{self.config['ollama']}/api/tags") is not None
            self._send_json(200 if ok else 503, {
                "ok": ok,
                "ollama": self.config["ollama"],
                "model": self.config["model"],
                "version": __version__,
            })
        elif path == "/v1/models":
            models = list_local_models(self.config["ollama"])
            self._send_json(200, {
                "object": "list",
                "data": [{
                    "id": m["name"],
                    "object": "model",
                    "owned_by": "ollama",
                    "fim_supported": detect_template(m["name"]) is not None,
                } for m in models],
            })
        else:
            self._send_json(404, {"error": "not found", "path": path})

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path != "/v1/completions":
            self._send_json(404, {"error": "not found", "path": path})
            return

        req = self._read_json()
        if req is None:
            self._send_json(400, {"error": {"message": "invalid JSON body"}})
            return

        prompt = req.get("prompt", "")
        if isinstance(prompt, list):
            prompt = "".join(p for p in prompt if isinstance(p, str))
        suffix_text = req.get("suffix", "") or ""
        model = req.get("model") or self.config["model"]
        max_tokens = int(req.get("max_tokens") or self.config["max_tokens"])
        temperature = float(req.get("temperature", self.config["temperature"]))
        top_p = float(req.get("top_p", 0.95))
        stream = bool(req.get("stream", False))
        stop = req.get("stop") or []
        if isinstance(stop, str):
            stop = [stop]

        tpl = detect_template(model)
        if not tpl:
            self._send_json(400, {"error": {
                "message": f"no FIM template known for model '{model}'. "
                           f"Supported families: qwen-coder, deepseek-coder, "
                           f"codestral, codellama, starcoder, codegemma."
            }})
            return

        full_prompt = build_fim_prompt(prompt, suffix_text, tpl)
        all_stops = list(tpl.stops) + list(stop)
        completion_id = f"cmpl-{int(time.time() * 1000)}"
        created = int(time.time())

        if stream:
            self._stream_response(
                model, full_prompt, all_stops, max_tokens, temperature, top_p,
                completion_id, created,
            )
        else:
            self._sync_response(
                model, full_prompt, all_stops, max_tokens, temperature, top_p,
                completion_id, created,
            )

    def _sync_response(self, model, prompt, stops, max_tokens, temp, top_p,
                       cid, created) -> None:
        try:
            text = ""
            finish = "stop"
            for chunk in stream_ollama(
                self.config["ollama"], model, prompt, stops, max_tokens, temp, top_p
            ):
                text += chunk.get("response", "")
                if chunk.get("done"):
                    finish = chunk.get("done_reason") or "stop"
                    break
            self._send_json(200, {
                "id": cid,
                "object": "text_completion",
                "created": created,
                "model": model,
                "choices": [{
                    "text": text,
                    "index": 0,
                    "logprobs": None,
                    "finish_reason": finish,
                }],
            })
        except urllib.error.URLError as e:
            self._send_json(502, {"error": {"message": f"ollama unreachable: {e}"}})

    def _stream_response(self, model, prompt, stops, max_tokens, temp, top_p,
                         cid, created) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            for chunk in stream_ollama(
                self.config["ollama"], model, prompt, stops, max_tokens, temp, top_p
            ):
                payload = {
                    "id": cid,
                    "object": "text_completion",
                    "created": created,
                    "model": model,
                    "choices": [{
                        "text": chunk.get("response", ""),
                        "index": 0,
                        "logprobs": None,
                        "finish_reason": (chunk.get("done_reason") or "stop") if chunk.get("done") else None,
                    }],
                }
                self.wfile.write(b"data: " + json.dumps(payload).encode() + b"\n\n")
                self.wfile.flush()
                if chunk.get("done"):
                    break
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except urllib.error.URLError as e:
            err = json.dumps({"error": {"message": f"ollama unreachable: {e}"}}).encode()
            try:
                self.wfile.write(b"data: " + err + b"\n\n")
                self.wfile.flush()
            except OSError:
                pass


class ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(host: str, port: int, ollama: str, model: str, max_tokens: int,
          temperature: float) -> None:
    Handler.config = {
        "ollama": ollama,
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    server = ThreadingServer((host, port), Handler)
    sys.stdout.write(
        f"  ollafim v{__version__}\n"
        f"  listening on http://{host}:{port}\n"
        f"  ollama:        {ollama}\n"
        f"  default model: {model}\n"
        f"  family:        {detect_template(model).family if detect_template(model) else 'unknown'}\n"
        f"\n"
        f"  endpoints:\n"
        f"    POST /v1/completions   (OpenAI-compatible FIM)\n"
        f"    GET  /v1/models\n"
        f"    GET  /health\n"
    )
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stdout.write("\n  bye\n")


def cmd_list(args) -> int:
    models = list_local_models(args.ollama)
    if not models:
        print("no models found (is ollama running?)", file=sys.stderr)
        return 1
    width = max(len(m["name"]) for m in models)
    print(f"{'MODEL':<{width}}  FIM SUPPORT     SIZE")
    print("-" * (width + 28))
    for m in models:
        tpl = detect_template(m["name"])
        size_gb = m.get("size", 0) / 1e9
        if tpl:
            print(f"{m['name']:<{width}}  ✓ {tpl.family:<13} {size_gb:>5.1f} GB")
        else:
            print(f"{m['name']:<{width}}  ✗ —             {size_gb:>5.1f} GB")
    return 0


def cmd_test(args) -> int:
    """Quick smoke test against the running ollafim server."""
    body = json.dumps({
        "model": args.model,
        "prompt": "def fibonacci(n):\n    ",
        "suffix": "\n    return result",
        "max_tokens": 60,
        "temperature": 0,
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        f"http://{args.host}:{args.port}/v1/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.load(r)
    except urllib.error.URLError as e:
        print(f"FAIL: server unreachable at {args.host}:{args.port} ({e})", file=sys.stderr)
        return 2
    text = d.get("choices", [{}])[0].get("text", "")
    print("--- completion ---")
    print(text)
    print("------------------")
    if not text.strip():
        print("FAIL: empty completion", file=sys.stderr)
        return 1
    print("OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="ollafim",
        description="Fill-In-the-Middle HTTP server for Ollama.",
    )
    p.add_argument("--version", action="version", version=f"ollafim {__version__}")
    sub = p.add_subparsers(dest="cmd")

    serve_p = sub.add_parser("serve", help="run the HTTP server (default)")
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve_p.add_argument("--ollama", default=DEFAULT_OLLAMA, help="Ollama base URL")
    serve_p.add_argument("--model", default=None, help="default FIM model (auto-detected if omitted)")
    serve_p.add_argument("--max-tokens", type=int, default=128)
    serve_p.add_argument("--temperature", type=float, default=0.2)

    list_p = sub.add_parser("list", help="list local models with FIM support flag")
    list_p.add_argument("--ollama", default=DEFAULT_OLLAMA)

    test_p = sub.add_parser("test", help="end-to-end smoke test against running server")
    test_p.add_argument("--host", default="127.0.0.1")
    test_p.add_argument("--port", type=int, default=DEFAULT_PORT)
    test_p.add_argument("--model", default="", help="model to use (empty = server default)")
    test_p.add_argument("--ollama", default=DEFAULT_OLLAMA)

    args = p.parse_args(argv)
    cmd = args.cmd or "serve"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    if cmd == "list":
        return cmd_list(args)
    if cmd == "test":
        return cmd_test(args)

    # serve
    model = args.model or pick_default_model(args.ollama)
    if not model:
        print("error: no FIM-capable model found locally. Pull one first:", file=sys.stderr)
        print("  ollama pull qwen2.5-coder:1.5b-base", file=sys.stderr)
        return 1
    serve(args.host, args.port, args.ollama, model, args.max_tokens, args.temperature)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
