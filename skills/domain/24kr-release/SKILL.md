---
name: 24kr-release
description: "Operate the 24 Karat Recordings release pipeline: validate a release folder, then submit it to the Label Engine as a draft."
version: 1.0.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [24kr, label, release, pipeline, dnb, jungle, devops]
    related_skills: [gmail-2fa]
---

# 24KR Release Pipeline

## Overview

This skill lets you (Aurum) drive Dan's existing 24 Karat Recordings release
pipeline. You do **not** reimplement any release logic — the proven pipeline
already does validation, payload building, ISRC/cat-number resolution, and the
Playwright Label Engine browser submission. You orchestrate it over its HTTP API.

The pipeline runs a FastAPI server on the **host**, reachable from inside the
container at `http://host.docker.internal:9111` (the cage adds the host-gateway
mapping automatically). Authenticate every request with the `X-API-Key` header,
whose value is the `PIPELINE_API_KEY` provided to the container environment.

## Prerequisites

- `PIPELINE_API_KEY` present in the environment (injected by the cage / OneCLI).
- The pipeline API server running on the host (`:9111`). If `GET /health` fails,
  tell the user the pipeline server isn't up — do not guess or fabricate results.
- The release folder lives under the allowlisted pipeline mount
  (`/workspace/extra/24kr-release-pipeline/...`); only read it if you need to
  inspect a manifest. The API operates on `folder_name` only.

## API contract

Base: `http://host.docker.internal:9111` · Header: `X-API-Key: $PIPELINE_API_KEY`

| Method & path        | Body                              | Purpose |
|----------------------|-----------------------------------|---------|
| `GET  /health`       | — (no auth)                       | Liveness check |
| `GET  /releases`     | —                                 | List available release folders |
| `POST /validate`     | `{"folder_name": "..."}`          | Validate a release manifest |
| `POST /submit`       | `{"folder_name": "...", "headless": true}` | Submit to Label Engine (draft) |
| `POST /archive`      | `{"folder_name": "..."}`          | Move a completed release to archive |
| `POST /fail`         | `{"folder_name": "..."}`          | Move a failed release aside |
| `POST /refresh-session` | —                              | Refresh the Label Engine browser session |

## Workflow

1. **Health first.** `GET /health`. If it doesn't return OK, stop and report that
   the pipeline server is offline.
2. **Identify the release.** If the user names a catalogue number / folder, use it
   as `folder_name`. If unsure, call `GET /releases` and confirm with the user.
3. **Validate.** `POST /validate {folder_name}`. Relay the validation result
   plainly — list any errors the pipeline reports. Do not proceed to submit if
   validation fails; surface the errors so the user can fix the manifest.
4. **Submit as draft (only when asked).** `POST /submit {folder_name, headless:true}`.
   Submission is sequential and can take minutes (the server holds a lock, ~15 min
   timeout) while Playwright drives the Label Engine. Report the outcome the server
   returns. If submission needs a Label Engine 2FA code, use the [[gmail-2fa]] skill
   to retrieve it.
5. **Report back** the concrete result (validated / submitted draft / errors) to
   the channel. Never claim a submission succeeded unless the API said so.

## Example (using the code_execution / terminal toolset)

```bash
curl -fsS -X POST http://host.docker.internal:9111/validate \
  -H "X-API-Key: $PIPELINE_API_KEY" -H "Content-Type: application/json" \
  -d '{"folder_name": "24KJ121"}'
```

## Guardrails

- Treat `/submit`, `/archive`, `/fail` as **state-changing** — only run them on an
  explicit user request, and confirm the `folder_name` first.
- Never fabricate a release result. If the API is unreachable or errors, say so.
- The API key is a secret: use it only in the `X-API-Key` header; never echo it
  back to the channel or write it to the group folder.
