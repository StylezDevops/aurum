# Aurum brain image — slim, single-shot Hermes for Aurum's ephemeral cage.
#
# Unlike the stock Hermes image (s6-overlay supervision + web dashboard + terminal
# UI + Playwright + messaging gateways — all of which the cage already provides),
# this image runs `hermes -q` exactly once per message and speaks the cage's
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
        ca-certificates curl git ripgrep util-linux \
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
# pytest is added so Skill-CI (tools/skill_ci.py) can run a skill's tests in a
# hardened subprocess before promotion (validate-before-promote). util-linux
# provides `unshare` for best-effort network isolation of those test runs.
RUN pip install -e . pytest==9.0.2 aiohttp==3.13.3

# The governance organs live at /opt/hermes/aurum/aurum (the `aurum.aurum.*` package).
# `pip install -e .` only exposes Hermes' OWN declared packages, not this nested tree,
# so put the repo root on the path explicitly — otherwise the aurum-governance plugin
# can't import the GovernanceKernel and (fail-closed) would block every tool call.
ENV PYTHONPATH=/opt/hermes

# The cage runs the container as the host UID (--user uid:gid) with HOME=/home/node
# on Linux, or as image-default root on native Windows. Make a world-writable home
# so dotfile writes succeed under either identity.
RUN mkdir -p /home/node && chmod 0777 /home/node

# stdin ContainerInput  ->  hermes -q  ->  sentinel-wrapped ContainerOutput.
COPY container/aurum/entrypoint.py /opt/aurum/entrypoint.py
# Persistent turn server: POST /turn -> hermes -q -> ContainerOutput JSON.
# Started by the broker when AURUM_PERSISTENT_CONTAINER=1 (docker run -d ... serve.py).
COPY container/aurum/serve.py /opt/aurum/serve.py
# Aurum constitution (identity + always-on guardrails). The entrypoint seeds it
# into each group's HERMES_HOME/SOUL.md on first run, substituting ASSISTANT_NAME.
COPY container/aurum/SOUL.md /opt/aurum/SOUL.md

# Expose the persistent turn server port (used when broker runs the container long-lived).
EXPOSE 8901

# Split python + script so `docker run IMAGE /opt/aurum/serve.py` overrides only
# the script (CMD), not the interpreter (ENTRYPOINT). Ephemeral mode (default CMD)
# runs entrypoint.py; persistent mode overrides CMD to serve.py.
ENTRYPOINT ["python"]
CMD ["/opt/aurum/entrypoint.py"]
