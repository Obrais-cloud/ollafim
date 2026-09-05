# Fleet integration

ollafim is a completion adapter alongside the agent systems. Its implemented request path is:

```text
Verified editor FIM client -> ollafim /v1/completions -> configured Ollama /api/generate
```

OpenClaw and Hermes perform their own agent work. A shared host or Ollama backend does not create an integration between those agents and ollafim. Do not register this completion-only endpoint as a chat provider without an explicitly implemented and tested adapter.

## Verify each instance independently

A launchd file describes intended startup settings. It does not establish that the process is running, its backend is reachable or its selected model is installed. Before using a local or fleet instance:

1. Match the running process to the actual listening address and port.
2. Read its configured Ollama endpoint and default model without exposing credentials.
3. Query that Ollama endpoint from the same host as the ollafim process. Connectivity from a different computer is insufficient.
4. Check `/api/tags` for the selected model and verify that the model supports the required FIM format. Do not substitute a regular chat model simply to obtain a green status.
5. Make one bounded synthetic completion through the intended client; inspect the request format, insertion, language compatibility and behavior at the token limit.

The previous topology was an installation snapshot. It is not a reliable current inventory, a performance measurement, or evidence of an active editor. Keep dated runtime observations in the operator's private fleet records instead of treating this document as live state.

## Health endpoint scope

```text
GET /health
GET /v1/models
POST /v1/completions
```

`/health` returns HTTP 200 only when the configured Ollama supplies a valid model catalog containing the selected default model. An omitted tag is treated as `:latest`; different tags, namespaces and aliases are not substituted. HTTP 503 distinguishes `backend_unavailable`, `invalid_catalog` and `model_missing` through the `reason` field. The existing `ok`, `ollama`, `model` and `version` fields remain, with `backend_reachable`, `catalog_valid` and `model_available` added. Availability is null when the catalog cannot establish it.

This is a metadata readiness check. It does **not** load a model, prove that it accepts the FIM template, produce code or verify an editor. A timeout is a failed observation, not proof that the adapter process exited. A backend can be reachable while its selected model is unavailable; this must not report healthy.

`/v1/models` lists models reported by that backend. Template detection is heuristic; its `fim_supported` flag is not a model-quality test. Metadata requests do not load models. A completion request does, and normal Ollama retention can leave the model loaded after the response.

## Local and fleet routes

Independent instances have independent backends, models and failure states. They do not forward requests to each other. Most clients select one configured endpoint. There is no automatic failover implemented by ollafim.

A manual alternate route should be offered only after its backend, selected model and client behavior have been verified from the relevant machines. Do not describe a TCP forwarder as failover unless its health selection, handling of in-flight requests and recovery have actually been implemented and tested. A sleeping operator laptop cannot be assumed to provide a fallback endpoint.

Keep serving addresses private to the intended clients. Changing the bind address or opening firewall access is separate from configuring an editor, and is not required to diagnose a model or protocol mismatch.

## Editor configuration

See the compatibility boundaries in [README.md](README.md#editor-integration). In particular, Cursor's custom chat-provider settings do not replace Cursor Tab, and Zed's general language-model provider settings are separate from edit prediction. Avoid changing an existing chat endpoint to ollafim.

Before adding an extension or another provider, identify the editor the operator actually uses and preserve its existing configuration. An installed extension is not proof that it consumes ollafim. Validate the editor request and accepted insertion separately from direct HTTP tests.

## Operations

Preserve active work, current launchd settings and logs when changing an instance. Do not restart a process merely because its backend is unavailable or an old exit code is nonzero. Resolve the failing dependency first. A service restart is needed only when a justified code or startup change must take effect and active requests have been accounted for.

Do not download a large model or replace a fast model by default. Compare a fixed set of representative completion cases, distinguish cold loading from warm requests, record syntax and unwanted side effects, and retain failed cases. A larger model is not automatically a better autocomplete model.
