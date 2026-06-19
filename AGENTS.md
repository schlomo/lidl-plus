# Agent instructions

Canonical guide for AI assistants in this repository (Cursor, Claude Code, GitHub Copilot, Cloud Agents). Read this before changing code.

**Unofficial project** — reverse-engineered Lidl Plus APIs. Endpoints, headers, and login flows can break without notice. Do not claim affiliation with Lidl.

**TDD first:** non-trivial behaviour changes start with a failing test (see [TDD](#tdd-required)). **Quality gate:** `uv run pytest` must pass before the task is done (see [Quality gate](#quality-gate-required)).

## TDD (required)

Use test-driven development for any change to parsing, sync/index logic, CLI behaviour, archive output, or SPA logic that has non-trivial branches. Do not implement first and add tests as an afterthought.

### Workflow

1. **Understand the bug or requirement** — reproduce with real `./data` when relevant; note inputs and expected output.
2. **Red** — add or extend a test in `tests/` that fails for the current code. Run `uv run pytest` and confirm the failure matches the bug (not a typo in the test).
3. **Green** — change production code (`lidlplus/…` or `lidlplus/archive/static/…`) until the test passes.
4. **Refactor** — simplify if needed; keep tests green.
5. **Full suite** — `uv run pytest` before handoff (same bar as CI).

Skip TDD only for mechanical edits with no behaviour change (typos, comments, dependency bumps, pure docs). When unsure, write the test.

### Where tests live

| Area | Test file | Production code |
|------|-----------|-----------------|
| Receipt parsing / view model | `tests/test_receipt_items.py`, `tests/test_receipt_view.py` | `lidlplus/archive/receipt.py` |
| CLI, `write_site`, index output | `tests/test_smoke.py`, `tests/conftest.py` | `lidlplus/__main__.py`, `lidlplus/archive/site.py` |
| SPA / client logic | JS/TS test runner (future addition when needed) | `lidlplus/archive/static/` |

Prefer **new focused test modules** over growing `test_smoke.py` when the surface is narrow.

### SPA / JavaScript

**Do not duplicate JS logic in Python tests.** Test client behaviour with JS/TS tooling (e.g. vitest) when coverage is needed.

| Approach | When |
|----------|------|
| **Single source in Python** (preferred) | Receipt parsing, line layout, product names → `lidlplus/archive/receipt.py`; pytest calls production code; SPA only renders the view model |
| **Golden fixtures** | Shared `{input → expected output}` JSON in `tests/fixtures/`; one implementation, tests assert output |
| **Node / vitest** | Client-only logic (e.g. live search highlight) that stays in the SPA |
| **Browser spot-check** | Layout/visual regressions after index rebuild — supplement, not substitute for automated tests |

After SPA or index changes:

```bash
uv run lidl-plus backup index --data-dir /path/to/data
uv run lidl-plus backup serve --data-dir /path/to/data
```

### Good tests

- Assert **observable behaviour** (parsed names, HTML output, file layout), not implementation details.
- Use **realistic strings** from receipt HTML when fixing parsing/highlight bugs (check `data/archive/receipts/` — gitignored but available locally).
- One concern per test; name tests after the behaviour (`test_kreuzberg_not_highlighted_for_product_query`).

### Bad patterns (do not)

- Implement a fix, then add a test that only passes on the new code without having seen it fail.
- Duplicate production logic in tests (including **Python copies of JS**).
- Weaken assertions to make CI green (`assert True`, drop checks on archive payloads).

## Before you change code

1. Read [`README.md`](README.md) for CLI usage, env vars, and Docker.
2. Decide if the task needs TDD (see above). If yes, write the failing test before production code.
3. Skim the module you are touching (see [Architecture](#architecture)).
4. For API changes, read `lidlplus/api.py` — especially `_APP_VERSION` and request headers.
5. For auth changes, read `lidlplus/auth/oauth.py` and `lidlplus/auth/selenium.py`.
6. For receipt UI or search, read `lidlplus/archive/static/` (SPA assets) and `lidlplus/archive/receipt.py` together. `lidlplus/archive/site.py` only builds `www/` from those assets.

## Architecture

| Module | Role |
|--------|------|
| `lidlplus/api.py` | Lidl Plus HTTP client (tickets, coupons, token refresh) |
| `lidlplus/auth/oauth.py` | OAuth PKCE browser login (`browser-auth`) |
| `lidlplus/auth/selenium.py` | Optional Selenium login (legacy `auth` command) |
| `lidlplus/__main__.py` | CLI entry (`lidl-plus`), argument parsing, legacy `receipt`/`coupon`/`id` commands |
| `lidlplus/backup/sync.py` | Incremental sync, `state.json`, coupon snapshots, calls `write_site` |
| `lidlplus/archive/receipt.py` | Parse `htmlPrintedReceipt` → view-model lines; search-index rows; product index |
| `lidlplus/archive/site.py` | Build pipeline: read static assets, inject index metadata, write hashed `www/` |
| `lidlplus/archive/static/` | SPA source (`index.html`, `app.js`, `style.css`, `favicon.svg`) |

**Data layout** (under `--data-dir` / `LIDL_DATA_DIR`, default `./data`):

```
data/
  state.json
  archive/receipts/{receiptId}.json   # full API ticket payloads (unchanged from sync)
  archive/coupons/                    # timestamped snapshots + latest.json
  www/                                # generated from archive/; wiped and rebuilt each index
```

Fork lineage: upstream [yagueto/lidl-plus](https://github.com/yagueto/lidl-plus), extended with backup/archive/SPA functionality. Prefer extending this repo rather than reintroducing a separate wrapper.

### Archive data pipeline

**Principle:** backup stores the **original API JSON** unchanged; `backup index` builds **view-optimized data** for the SPA. Re-indexing can improve parsers without re-downloading.

```
Lidl API ticket JSON
       ↓ backup sync (no transform)
archive/receipts/{receiptId}.json     ← source of truth (htmlPrintedReceipt + store + barCode + logoUrl + …)
       ↓ backup index (Python)
www/
  index.html                           ← entry page; links content-addressed assets
  {hash}.data.json.gz                  ← search index only: r (receipt rows), p (products)
  {hash}.{receiptId}.json.gz           ← per-receipt view model (lazy-loaded by SPA)
  {hash}.app.js / {hash}.css / {hash}.svg
       ↓ browser
SPA                                    ← render pre-parsed lines; search/highlight/print only
```

#### Search index row (`r[]`)

Built by `receipt_record()` in `receipt.py`; `site.py` adds `file` before writing the index.

| Field | Source | Notes |
|-------|--------|-------|
| `id`, `date`, `datetime` | ticket | `date` is `YYYY-MM-DD` slice of API `date` |
| `store`, `address`, `locality`, `postalCode`, `street` | ticket `store` | for list UI and search |
| `total`, `currency`, `currencyCode` | ticket + parsed HTML | |
| `articlesCount`, `items` | parsed `htmlPrintedReceipt` | product names for search |
| `text` | derived | lowercased search blob (id, date, store, street, items, total) |
| `file` | `site.py` | content-addressed gzip filename, e.g. `{hash}.{receiptId}.json.gz` |

Product rows (`p[]`): `{ name, count, purchases: [{ receiptId, date, store }] }` from `build_product_index()`.

Legacy index blobs with an embedded `h` HTML map are **not** supported — only `{ r, p }` plus per-receipt gzip files.

#### Receipt view model (per receipt)

Compact JSON the SPA renders as monospace `<pre>` lines — no HTML surgery in the browser.

```json
{
  "id": "23001934220240228118502",
  "logo": "https://static-tickets.lidlplus.com/images/assets/DE/logo_lidl-DE-new.png",
  "barcode": "0888193411850202280224",
  "barcodeImage": "data:image/svg+xml;base64,…",
  "currency": "€",
  "currencyCode": "EUR",
  "lines": [
    {"text": "Bonkopie", "role": "header"},
    {"text": "Dresdener Straße 10, 10999 Berlin", "role": "header"},
    {"text": "Erdbeeren kg          1,010 kg x 2,99    3,02 A", "role": "item"},
    {"text": "Preisvorteil                       -0,33", "role": "discount", "kind": "plain"},
    {"text": "    Lidl Plus Rabatt               -0,33", "role": "discount", "kind": "lidl_plus", "bold": true},
    {"text": "zu zahlen                         176,48", "role": "summary", "bold": true},
    {"text": "2,80 EUR gespart", "role": "savings", "kind": "lidl_plus", "bold": true}
  ]
}
```

| Field | Source | Notes |
|-------|--------|-------|
| `id` | ticket `id` | same as archive filename stem |
| `logo` | ticket `logoUrl` | **Not** inside `htmlPrintedReceipt`; must come from JSON field |
| `barcode` | ticket `barCode` | return-code digits (plain text under barcode image) |
| `barcodeImage` | `render_barcode_data_url(barCode)` | Code128 SVG as data URL (`python-barcode`) |
| `currency`, `currencyCode` | parsed `htmlPrintedReceipt` | symbol from `data-currency`; ISO code from currency span |
| `lines[].text` | parsed `htmlPrintedReceipt` | span merge + column normalize in **`lidlplus/archive/receipt.py`** |
| `lines[].role` | span `id` prefix + classes | see parsing table below — not German string matching |
| `lines[].kind` | span `class` (+ savings regex on `vat_info_line_*`) | optional internal enum (`plain`, `lidl_plus`, `other`) — **never** parsed from localized label text in `lines[].text` |
| `lines[].bold` | `css_bold` in span group | optional |

#### Parsing `htmlPrintedReceipt` (class-based, i18n-safe)

Lidl marks line types with span **classes**, not localized strings. Parser should be dumb about markup:

1. **Group** spans by shared `id` (`purchase_list_line_40`, …).
2. **Merge** text per group.
3. **Decode** HTML entities; **normalize** monospace columns in **`receipt.py`** (`normalize_receipt_line`, `parse_receipt_lines`, `build_receipt_view`).
4. **Classify** each merged line from span classes in that group (not from German labels):

| Merged line (typical `id` prefix) | Span classes in group | `role` / `kind` |
|-----------------------------------|----------------------|-----------------|
| `header_line_*` | any | `header` |
| `purchase_list_line_*` | `article` (+ optional `css_bold`) | `item` |
| `purchase_list_line_*` | `discount` (+ optional `css_bold`) | `discount` — `lidl_plus` if merged text matches `/lidl plus/i`, else `other` |
| `purchase_list_line_*` | **neither** `article` nor `discount` (not a kg subline) | `discount` / `plain` (promotion line without Lidl’s `discount` class; skip lone currency markers like `EUR`) |
| `purchase_list_line_*` | (text matches kg subline pattern) | `kg_detail` |
| `purchase_summary_*` | any (+ often `css_bold`) | `summary` (totals, tax table, etc.) |
| `vat_info_line_*` | savings footer (`/lidl plus\|eur gespart/i`) | `savings` / `lidl_plus` |
| `vat_info_line_*` | otherwise | `summary` |
| `footer_line_*` | any | `footer` (TSE block, UST-ID, …) |
| `return_code_line_*` | any | `meta` (signature / hash lines before barcode) |
| `purchase_tender_information_*` | any | `tender` (card slip after barcode) |

**Preisvorteil vs Lidl Plus Rabatt:** same as in the Lidl app — Preisvorteil spans have **no class**; Lidl Plus uses `class="discount"`. No need to match the word “Preisvorteil” in Python (works in any language/country as long as Lidl keeps this markup).

`css_bold` is **weight only** (render bold); it does not replace the article/discount/plain rules above.

Store merged **label + amounts as Lidl printed them** in `lines[].text` (any language). SPA maps `kind` → CSS (`plain` = default styling, `lidl_plus` → `#0050aa`).

#### Discount / savings styling (SPA)

| `kind` | Set by parser when | SPA |
|--------|-------------------|-----|
| `plain` | `purchase_list_line_*`, no `article`/`discount` class | normal / slightly indented |
| `lidl_plus` | `discount` class + `/lidl plus/i` in text, or savings footer on `vat_info_line_*` | brand blue `#0050aa` |
| `other` | `discount` class, not Lidl Plus | muted discount |

#### Division of labour

| Layer | Responsibility |
|-------|------------------|
| **Python** | Parse HTML → lines; merge spans; normalize columns; classify `role`/`kind`; decode entities; attach `logoUrl` / `barCode`; gzip per-receipt files; build `r`/`p` search index |
| **SPA** | List/search/products UI; `fetch` receipt view on detail open; render `<pre>` lines + `<img logo>` + barcode widget; `highlightHtml` on plain text; print stylesheet |

#### Per-receipt loading

Index blob stays small (~receipt metadata + products). Receipt bodies load on demand via the `file` field on each `r[]` row:

```javascript
const res = await fetch(receiptRow.file);
const json = await new Response(
  res.body.pipeThrough(new DecompressionStream('gzip')),
).text();
const view = JSON.parse(json);
```

Same `DecompressionStream('gzip')` pattern loads `{hash}.data.json.gz` from `window.LIDL_DATA` on startup.

#### Print fidelity

Target: **Lidl’s line content and layout** (amounts, discounts, totals, barcode) after Python normalization. Print stylesheet uses `@page { size: 80mm auto }` and ~8pt monospace. Logo + barcode included explicitly.

## Lidl API (critical)

- **`App-Version` header** must stay realistic (currently `16.45.5` in `LidlPlusApi._APP_VERSION`). Stock upstream used `999.99.9`, which caused ticket API timeouts — do not revert casually.
- Keep Selenium imports **optional** in `lidlplus/auth/selenium.py`; used only by the broken legacy **`auth`** command — not by `browser-auth`.
- Default auth path is **`browser-auth`** (manual OAuth callback via `oic`) — **the only reliable login**. Selenium **`auth` is broken** with the current Lidl UI; do not use or “fix” casually.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `LIDL_LANGUAGE` | e.g. `de` |
| `LIDL_COUNTRY` | e.g. `DE` |
| `LIDL_REFRESH_TOKEN` | OAuth refresh token (never commit) |
| `LIDL_DATA_DIR` | Backup root (default `./data`; Docker uses `/data`) |

CLI flags override env where both exist. `get_arguments()` returns an `argparse.Namespace` — use attribute access (`args.data_dir`), not dict keys.

## Archive website conventions

When editing the archive SPA in `lidlplus/archive/static/`, preserve these behaviors (regression-prone):

- **Currency** — from receipt view model / index rows; do not hardcode EUR; use `formatTotal(total, currency)` in the SPA.
- **Receipt lines** — pre-parsed in Python (`normalize_receipt_line`, `_pad_receipt_line` for footer/tender); amounts end at column 38 (`_RECEIPT_AMOUNT_END`); SPA renders `lines[]` with `role`/`kind` CSS only. Barcode sits between `meta`/`footer` lines and `tender` lines.
- **Preisvorteil / Lidl Plus Rabatt** — classified at index time by span class; see [Parsing](#parsing-htmlprintedreceipt-class-based-i18n-safe).
- **Logo / barcode** — from ticket JSON via view model (`logo`, `barcode`, `barcodeImage` fields).
- **Product search UX** — clicking a product fills the search field; navigating back to the list clears it.
- **Search highlights** — client-side in `app.js`; add vitest if regressions need automated coverage.
- **Serving** — `lidl-plus backup serve` runs a plain static HTTP server for `www/`. The SPA fetches `{hash}.data.json.gz` and per-receipt `{hash}.{receiptId}.json.gz`; both are decompressed in the browser via `DecompressionStream('gzip')`.

## Development

- **Python ≥ 3.14**, package manager **[uv](https://github.com/astral-sh/uv)**.
- Install: `uv sync` (dev: `uv sync --dev`; Selenium: `uv sync --extra selenium`).
- Run CLI: `uv run lidl-plus …`
- Keep dependencies minimal: `requests` + `oic` by default; Selenium stack only behind `[selenium]` extra.

### Quality gate (required)

**Do not finish a task that changes code until pytest passes.** CI runs the same in [`.github/workflows/ci-cd.yml`](.github/workflows/ci-cd.yml).

For behaviour changes, the gate includes **having followed [TDD](#tdd-required)**: a failing test existed before the fix unless the change was explicitly test-exempt (docs-only, etc.).

Run before handoff:

```bash
uv sync --dev --frozen
uv run pytest
```

- **All tests must pass** — fix or revert; do not hand off red CI.
- **Smoke tests** (`tests/test_smoke.py`) cover CLI help, imports, OAuth callback parsing, `write_site()` output, and `backup index`. Touching those areas without updating tests is a regression risk.

Optional sanity checks (not a substitute for pytest):

```bash
uv run lidl-plus -h
uv run lidl-plus backup -h
```

## Docker

- `Dockerfile` — multi-stage Alpine build, `WORKDIR /data`, `VOLUME /data`. Local build: `docker build -t ghcr.io/schlomo/lidl-plus .`
- Default: `lidl-plus backup sync --data-dir /data`.
- Mount host data: `-v "$(pwd)/data:/data"` plus `LIDL_*` env vars (`.env` / `--env-file`).
- Published to `ghcr.io/schlomo/lidl-plus` via [`.github/workflows/ci-cd.yml`](.github/workflows/ci-cd.yml) on merge to `main` (`latest` + `1.<commit-count>.0`; no git tag releases).
- Refresh token: interactive `docker run -it … -l de -c DE browser-auth` (host browser + paste callback).
- Do not bake secrets into the image.

## Commits and secrets

- Only commit when the user explicitly asks.
- Never commit refresh tokens, `.env`, or real `data/archive/` payloads.
- Update `README.md` when CLI flags, env vars, or Docker behavior change.

## Scope discipline

- Minimize diffs; match existing style (`from __future__ import annotations`, pathlib, logging).
- Do not add markdown files the user did not ask for.
- Do not reintroduce `setup.py` / `requirements.txt` — use `pyproject.toml` + `uv.lock`.
