"""Aurum cage — deployment-hardening substrate (not an organ).

The cage is where untrusted/agent execution runs: an ephemeral `docker run --rm`
container with least-privilege, allowlisted host mounts and per-request secret
injection. Per the spec's DEPLOYMENT & RUNTIME HARDENING section this is substrate,
not a new organ — it hardens what PK/TS/AA/EL already assume.

Modules:
- `mount_jail`  — the allowlist/jailing primitive (deny-by-default, symlink/traversal
  safe). The containment boundary; covered by AURUM_ERR_021.
- `broker`      — the OpenAI-compatible `/v1/chat/completions` SSE server Hermes' gateway
  proxies to; per request it runs the turn in a `--rm` cage and streams the result.
  (See PROXY_CONTRACT.md.)
"""
