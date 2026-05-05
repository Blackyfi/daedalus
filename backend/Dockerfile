FROM python:3.12-slim

ARG DAEDALUS_INSTALL_EXTRAS=""

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates build-essential libpq-dev gnupg \
    && rm -rf /var/lib/apt/lists/*

# Node 22 + the Anthropic Claude Code CLI. Daedalus's planner / Argus /
# claude-code connector all shell out to `claude`, which authenticates
# against the operator's Pro/Max subscription via OAuth state mounted
# from the host's ~/.claude. NB: ANTHROPIC_API_KEY must be UNSET inside
# this container, otherwise the CLI prefers it over the OAuth token.
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/* && \
    npm install -g @anthropic-ai/claude-code && \
    claude --version

# Bind-mounted host workspaces are owned by the host operator (uid 1000),
# but processes inside the container run as root. Git's "dubious ownership"
# guard would refuse to operate on those repos. Tell git to trust everything
# at the system level — safe in a single-tenant container that already has
# full mount-time write access.
RUN git config --system --add safe.directory '*'

# Non-root user for the talos/argus-worker containers. Claude Code refuses
# `--dangerously-skip-permissions` when running as root, and forcing the
# uid to match the host operator (1000:1000) means bind-mounted workspaces
# and ~/.claude are read/write without chown gymnastics. The api / iris /
# hermes containers keep root since they don't spawn yolo agents — the
# `user:` directive in compose decides per-service.
ARG DAEDALUS_UID=1000
ARG DAEDALUS_GID=1000
RUN groupadd -g ${DAEDALUS_GID} daedalus 2>/dev/null || true && \
    useradd -m -u ${DAEDALUS_UID} -g ${DAEDALUS_GID} -s /bin/bash daedalus && \
    install -d -o daedalus -g daedalus /home/daedalus/.claude

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip && \
    if [ -n "$DAEDALUS_INSTALL_EXTRAS" ]; then \
        pip install -e ".[${DAEDALUS_INSTALL_EXTRAS}]"; \
    else \
        pip install -e .; \
    fi

COPY . /app

EXPOSE 8000

CMD ["./entrypoint.sh"]
