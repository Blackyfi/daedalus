#!/usr/bin/env bash
# Daedalus agentnet egress firewall — apply / reload loop.
#
# Periodically:
# 1. Read every connector spec under $CONNECTORS_DIR.
# 2. Collect the union of `egress_allowlist` hosts plus the comma-separated
#    $AGENTNET_FIREWALL_BASELINE_HOSTS.
# 3. Resolve each host to A records.
# 4. Discover the agentnet bridge (or use the pinned AGENTNET_BRIDGE_NAME).
# 5. Replace our dedicated `DAEDALUS_AGENTNET` chain with fresh rules:
#       ACCEPT loopback + DNS + every resolved IP + RELATED,ESTABLISHED
#       REJECT everything else.
#    `DOCKER-USER` gets one jump rule into our chain. That's the entire
#    daedalus footprint in DOCKER-USER, so we never accidentally confuse
#    our rules with rules other compose stacks add.
#
# Runs with `network_mode: host` + cap NET_ADMIN; talks to the host kernel's
# netfilter through the in-container iptables binary (iptables-nft).

set -euo pipefail

: "${CONNECTORS_DIR:=/etc/daedalus/connectors}"
: "${AGENTNET_FIREWALL_RELOAD_SECONDS:=120}"
: "${AGENTNET_FIREWALL_BASELINE_HOSTS:=}"
: "${AGENTNET_BRIDGE_NAME:=}"

# Sub-chain holding all of our rules. Wholly owned by this sidecar — flushed
# and rewritten on every reload, never edited by hand.
SUBCHAIN="DAEDALUS_AGENTNET"

log() { printf '%s agentnet-fw %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }

# ── helpers ──────────────────────────────────────────────────────────────

resolve_host() {
  local host="$1"
  if [[ "$host" =~ ^[0-9.]+(/[0-9]+)?$ ]] || [[ "$host" =~ : ]]; then
    printf '%s\n' "$host"
    return
  fi
  dig +short +time=2 +tries=1 "$host" A 2>/dev/null \
    | grep -E '^[0-9.]+$' || true
}

collect_hosts() {
  {
    if [ -n "$AGENTNET_FIREWALL_BASELINE_HOSTS" ]; then
      printf '%s\n' "$AGENTNET_FIREWALL_BASELINE_HOSTS" \
        | tr ',' '\n' \
        | tr -d ' '
    fi
    if [ -d "$CONNECTORS_DIR" ]; then
      find "$CONNECTORS_DIR" -maxdepth 2 -type f -name '*.json' \
        -exec jq -r '.egress_allowlist // [] | .[]' {} \; 2>/dev/null
    fi
  } | awk 'NF' | sort -u
}

discover_bridge() {
  if [ -n "$AGENTNET_BRIDGE_NAME" ]; then
    printf '%s' "$AGENTNET_BRIDGE_NAME"
    return
  fi
  awk -F: '/^[ ]*br-/{print $1}' /proc/net/dev 2>/dev/null \
    | tr -d ' ' \
    | head -n 1
}

ensure_subchain() {
  iptables -t filter -N "$SUBCHAIN" 2>/dev/null || true
  iptables -t filter -F "$SUBCHAIN"
}

ensure_jump_in_docker_user() {
  local bridge="$1"
  # Make sure DOCKER-USER itself exists (rare on a fresh host).
  iptables -t filter -N DOCKER-USER 2>/dev/null || true

  # Strip any prior daedalus jump rules so we have exactly one.
  while iptables -t filter -C DOCKER-USER -i "$bridge" -j "$SUBCHAIN" 2>/dev/null; do
    iptables -t filter -D DOCKER-USER -i "$bridge" -j "$SUBCHAIN"
  done

  # Insert at position 1 so it runs before any other DOCKER-USER rule.
  iptables -t filter -I DOCKER-USER 1 -i "$bridge" -j "$SUBCHAIN"
}

populate_subchain() {
  local -a ips=("$@")

  # Allow return traffic for connections we already accepted on egress.
  iptables -t filter -A "$SUBCHAIN" \
    -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT

  # Always allow DNS so name-resolution doesn't break inside the agent.
  iptables -t filter -A "$SUBCHAIN" -p udp --dport 53 -j ACCEPT
  iptables -t filter -A "$SUBCHAIN" -p tcp --dport 53 -j ACCEPT

  # ACCEPT each resolved IP.
  for ip in "${ips[@]}"; do
    [ -z "$ip" ] && continue
    iptables -t filter -A "$SUBCHAIN" -d "$ip" -j ACCEPT
  done

  # REJECT the rest.
  iptables -t filter -A "$SUBCHAIN" -j REJECT --reject-with icmp-port-unreachable
}

apply_once() {
  local bridge
  bridge="$(discover_bridge)"
  if [ -z "$bridge" ]; then
    log "no agentnet bridge found yet — skipping this cycle"
    return 0
  fi

  local hosts
  hosts="$(collect_hosts)"
  if [ -z "$hosts" ]; then
    log "no allowlist hosts configured — applying default-deny on bridge=${bridge}"
  else
    log "applying allowlist on bridge=${bridge}, hosts=$(printf '%s' "$hosts" | tr '\n' ',' | sed 's/,$//')"
  fi

  local resolved=()
  while IFS= read -r host; do
    [ -z "$host" ] && continue
    while IFS= read -r ip; do
      [ -z "$ip" ] && continue
      resolved+=("$ip")
    done < <(resolve_host "$host")
  done <<<"$hosts"

  if [ "${#resolved[@]}" -gt 0 ]; then
    mapfile -t resolved < <(printf '%s\n' "${resolved[@]}" | sort -u)
  fi

  log "resolved ${#resolved[@]} unique IPs"

  ensure_subchain
  populate_subchain "${resolved[@]}"
  ensure_jump_in_docker_user "$bridge"
}

cleanup_legacy_rules() {
  # Remove anything in DOCKER-USER that targets our bridge but isn't our
  # `-j DAEDALUS_AGENTNET` jump. This catches:
  #   - the v0 fence-comment markers (RETURN with a comment)
  #   - the v0 in-place ACCEPT/REJECT rules between those fences
  #   - stale rules from earlier reload cycles that wrote different rules
  # The only daedalus-owned entry we keep in DOCKER-USER is the single
  # jump rule that `ensure_jump_in_docker_user` re-asserts on every cycle.
  local bridge="${1:-}"
  [ -z "$bridge" ] && return 0

  # Loop until no more matching rules remain. We always delete the
  # *highest* line number first so positions stay valid. A rule is
  # "ours-to-keep" iff it jumps to $SUBCHAIN; everything else that
  # mentions the bridge is leftover and gets pruned.
  while true; do
    # Combine declaration + assignment so a failing pipe stage (grep with no
    # matches) doesn't abort under `set -e -o pipefail` — `local` always
    # returns 0.
    local idx
    local rules
    rules="$(iptables -t filter -nvL DOCKER-USER --line-numbers 2>/dev/null || true)"
    idx="$(printf '%s\n' "$rules" \
      | grep -E "^\s*[0-9]+\s" \
      | grep -F "$bridge" \
      | grep -v -F "$SUBCHAIN" \
      | awk '{ print $1 }' \
      | sort -rn \
      | head -n 1 || true)"
    [ -z "$idx" ] && break
    iptables -t filter -D DOCKER-USER "$idx" 2>/dev/null || break
  done
}

# ── main loop ────────────────────────────────────────────────────────────

# Pre-flight cleanup using whatever bridge we'll target. This catches stale
# rules from a previous version of this sidecar.
cleanup_legacy_rules "$(discover_bridge)"

trap 'log "shutting down — leaving last-applied rules in place"; exit 0' TERM INT

log "starting reload loop, period=${AGENTNET_FIREWALL_RELOAD_SECONDS}s, connectors=${CONNECTORS_DIR}"

while true; do
  if ! apply_once; then
    log "apply cycle failed — will retry"
  fi
  sleep "$AGENTNET_FIREWALL_RELOAD_SECONDS" &
  wait $!
done
