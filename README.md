**Unofficial — not affiliated with Lidl.** Built from reverse-engineered API requests; may break at any time.

# lidl-plus

Python client for Lidl Plus: fetch receipts and coupons, authenticate via manual browser OAuth, and run an incremental local backup with a searchable archive website.

## Features

- **Browser auth (`browser-auth`)** — reliable OAuth PKCE: log in in your normal browser, paste the callback once. Default install (`requests` + `oic` only).
- **Incremental backup** — download new receipts, snapshot coupons, rebuild the archive index.
- **Local archive website** — search receipts and products, lazy-loaded receipt detail, print-friendly view.
- **Docker** — cron-friendly one-shot sync with data on `/data`; multi-arch images on GHCR.

## Requirements

- Python **3.14+**
- [uv](https://github.com/astral-sh/uv) (recommended)

## Installation

```bash
git clone https://github.com/schlomo/lidl-plus.git
cd lidl-plus
uv sync
```

Optional Selenium extra — only for the broken legacy `auth` command (not needed for `browser-auth`):

```bash
uv sync --extra selenium
```

## Environment variables

| Variable | Purpose |
|----------|---------|
| `LIDL_LANGUAGE` | e.g. `de` |
| `LIDL_COUNTRY` | e.g. `DE` |
| `LIDL_REFRESH_TOKEN` | OAuth refresh token (never commit) |
| `LIDL_DATA_DIR` | Backup root (default `./data`; Docker uses `/data`) |

CLI flags override env where both exist (`-l`, `-c`, `-r`).

## Authentication

| Command | Status | Notes |
|---------|--------|-------|
| **`browser-auth`** | **Works** | Paste OAuth callback from DevTools (or use `--open`). Only supported login path. No Selenium. |
| **`auth`** | **Broken** | Selenium automation against Lidl’s login UI. **Known not to work** — kept for upstream compatibility only. |

### `browser-auth` (use this)

Log in with your real browser, copy the OAuth callback from DevTools, and exchange it for a refresh token.

```bash
uv run lidl-plus -l de -c DE browser-auth
```

Steps printed by the command:

1. Open DevTools → Network before logging in.
2. Open the printed login URL and sign in (including 2FA).
3. Find the canceled `com.lidlplus.app://callback?code=…` request.
4. Paste the full URL or just the `code` value.

Optional flags:

- `--open` — open the login URL in your default browser.

Save the refresh token:

```bash
export LIDL_LANGUAGE=de
export LIDL_COUNTRY=DE
export LIDL_REFRESH_TOKEN="your-token-here"
```

#### Refresh token via Docker

`browser-auth` is interactive — run once on your machine (or in a container with `-it`), log in in a browser on your host, paste the callback into the terminal.

```bash
docker run --rm -it \
  ghcr.io/schlomo/lidl-plus:latest \
  -l de -c DE browser-auth
```

Store credentials in a `.env` file for cron/sync:

```bash
cat > .env <<'EOF'
LIDL_LANGUAGE=de
LIDL_COUNTRY=DE
LIDL_REFRESH_TOKEN=your-token-here
EOF
```

### `auth` — broken, do not use

The legacy `auth` command drove Lidl’s login UI through Selenium. **It does not work with the current Lidl login flow** and is not maintained here.

```bash
# Do not use — will fail
uv run lidl-plus -l de -c DE auth
```

Use `browser-auth` instead.

## Backup

Data lives under `--data-dir` (default `./data`, or `LIDL_DATA_DIR`).

### `backup sync`

Incremental download from the Lidl API (first run fetches everything, later runs only new receipts), optional coupon snapshot, then rebuilds `www/`.

```bash
uv run lidl-plus backup sync
```

Requires `LIDL_LANGUAGE`, `LIDL_COUNTRY`, and `LIDL_REFRESH_TOKEN` (env or `-l` / `-c` / `-r`).

Options:

- `--data-dir ./data` — archive location
- `--full` — re-download all receipts
- `--no-coupons` — skip coupon snapshot

### `backup index`

Rebuild `www/` from existing `archive/receipts/` without calling the API. Use after parser or SPA changes, or to refresh the index without syncing.

```bash
uv run lidl-plus backup index
```

### `backup serve`

Serve `data/www/` over HTTP (required for search and lazy-loaded receipt data — `file://` will not work).

```bash
uv run lidl-plus backup serve
# http://127.0.0.1:8765/
```

Options:

- `--data-dir ./data`
- `--host 127.0.0.1` — use `0.0.0.0` in Docker
- `--port 8765`
- `--no-open` — do not open a browser (headless/Docker)

### Data layout

```
data/
  state.json                      # sync history, known receipt ids
  archive/receipts/{id}.json      # full API ticket payloads (source of truth)
  archive/coupons/                # timestamped snapshots + latest.json
  www/                            # generated; wiped and rebuilt on each index
    index.html
    {hash}.data.json.gz           # search index: receipt rows (r) + products (p)
    {hash}.{receiptId}.json.gz    # per-receipt view model (lazy-loaded)
    {hash}.app.js                 # SPA (content-addressed)
    {hash}.css
    {hash}.svg                    # favicon
```

**Pipeline:** `backup sync` stores raw API JSON unchanged in `archive/receipts/`. `backup index` (also run at end of sync) parses receipts in Python into a compact view model (monospace lines, logo, barcode) and writes the SPA assets. The browser only renders pre-parsed data — no HTML surgery client-side.

## Docker

Multi-arch images (`linux/amd64`, `linux/arm64`) on [GitHub Container Registry](https://github.com/schlomo/lidl-plus/pkgs/container/lidl-plus). Published on merge to `main` via `.github/workflows/ci-cd.yml` (tag: `latest`; build version `1.<commit-count>.0` is in OCI labels). Forks publish under `ghcr.io/<owner>/lidl-plus`.

### Quick start (backup sync)

```bash
mkdir -p ./data

docker run --rm -t \
  -u "$(id -u):$(id -g)" \
  --env-file .env \
  -v "$(pwd)/data:/data" \
  ghcr.io/schlomo/lidl-plus:latest
```

Default command: `lidl-plus backup sync --data-dir /data`.

Build locally:

```bash
docker build -t ghcr.io/schlomo/lidl-plus .
docker run --rm -t -u "$(id -u):$(id -g)" --env-file .env -v "$(pwd)/data:/data" ghcr.io/schlomo/lidl-plus
```

### Browse the archive

```bash
docker run --rm -t -p 8765:8765 \
  -u "$(id -u):$(id -g)" \
  -v "$(pwd)/data:/data" \
  ghcr.io/schlomo/lidl-plus:latest \
  backup serve --host 0.0.0.0 --data-dir /data --no-open
# open http://localhost:8765/
```

### Cron example

```cron
0 3 * * * docker run --rm \
  --env-file /path/to/lidl-plus.env \
  -v /path/to/data:/data \
  ghcr.io/schlomo/lidl-plus:latest
```

## Other commands

Legacy API helpers (require `-l`, `-c`, `-r`):

```bash
# Download N receipts to out/ (HTML files + summary.json)
uv run lidl-plus -l de -c DE -r TOKEN receipt -n 10

# List or activate coupons
uv run lidl-plus -l de -c DE -r TOKEN coupon
uv run lidl-plus -l de -c DE -r TOKEN coupon --all

# Loyalty ID
uv run lidl-plus -l de -c DE -r TOKEN id
```

## Python API

```python
from lidlplus import LidlPlusApi

api = LidlPlusApi("de", "DE", refresh_token="…")
for row in api.tickets():
    ticket = api.ticket(row["id"])
    print(ticket["date"], ticket.get("totalAmount"))
```

## Development

```bash
uv sync --dev
uv run pytest
uv run lidl-plus -h
uv run lidl-plus backup -h
```

## Help

```bash
uv run lidl-plus -h
uv run lidl-plus browser-auth -h
uv run lidl-plus backup -h
uv run lidl-plus backup sync -h
```

## Licence

MIT — see [LICENCE](LICENCE).

Fork trail: [schlomo/lidl-plus](https://github.com/schlomo/lidl-plus) ← [yagueto/lidl-plus](https://github.com/yagueto/lidl-plus) ← [Andre0512/lidl-plus](https://github.com/Andre0512/lidl-plus) (original, Andre Basche).
