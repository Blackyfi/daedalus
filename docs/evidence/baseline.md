# Daedalus Baseline Evidence

Collected: 2026-06-26 by automated baseline run. All results are real command output, not estimates.

---

## 1. Backend tests (pytest)

**Working method:** option (d) — a one-off container from the `deploy-hermes:latest` image with
the working-tree `backend/` bind-mounted over `/app`, on network `deploy_backnet`, with `--env-file .env`.

- Local system Python (`/usr/bin/python3`, 3.12.3) does **not** work: `ModuleNotFoundError: No module
  named 'prometheus_client'` during collection. No project venv exists (`.venv` / `pyvenv.cfg` absent).
- The image already ships pytest 9.0.3 + all runtime deps. `pip install -e '.[dev]'` inside the
  container **failed** (`ERROR: Failed to build 'file:///app' when getting requirements to build
  editable`), but pytest ran fine against the baked install + mounted source, so this did not block.

Command:
```
docker run --rm --network deploy_backnet --env-file .env \
  -v "$PWD/backend:/app" -w /app --entrypoint sh deploy-hermes:latest \
  -c "python -m pytest -q"
```

**Result: 1 failed, 128 passed in 6.14s** (124 tests reported at collect-only with system python; full
run in-container collected/ran 129). Test files in `backend/tests`: **21**.

### The single failure (environment artifact, not a code bug)
```
________________ test_internal_key_falls_back_to_session_secret ________________
    def test_internal_key_falls_back_to_session_secret() -> None:
        s = get_settings()
>       assert s.internal_key == s.session_secret
E       AssertionError: assert '90e77e3d429b...54abcf62a24b6' == 'KCV8ByDxnLi4...xTB1xR8JobPAZ'
E         - KCV8ByDxnLi4XW1M59NK5pBMYey6_DaLN_4T74yeENvl3wPyfpwxTB1xR8JobPAZ
E         + 90e77e3d429bba6d71c93a2be5fbf1cffa92d432f7eedf11a5554abcf62a24b6
tests/test_security_hardening.py:40: AssertionError
```
Cause: the run used the production `--env-file .env`, which sets `DAEDALUS_INTERNAL_KEY` to a distinct
value. The test asserts the *fallback* (internal_key defaults to session_secret when unset), which can
only hold when `DAEDALUS_INTERNAL_KEY` is absent. This is a test-vs-env conflict from supplying the real
`.env`, not a defect in the code. Run without that env var, it would pass.

---

## 2. Lint (ruff)

ruff 0.15.14, run in-container over `backend/`:
```
docker run --rm -v "$PWD/backend:/app" -w /app --entrypoint sh deploy-hermes:latest -c "ruff check ."
```

**Result: 166 errors found (140 auto-fixable with `--fix`; 21 more via `--unsafe-fixes`).**
Sampled rules are predominantly in test files — e.g. `RUF059` (unpacked variable never used),
`UP037` (quotes on type annotations), unused `noqa` directives. Exit code reported 0 in the wrapper due
to piping; the "Found 166 errors" line is authoritative.

---

## 3. Frontend build

`cd frontend && npm run build` (script: `tsc -b && vite build`). `node_modules` already present.

**Result: SUCCESS (exit 0), built in 6.54s. Zero TypeScript errors.**

Two stale, root-owned artifacts from a prior in-container build initially blocked it (not code issues):
1. `tsconfig*.tsbuildinfo` — `TS5033 EACCES: permission denied` (root-owned; removed).
2. `dist/` (root-owned `dist/` dir, root-owned contents) — `EACCES: permission denied, rmdir
   dist/assets`. Moved aside to `frontend/dist.rootowned.bak` (still present, cannot delete without
   root), then build produced a fresh user-owned `dist/`.

After clearing those, `tsc -b` passed clean and `vite build` succeeded. Only a non-fatal warning remains:
the main chunk `index-*.js` is 1,649 kB (>500 kB chunk-size advisory). There is also a benign CSS notice
about an `@import` (`@xterm/xterm/css/xterm.css`) ordering after `@tailwind` — warning only, build passes.

### Frontend tests: NONE
- `package.json` scripts: `dev`, `build`, `preview` only — no `test` script.
- No `vitest` / `playwright` / `jest` in dependencies or devDependencies.
- No `*.test.*` / `*.spec.*` files under `frontend/src`.

---

## 4. Live stack probes (unauthenticated, read-only — no data mutated)

```
$ curl -sk https://localhost:9443/api/health
{"status":"ok"}

$ curl -sk https://localhost:9443/api/v1/auth/status
{"authenticated":false,"user":null}
```
Both reachable through Caddy without a client cert.

```
$ curl -sk -o /dev/null -w '%{http_code}' https://localhost:9443/metrics
200   # but body is the SPA index.html, NOT Prometheus metrics
```
`/metrics` is **not** exposed via Caddy — the path falls through to the frontend SPA (`<title>Daedalus
</title>` HTML), so no Prometheus scrape surface is reachable on the public 9443 vhost.

### Running containers
```
deploy-api-1               Up 9 days
deploy-iris-1              Up 9 days
deploy-argus-worker-1      Up 9 days
deploy-talos-1             Up 9 days
deploy-hermes-1            Up 9 days
deploy-frontend-1          Up 9 days
deploy-pg-backup-1         Up 9 days
deploy-agentnet-firewall-1 Up 9 days
deploy-postgres-1          Up 9 days (healthy)
deploy-caddy-1             Up 9 days
deploy-redis-1             Up 9 days (healthy)
deploy-litellm-1           Up 9 days (healthy)
deploy-minio-1             Up 9 days
deploy-mailpit-1           Up 9 days (healthy)
```
All 14 Daedalus containers up ~9 days (started ~2026-06-17).

### Stale-image determination — NOT STALE (relative to git HEAD)
```
deploy-api-1 image created : 2026-05-27T16:56:33Z  (UTC)  = 18:56 +0200
deploy-hermes-1 image      : 2026-05-27T11:36:37Z
git HEAD (95cf712)         : Wed May 27 13:35:44 2026 +0200  = 11:35 UTC
commits after image build  : (none)
git status                 : clean (working tree == HEAD)
```
The `deploy-api` image was built ~5 h **after** the current HEAD commit, and there have been **zero
commits since**. The working tree is clean (no uncommitted changes). Therefore the running images
**reflect the current HEAD** and are functionally current, *not* stale versus the repo.

Caveat / nuance: the images are ~1 month old and containers run baked code with no source bind-mount, so
any *uncommitted* edits would not be live — but right now there are none, so what's deployed == what's in
git. The earlier "running images are stale" note (E2E test 2026-05-27) pre-dated this image rebuild and
no longer applies.
