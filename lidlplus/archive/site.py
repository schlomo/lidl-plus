"""Generate searchable static HTML archive."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import shutil
from pathlib import Path

from lidlplus.archive.receipt import build_product_index, build_receipt_view, receipt_record

INDEX_HTML = "index.html"
_HASH_LEN = 12
_STATIC_DIR = Path(__file__).resolve().parent / "static"


def write_site(data_dir: Path) -> int:
    """Wipe and rebuild www/ from archive/receipts/."""
    archive_dir = data_dir / "archive" / "receipts"
    www_dir = data_dir / "www"
    if www_dir.exists():
        shutil.rmtree(www_dir)
    www_dir.mkdir(parents=True)

    records = []
    for path in sorted(archive_dir.glob("*.json")):
        with path.open(encoding="utf-8") as handle:
            ticket = json.load(handle)
        record = receipt_record(ticket)
        record["file"] = _write_receipt_view(www_dir, ticket)
        records.append(record)

    records.sort(key=lambda row: row.get("datetime") or "", reverse=True)
    products = build_product_index(records)
    date_range = _date_range_label(records)

    favicon_name = _write_asset(www_dir, (_STATIC_DIR / "favicon.svg").read_bytes(), "svg")
    css_name = _write_asset(www_dir, _minify_css(_read_static("style.css")).encode(), "css")
    data_name = _write_archive_data(www_dir, records, products)
    app_name = _write_asset(www_dir, _minify_js(_read_static("app.js")).encode(), "app.js")

    index_html = _minify_html(
        _render_index(
            len(records),
            len(products),
            date_range,
            favicon=favicon_name,
            css=css_name,
            data=data_name,
            app_js=app_name,
        )
    )
    index_path = www_dir / INDEX_HTML
    index_path.write_text(index_html, encoding="utf-8")
    return len(records)


def _read_static(name: str) -> str:
    return (_STATIC_DIR / name).read_text(encoding="utf-8")


def _content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()[:_HASH_LEN]


def _write_asset(www_dir: Path, content: bytes, ext: str) -> str:
    """Write content-addressed asset; return filename."""
    name = f"{_content_hash(content)}.{ext}"
    (www_dir / name).write_bytes(content)
    return name


def _write_receipt_view(www_dir: Path, ticket: dict) -> str:
    """Write gzip-compressed receipt view model; return content-addressed filename."""
    view = build_receipt_view(ticket)
    content = gzip.compress(
        json.dumps(view, ensure_ascii=False, separators=(",", ":")).encode(),
        compresslevel=9,
    )
    name = f"{_content_hash(content)}.{ticket['id']}.json.gz"
    (www_dir / name).write_bytes(content)
    return name


def _write_archive_data(
    www_dir: Path,
    records: list,
    products: list,
) -> str:
    """Write gzip-compressed search index; return content-addressed filename."""
    payload = {"r": records, "p": products}
    content = gzip.compress(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
        compresslevel=9,
    )
    name = f"{_content_hash(content)}.data.json.gz"
    (www_dir / name).write_bytes(content)
    return name


def _minify_css(css: str) -> str:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    css = re.sub(r"\s+", " ", css)
    css = re.sub(r"\s*([{}:;,>+~])\s*", r"\1", css)
    return css.strip()


def _minify_js(js: str) -> str:
    return "".join(line.strip() for line in js.splitlines() if line.strip())


def _minify_html(html: str) -> str:
    html = re.sub(r">\s+<", "><", html)
    return re.sub(r"\s+", " ", html).strip()


def _date_range_label(records: list) -> str:
    dates = sorted({row["date"] for row in records if row.get("date")})
    if not dates:
        return ""
    if len(dates) == 1:
        return dates[0]
    return f"{dates[0]} – {dates[-1]}"


def _render_index(
    count: int,
    product_count: int,
    date_range: str,
    *,
    favicon: str,
    css: str,
    data: str,
    app_js: str,
) -> str:
    title = "Lidl Plus Archive"
    if date_range:
        title = f"{title} · {date_range}"
    meta = f"{count} receipts · {product_count} products"
    if date_range:
        meta = f"{meta} · {date_range}"
    html = _read_static("index.html")
    return html.format_map(locals())
