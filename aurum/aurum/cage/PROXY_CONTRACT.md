# Cage broker ↔ Hermes gateway — proxy wire-contract (pinned)

Pinned from `gateway/run.py` `_run_agent` → `_run_agent_via_proxy` (and `_get_proxy_url`).
The cage broker MUST implement exactly this so Hermes' gateway — unmodified — delegates every
agent turn (chat + headless) to it. The gateway is a thin relay; the broker runs the turn in a
per-request `--rm` cage and streams the result back.

## How the gateway selects proxy mode

`_get_proxy_url()` returns, in order:
1. env `GATEWAY_PROXY_URL` (trailing `/` stripped), else
2. `gateway.proxy_url` in `config.yaml`, else `None`.

If set, **every** turn routes through `_run_agent_via_proxy` instead of the in-process `AIAgent`.
So flipping in-process(dev) ↔ caged(prod) is one config/env value. We change no gateway code.

## Endpoint

```
POST {proxy_url}/v1/chat/completions
```

### Request

Headers:
- `Content-Type: application/json`
- `Authorization: Bearer {GATEWAY_PROXY_KEY}` — present only if env `GATEWAY_PROXY_KEY` is set.
- `X-Hermes-Session-Id: {session_id}` — present when there's a session; the remote is expected
  to maintain its own per-session history keyed by this (the gateway only sends the current
  message + a compact local history for first contact).

Body (JSON, OpenAI chat-completions shape):
```json
{
  "model": "hermes-agent",
  "messages": [
    {"role": "system",    "content": "<context_prompt>"},   // present only if context_prompt set
    {"role": "user",      "content": "<prior user turn>"},   // 0..n history turns, text only
    {"role": "assistant", "content": "<prior assistant turn>"},
    {"role": "user",      "content": "<current message>"}    // ALWAYS the last element
  ],
  "stream": true
}
```
History is text-only user/assistant turns; the remote owns tool replay + system prompt.

### Response — SSE stream of OpenAI chunks

- `HTTP 200` required. Any non-200 → the gateway surfaces `⚠️ Proxy error (status)` to the user
  and discards the turn. (So: **non-200 = fail closed at the gateway.** The broker returns
  non-200 when it cannot run the turn caged.)
- Body is `text/event-stream`. Each event is a line `data: {json}\n`.
- The gateway consumes **only** `choices[0].delta.content` from each chunk, accumulating it into
  the response text:
  ```json
  {"choices": [{"delta": {"content": "<token-or-text>"}}]}
  ```
- Terminate the stream with:
  ```
  data: [DONE]
  ```
- Everything else an OpenAI chunk may carry (`id`, `model`, `usage`, `finish_reason`, tool calls)
  is **ignored** by the gateway. The broker may include them or not; only `delta.content` matters.
- Read timeout is generous (`sock_read=1800s`, no total timeout) — long turns are fine.

### What the gateway fabricates locally (the broker need NOT return)

After the stream, the gateway builds its result dict itself:
`final_response = accumulated content`, `messages = [user, assistant]`, `api_calls = 1`,
`tools = []`. So the broker's job is purely: **stream content deltas, then `[DONE]`.**

## Implications for the cage broker

1. The broker is a tiny OpenAI-compatible SSE server on the host at `{proxy_url}`.
2. Per request it: auth-checks the bearer key (fail closed on mismatch) → resolves
   `X-Hermes-Session-Id` to a group + its mount-allowlist → `docker run --rm aurum-agent` with
   RO project, RW group dir, allowlisted extra mounts (jailed), per-request secrets injected →
   feeds the `messages` to the container's one-turn entrypoint → streams the assistant text back
   as `delta.content` chunks → `data: [DONE]`.
3. **Fail closed:** if docker won't run, mount validation fails, or a secret can't be brokered,
   return non-200 (or a single error delta + `[DONE]`) — NEVER run the turn uncaged or with an
   over-broad mount.
4. The container does NOT need to serve HTTP — it processes one turn over stdin/stdout; the
   broker owns the OpenAI/SSE translation. (Streaming token-by-token is a later refinement; a
   single final-text chunk + `[DONE]` already satisfies the contract.)
5. Session history persistence is the container's job (per-group `HERMES_HOME` on the RW mount);
   the broker just forwards `X-Hermes-Session-Id` → group resolution.
