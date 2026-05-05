# `deploy/secrets/`

Files dropped here are bind-mounted read-only into Caddy and the
backend services. Nothing in this folder is committed to git
(`.gitignore` keeps it empty in the repo).

## Required for boot

| file | used by | how to get it |
|---|---|---|
| `internal_ca.crt` | Caddy (mTLS verify) + `mint-client-cert` | Export from your internal CA. |
| `internal_ca.key` | `mint-client-cert` only | Internal CA private key (RSA, no passphrase). **Don't put this on the host if you don't intend to mint client certs from it** — leave it offline and copy on demand. |
| `server.crt`      | Caddy | TLS cert for the public hostname. |
| `server.key`      | Caddy | TLS private key. |

## Optional

| file | meaning |
|---|---|
| `clients/`        | Where `mint-client-cert` writes minted operator bundles by default in compose (`/run/daedalus/secrets/clients`). |

## Minting an operator cert

If you already have your internal CA materials in `internal_ca.crt`
and `internal_ca.key`:

```bash
make mint-cert EMAIL=alice@your.lan PIN=true
# → bundles in deploy/secrets/clients/alice_at_your.lan.{key,crt,p12}
```

The `.p12` is what the operator imports into their browser.

If you'd rather keep the CA key offline, run the CLI on a separate
host that does have it:

```bash
python -m daedalus.cli mint-client-cert \
    --email alice@your.lan \
    --ca-cert /vault/internal_ca.crt \
    --ca-key  /vault/internal_ca.key \
    --out-dir /tmp/alice-cert \
    --pin
```

…and then carry the `.p12` to the operator out-of-band.
