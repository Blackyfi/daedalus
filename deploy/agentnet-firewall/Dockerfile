# Daedalus agentnet egress firewall sidecar.
#
# Reads connector specs every $AGENTNET_FIREWALL_RELOAD_SECONDS, builds the
# union of all `egress_allowlist` hosts (plus the global baseline), resolves
# them to IPs, and writes a fenced block of iptables rules into the host's
# DOCKER-USER chain. The fence is replaced on every reload — no leftover
# rules.
#
# This container runs with network_mode: host and NET_ADMIN so the iptables
# binary inside the container talks to the kernel's real netfilter. It does
# NOT have access to Docker; it watches connector files via a bind-mount.

FROM alpine:3.20

RUN apk add --no-cache \
        bash \
        iptables \
        ip6tables \
        bind-tools \
        jq \
        ca-certificates \
        coreutils \
        findutils

COPY apply.sh /usr/local/bin/apply
RUN chmod +x /usr/local/bin/apply

ENTRYPOINT ["/usr/local/bin/apply"]
