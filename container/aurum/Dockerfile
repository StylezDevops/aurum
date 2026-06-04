# Aurum brain image — slim, single-shot Hermes for nanoclaw's ephemeral cage.
#
# Unlike the stock Hermes image (s6-overlay supervision + web dashboard + terminal
# UI + Playwright + messaging gateways — all of which nanoclaw already provides),
# this image runs `hermes -q` exactly once per message and speaks nanoclaw's
# stdin ContainerInput / sentinel ContainerOutput contract via entrypoint.py.
#
# Build (context = aurum repo root):
#   docker build -f container/aurum/Dockerfile -t aurum-agent:latest .
FROM python:3.13-slim-trixie

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Don't lazy-install Node.js 22 on every ephemeral run for browser tools —
    # v1 Aurum has no browser need; this keeps each container fast and quiet.
    # (Phase 6 social-media will use a separate browser-capable image.)
    HERMES_SKIP_NODE_BOOTSTRAP=1

# Runtime deps the agent's core tools expect: ripgrep (Grep tool), git, curl, CA certs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git ripgrep \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/hermes

# Copy source. .dockerignore excludes .git/.venv/.env/node_modules/*.md, so the
# host's Windows .venv never leaks in. pyproject's `readme = "README.md"` is
# excluded by *.md, so placeholder it before the editable install resolves it.
COPY . .
RUN touch README.md
# NOTE: we deliberately do NOT normalize *.sh CRLF→LF here. The Windows clone
# leaves scripts/install.sh with CRLF, so Hermes' first-run dep bootstrap (which
# treats each fresh ephemeral HERMES_HOME as a new install) fails early instead of
# downloading Node.js 22 on every single container run. v1 Aurum uses no shell-
# based Hermes tools, so this is harmless and keeps each run fast and quiet.
# When a later phase needs a specific shell tool, normalize THAT script only.

# Install Hermes core only (no --extra all / no torch) — matches the verified
# local baseline (cli.py -q answered through OpenRouter with just these deps).
RUN pip install -e .

# nanoclaw runs the container as the host UID (--user uid:gid) with HOME=/home/node
# on Linux, or as image-default root on native Windows. Make a world-writable home
# so dotfile writes succeed under either identity.
RUN mkdir -p /home/node && chmod 0777 /home/node

# stdin ContainerInput  ->  hermes -q  ->  sentinel-wrapped ContainerOutput.
COPY container/aurum/entrypoint.py /opt/aurum/entrypoint.py
# Aurum constitution (identity + always-on guardrails). The entrypoint seeds it
# into each group's HERMES_HOME/SOUL.md on first run, substituting ASSISTANT_NAME.
COPY container/aurum/SOUL.md /opt/aurum/SOUL.md

ENTRYPOINT ["python", "/opt/aurum/entrypoint.py"]
