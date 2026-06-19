"""Receipt parsing helpers for search indexing and archive view models."""

from __future__ import annotations

import html
import logging
import re
from html.parser import HTMLParser
from typing import Any

# Lidl puts UTF-8 in data-art-description; visible span text uses &euro; / &ouml; etc.
_ARTICLE = re.compile(r'data-art-id="(\d+)"[^>]*data-art-description="([^"]*)"')
_DEPOSIT_PREFIX = re.compile(r"^\d+[,.]?\d*\s*€?\s*DP\s+", re.IGNORECASE)
_RECEIPT_AMOUNT_END = 38
_RECEIPT_LINE_WIDTH = 42
_UNIT_EXPR_COL = 22
_KG_SUBLINE_INDENT = 3
_LIDL_PLUS = re.compile(r"lidl plus", re.IGNORECASE)
_SAVINGS = re.compile(r"lidl plus|eur gespart", re.IGNORECASE)
_KG_LINE = re.compile(r"\bkg x\b|[A-Z]{3}/kg")
_KG_SUBLINE = re.compile(r"^\d+,\d+\s*kg\s*x\s*\d+,\d+\s*EUR/kg\s*$", re.IGNORECASE)
_UNIT_LINE = re.compile(
    r"^(.+?)\s+(\d,\d{2}\s+x\s+\d+)\s+(-?\d+,\d{2})((?:\*[A-Z])|(?:\s+[A-Z*]))?\s*$"
)
_AMOUNT_LINE = re.compile(
    r"^(.+?)(-?\d+,\d{2})((?:(?:\s+[A-Z]{3})|(?:\*[A-Z])|(?:\s+[A-Z*]))?)\s*$"
)


def normalize_item_name(description: str, art_id: str = "") -> str:
    """Turn Lidl art descriptions into human-readable product names."""
    name = html.unescape(description).strip()
    if not name:
        return name
    if art_id and name.endswith(f"-{art_id}"):
        name = name[: -(len(art_id) + 1)].rstrip()
    name = _DEPOSIT_PREFIX.sub("", name)
    return name.replace("\u00b4", "'").strip()


def extract_items(html_receipt: str) -> list[str]:
    """Unique product names from receipt HTML data attributes."""
    if not html_receipt:
        return []
    seen: set[str] = set()
    items: list[str] = []
    for match in _ARTICLE.finditer(html_receipt):
        art_id = match.group(1)
        name = normalize_item_name(match.group(2), art_id)
        if name and name not in seen:
            seen.add(name)
            items.append(name)
    return items


def extract_currency(html_receipt: str) -> dict[str, str | None]:
    """Currency symbol and ISO code from receipt HTML."""
    if not html_receipt:
        return {"symbol": None, "code": None}
    symbol = None
    code = None
    match = re.search(r'data-currency="([^"]*)"', html_receipt)
    if match:
        symbol = html.unescape(match.group(1)).strip() or None
    match = re.search(
        r'class="currency(?:\s+css_bold)?"[^>]*>([A-Z]{3})</span>',
        html_receipt,
    )
    if match:
        code = match.group(1)
    return {"symbol": symbol, "code": code}


def receipt_record(ticket: dict[str, Any]) -> dict[str, Any]:
    """Build a search-index record from a full ticket API response."""
    store = ticket.get("store") or {}
    html_receipt = ticket.get("htmlPrintedReceipt") or ""
    items = extract_items(html_receipt)
    currency = extract_currency(html_receipt)
    store_name = store.get("name") or ""
    address = ", ".join(
        part
        for part in (
            store.get("address"),
            " ".join(filter(None, (store.get("postalCode"), store.get("locality")))),
        )
        if part
    )
    locality = store.get("locality") or ""
    postal_code = store.get("postalCode") or ""
    street = store.get("address") or ""
    date_raw = ticket.get("date") or ""
    date_display = date_raw[:10] if len(date_raw) >= 10 else date_raw
    total = ticket.get("totalAmount")
    text_parts = [
        ticket.get("id", ""),
        date_display,
        store_name,
        street,
        locality,
        postal_code,
        *items,
    ]
    if total is not None:
        text_parts.append(str(total))
    return {
        "id": ticket["id"],
        "date": date_display,
        "datetime": date_raw,
        "store": store_name,
        "address": address,
        "locality": locality,
        "postalCode": postal_code,
        "street": street,
        "total": total,
        "currency": currency["symbol"],
        "currencyCode": currency["code"],
        "articlesCount": ticket.get("articlesCount") or len(items),
        "items": items,
        "text": " ".join(text_parts).lower(),
    }


def build_product_index(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate products across receipts with purchase history."""
    by_name: dict[str, list[dict[str, str]]] = {}
    for row in records:
        for item in row.get("items") or []:
            by_name.setdefault(item, []).append(
                {
                    "receiptId": row["id"],
                    "date": row["date"],
                    "store": row.get("store") or "",
                }
            )
    products = []
    for name, purchases in by_name.items():
        purchases.sort(key=lambda entry: entry["date"], reverse=True)
        products.append({"name": name, "count": len(purchases), "purchases": purchases})
    products.sort(key=lambda entry: entry["name"].casefold())
    return products


class _SpanCollector(HTMLParser):
    """Collect flat span elements from Lidl receipt HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.spans: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "span":
            return
        data = {key: value or "" for key, value in attrs}
        self._current = {
            "id": data.get("id", ""),
            "class": data.get("class", ""),
            "text": "",
        }

    def handle_endtag(self, tag: str) -> None:
        if tag == "span" and self._current is not None:
            self.spans.append(self._current)
            self._current = None

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current["text"] += data


def _group_spans(html_receipt: str) -> list[tuple[str, list[dict[str, str]]]]:
    """Merge spans by id in document order."""
    if not html_receipt:
        return []
    parser = _SpanCollector()
    parser.feed(html_receipt)
    groups: dict[str, list[dict[str, str]]] = {}
    order: list[str] = []
    for span in parser.spans:
        span_id = span["id"]
        if not span_id:
            continue
        if span_id not in groups:
            groups[span_id] = []
            order.append(span_id)
        groups[span_id].append(span)
    return [(span_id, groups[span_id]) for span_id in order]


def _span_classes(spans: list[dict[str, str]]) -> set[str]:
    classes: set[str] = set()
    for span in spans:
        if span.get("class"):
            classes.update(span["class"].split())
    return classes


def _merge_span_text(spans: list[dict[str, str]]) -> str:
    return html.unescape("".join(span.get("text", "") for span in spans))


def _amount_start(amount: str) -> int:
    return _RECEIPT_AMOUNT_END - len(amount) + 1


def _split_amount_line(text: str) -> tuple[str, str, str] | None:
    """Split a receipt row into label, trailing amount, and tax suffix."""
    unit_match = _UNIT_LINE.match(text)
    if unit_match:
        name, unit_expr, amount, suffix = unit_match.groups()
        label = name.rstrip().ljust(_UNIT_EXPR_COL) + unit_expr.strip()
        return label, amount, suffix or ""
    match = _AMOUNT_LINE.match(text)
    if match:
        left, amount, suffix = match.groups()
        return left.rstrip(), amount, suffix or ""
    return None


def _format_kg_subline(text: str) -> str:
    """Indent weight breakdown slightly from the left margin."""
    return (" " * _KG_SUBLINE_INDENT) + text.strip()


def _is_kg_subline(text: str) -> bool:
    return bool(_KG_SUBLINE.match(text.strip()))


def _pad_receipt_line(text: str, width: int = _RECEIPT_LINE_WIDTH) -> str:
    """Center footer/tender labels and stretch rule lines to receipt width."""
    stripped = text.strip()
    if not stripped:
        return " " * width
    if stripped.replace("-", "") == "":
        return stripped[0] * width
    if len(stripped) >= width:
        return stripped
    pad = width - len(stripped)
    left = pad // 2
    return " " * left + stripped + " " * (pad - left)


def normalize_receipt_line(text: str, role: str = "line") -> str:
    """Lay out receipt rows on fixed unit-price and amount columns."""
    if role == "kg_detail" or _is_kg_subline(text):
        return _format_kg_subline(text)
    if _KG_LINE.search(text):
        return _format_kg_subline(text)

    parts = _split_amount_line(text)
    if not parts:
        return text
    label, amount, suffix = parts
    if re.fullmatch(r"\s+[A-Z]{3}", suffix or ""):
        amount = f"{amount}{suffix.strip()}"
        suffix = ""
    amount_start = _amount_start(amount)
    if role == "discount":
        label = f"     {label.lstrip()}"
    elif role in ("summary", "tender"):
        label = label.lstrip()
    return label.ljust(amount_start) + amount + suffix


def _trim_barcode_svg(svg: str) -> str:
    """Drop reserved text band from python-barcode SVG (write_text=False)."""
    max_bottom = 0.0
    for match in re.finditer(
        r'<rect x="[\d.]+mm" y="([\d.]+)mm" width="([\d.]+)mm" height="([\d.]+)mm"',
        svg,
    ):
        y_mm, width_mm, height_mm = match.groups()
        if float(width_mm) > 30:
            continue
        max_bottom = max(max_bottom, float(y_mm) + float(height_mm))
    if max_bottom <= 0:
        return svg
    new_height = max_bottom + 1.0
    svg = re.sub(
        r'(<svg[^>]*\sheight=")[\d.]+mm(")',
        rf"\g<1>{new_height:.3f}mm\2",
        svg,
        count=1,
    )
    return svg


def render_barcode_data_url(code: str) -> str | None:
    """Render return code as inline SVG data URL for the SPA."""
    if not code:
        return None
    try:
        import base64
        from io import BytesIO

        logging.getLogger("pyBarcode").setLevel(logging.ERROR)
        from barcode import Code128
        from barcode.writer import SVGWriter

        writer = SVGWriter()
        writer.set_options(
            {
                "module_width": 1.0,
                "module_height": 40,
                "quiet_zone": 4,
                "write_text": False,
            }
        )
        buffer = BytesIO()
        Code128(code, writer=writer).write(buffer)
        svg = buffer.getvalue().decode().strip()
        svg = re.sub(r"<text[^>]*>.*?</text>", "", svg, flags=re.DOTALL)
        svg = _trim_barcode_svg(svg)
        encoded = base64.standard_b64encode(svg.encode()).decode()
        return f"data:image/svg+xml;base64,{encoded}"
    except Exception:
        return None


def _should_skip_line(span_id: str, classes: set[str], text: str) -> bool:
    if span_id.startswith("return_code_line_") and not text.strip():
        return True
    if span_id.startswith("header_line_") and not text.strip():
        return True
    if text.strip():
        if span_id.startswith("purchase_list_line_") and "currency" in classes:
            if text.strip() in {"EUR", "€"}:
                return True
        return False
    # Keep intentional blank lines (spacing in summary / tax / card slip blocks).
    return not span_id.startswith(
        ("purchase_summary_", "vat_info_line_", "purchase_tender_information_", "footer_line_")
    )


def _classify_line(span_id: str, classes: set[str], text: str) -> dict[str, Any]:
    bold = "css_bold" in classes
    role = "line"
    kind: str | None = None

    if span_id.startswith("header_line_"):
        role = "header"
    elif span_id.startswith("footer_line_"):
        role = "footer"
    elif span_id.startswith("purchase_tender_information_"):
        role = "tender"
    elif span_id.startswith("return_code_line_"):
        role = "meta"
    elif span_id.startswith("purchase_summary_"):
        role = "summary"
    elif span_id.startswith("vat_info_line_"):
        if _SAVINGS.search(text):
            role = "savings"
            kind = "lidl_plus"
        else:
            role = "summary"
    elif span_id.startswith("purchase_list_line_"):
        if _is_kg_subline(text):
            role = "kg_detail"
        elif "article" in classes:
            role = "item"
        elif "discount" in classes:
            role = "discount"
            kind = "lidl_plus" if _LIDL_PLUS.search(text) else "other"
        else:
            role = "discount"
            kind = "plain"

    line: dict[str, Any] = {"text": text, "role": role}
    if kind:
        line["kind"] = kind
    if bold:
        line["bold"] = True
    return line


def parse_receipt_lines(html_receipt: str) -> list[dict[str, Any]]:
    """Parse htmlPrintedReceipt into ordered view-model lines."""
    lines: list[dict[str, Any]] = []
    for span_id, group in _group_spans(html_receipt):
        classes = _span_classes(group)
        text = _merge_span_text(group)
        if _should_skip_line(span_id, classes, text):
            continue
        line = _classify_line(span_id, classes, text)
        role = line["role"]
        if role in ("footer", "tender"):
            if _split_amount_line(text):
                line["text"] = normalize_receipt_line(text, role)
            else:
                line["text"] = _pad_receipt_line(text)
        elif span_id.startswith(("purchase_list_line_", "purchase_summary_")):
            line["text"] = normalize_receipt_line(text, role)
        lines.append(line)
    return lines


def build_receipt_view(ticket: dict[str, Any]) -> dict[str, Any]:
    """Build a lazy-loaded receipt view model for the SPA."""
    html_receipt = ticket.get("htmlPrintedReceipt") or ""
    currency = extract_currency(html_receipt)
    return {
        "id": ticket["id"],
        "logo": ticket.get("logoUrl"),
        "barcode": ticket.get("barCode"),
        "barcodeImage": render_barcode_data_url(ticket.get("barCode") or ""),
        "currency": currency["symbol"],
        "currencyCode": currency["code"],
        "lines": parse_receipt_lines(html_receipt),
    }
