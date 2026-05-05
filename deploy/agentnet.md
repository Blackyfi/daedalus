# agentnet egress filtering

The `agentnet` Docker network is the only network reachable by Talos and
Argus runner containers. Egress from this network is filtered by the
**`agentnet-firewall` sidecar** (`deploy/agentnet-firewall/`) which runs
in `network_mode: host` and rewrites a fenced block of rules in the
host's `DOCKER-USER` iptables chain on every reload.

## What gets allowed

Each cycle the sidecar builds the union of:

1. `AGENTNET_FIREWALL_BASELINE_HOSTS` from `.env` — the hosts every run
   needs (defaults to `api.anthropic.com,api.openai.com`).
2. The `egress_allowlist` arrays from every connector spec under
   `${CONNECTORS_DIR}` (mounted from `connectors/` into the sidecar).

Every host is resolved (A records), and one `ACCEPT -d <ip>` rule is
written per resolved IP. DNS (TCP/UDP 53) is always allowed so the
agent can resolve names; loopback is left alone. Anything else from
the agentnet bridge is `REJECT`ed.

## Reload cadence

The loop wakes up every `AGENTNET_FIREWALL_RELOAD_SECONDS` (default
120 s), removes the previous fenced block, and writes a fresh one.
This means:

- Editing a connector's `egress_allowlist` and saving the JSON file
  applies within ≤2 minutes — no restart needed.
- The fence comments (`daedalus-agentnet-start` / `…-end`) are how
  the sidecar finds its own rules — don't write rules with those
  exact comments by hand.

## Pinning the bridge

The sidecar auto-detects the `agentnet` Docker bridge name from
`/proc/net/dev`. If you have many `br-*` interfaces and the wrong one
is picked, set `AGENTNET_BRIDGE_NAME` in `.env` to the canonical name
from `docker network inspect agentnet --format '{{.Id}}' | cut -c1-12
| sed 's/^/br-/'`.

## Verifying

```bash
# What's currently applied
sudo iptables -t filter -nvL DOCKER-USER --line-numbers

# Sidecar logs show each apply cycle
docker compose -f deploy/docker-compose.yml logs -f agentnet-firewall
```

## Emergency bypass

```bash
docker compose -f deploy/docker-compose.yml stop agentnet-firewall
sudo iptables -F DOCKER-USER   # leaves DOCKER-USER empty (default RETURN)
```

Restart the sidecar to re-apply.

## Manual recipe (fallback only)

If you can't run the sidecar (e.g. on a host without `NET_ADMIN`), the
old manual recipe still works:

```bash
sudo iptables -I DOCKER-USER -i br-agentnet -p udp --dport 53 -j ACCEPT
sudo iptables -I DOCKER-USER -i br-agentnet -p tcp --dport 53 -j ACCEPT
sudo iptables -I DOCKER-USER -i br-agentnet -d api.anthropic.com -j ACCEPT
sudo iptables -I DOCKER-USER -i br-agentnet -d api.openai.com    -j ACCEPT
sudo iptables -A DOCKER-USER -i br-agentnet -j REJECT
```
