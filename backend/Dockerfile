FROM python:3.12-slim

ARG DAEDALUS_INSTALL_EXTRAS=""

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        git openssh-client curl ca-certificates build-essential libpq-dev gnupg \
    && rm -rf /var/lib/apt/lists/*

# Go toolchain. Talos & argus-worker run project-supplied verify_commands
# (ruff/pytest/go test/...) and Argus grades the captured output. If the
# tooling is missing the agent's verify scripts fall back to a structural
# stub and Argus correctly marks the run partial — see the needs_fixes
# audit. Install Go from the official tarball (Debian's golang-go is too
# old for modules-heavy projects like cloud-auto-sync-save).
ARG GO_VERSION=1.22.6
RUN curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" \
        -o /tmp/go.tar.gz \
    && tar -C /usr/local -xzf /tmp/go.tar.gz \
    && rm /tmp/go.tar.gz \
    && ln -s /usr/local/go/bin/go /usr/local/bin/go \
    && ln -s /usr/local/go/bin/gofmt /usr/local/bin/gofmt \
    && go version

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
RUN git config --system --add safe.directory '*' && \
    git config --system user.email 'daedalus@daedalus.local' && \
    git config --system user.name  'Daedalus'

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
# `ruff` and `pytest` ship with the dev extras and are needed by the default
# connector verify_commands. Pre-install the dev extras unconditionally so
# the runner containers always have the lint/test gate available; runtime
# size impact is small and the production services don't invoke them.
RUN pip install --upgrade pip && \
    EXTRAS="${DAEDALUS_INSTALL_EXTRAS:-dev}" && \
    pip install -e ".[${EXTRAS}]"

COPY . /app

EXPOSE 8000

CMD ["./entrypoint.sh"]
