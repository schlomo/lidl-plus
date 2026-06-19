"""Receipt view model parsing (htmlPrintedReceipt → SPA lines)."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import pytest

from lidlplus.archive.receipt import (
    _group_spans,
    _merge_span_text,
    build_receipt_view,
    normalize_receipt_line,
    parse_receipt_lines,
    render_barcode_data_url,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOLDEN = json.loads((FIXTURES / "receipt_line_golden.json").read_text(encoding="utf-8"))

SAMPLE_HTML = (
    '<html><body><pre>'
    '<span id="header_line_1" class="css_bold">Bonkopie</span>'
    '<span id="purchase_list_line_1" class="currency" data-currency="€">    </span>'
    '<span id="purchase_list_line_1" class="currency css_bold" data-currency="€">EUR</span>'
    '<span id="purchase_list_line_2" class="article css_bold" '
    'data-art-id="0080000" data-art-description="Banane lose">Banane lose</span>'
    '<span id="purchase_list_line_2" class="article" data-art-id="0080000" '
    'data-art-description="Banane lose">                  </span>'
    '<span id="purchase_list_line_2" class="article css_bold" data-art-id="0080000" '
    'data-art-description="Banane lose">      1,17 A</span>'
    '<span id="purchase_list_line_3" class="discount css_bold">     Lidl Plus Rabatt             -0,48</span>'
    '<span id="purchase_list_line_4">Preisvorteil                       -1,50</span>'
    '<span id="purchase_summary_2" class="css_bold">zu zahlen                        165,17</span>'
    '<span id="vat_info_line_8" class="css_bold">¦           0,48 EUR gespart           ¦</span>'
    "</pre></body></html>"
)

_AMOUNT = re.compile(r"-?\d+,\d{2}")


def _amount_end_column(text: str) -> int | None:
    matches = list(_AMOUNT.finditer(text))
    if not matches:
        return None
    last = matches[-1]
    return last.start() + len(last.group()) - 1


def test_normalize_receipt_line_aligns_amount():
    raw = "Bread                              1,99 A"
    normalized = normalize_receipt_line(raw, role="item")
    assert normalized.endswith("1,99 A")
    assert _amount_end_column(normalized) == 38


def test_normalize_receipt_line_skips_kg_lines():
    line = "  0,908 kg x 1,29   EUR/kg"
    assert normalize_receipt_line(line, role="kg_detail") == (" " * 3) + "0,908 kg x 1,29   EUR/kg"


def test_parse_receipt_lines_classifies_items_and_discounts():
    lines = parse_receipt_lines(SAMPLE_HTML)
    roles = [(line["role"], line.get("kind")) for line in lines]

    assert ("header", None) in roles
    assert ("item", None) in roles
    assert ("discount", "lidl_plus") in roles
    assert ("discount", "plain") in roles
    assert ("summary", None) in roles
    assert ("savings", "lidl_plus") in roles
    assert ("currency", None) not in roles


def test_parse_receipt_lines_skips_lone_eur_marker():
    lines = parse_receipt_lines(SAMPLE_HTML)
    assert all("EUR" != line["text"].strip() for line in lines)


def test_normalize_receipt_line_unit_qty_layout():
    raw = "Heidelbeeren 4,39 x   2    8,78 A"
    normalized = normalize_receipt_line(raw, role="item")
    assert normalized.startswith("Heidelbeeren          4,39 x   2")
    assert normalized.endswith("8,78 A")
    assert _amount_end_column(normalized) == 38


def test_build_receipt_view_includes_barcode_image():
    ticket = {
        "id": "t1",
        "logoUrl": "https://example.com/logo.png",
        "barCode": "0888193411850202280224",
        "htmlPrintedReceipt": SAMPLE_HTML,
    }
    view = build_receipt_view(ticket)
    assert view["id"] == "t1"
    assert view["logo"] == "https://example.com/logo.png"
    assert view["barcode"] == "0888193411850202280224"
    assert view["barcodeImage"] is not None
    assert view["barcodeImage"].startswith("data:image/svg+xml;base64,")
    assert view["lines"]
    assert any(line["role"] == "item" for line in view["lines"])


def test_render_barcode_data_url_omits_svg_text():
    url = render_barcode_data_url("0888193419047501200723")
    assert url is not None
    assert url.startswith("data:image/svg+xml;base64,")
    svg = base64.b64decode(url.split(",", 1)[1]).decode()
    assert "<text" not in svg
    height = float(re.search(r'height="([\d.]+)mm"', svg).group(1))
    assert height < 20


def test_golden_receipt_line_layout() -> None:
    archive = Path(__file__).resolve().parents[1] / "data" / "archive" / "receipts"
    ticket_path = archive / f"{GOLDEN['receipt_id']}.json"
    if not ticket_path.is_file():
        pytest.skip("local archive fixture not available")
    ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
    lines = parse_receipt_lines(ticket["htmlPrintedReceipt"])
    texts = {line["text"] for line in lines}
    for key, expected in GOLDEN["aligned_lines"].items():
        assert expected in texts, f"{key}: missing aligned line {expected!r}"


def test_purchase_lines_share_amount_column() -> None:
    archive = Path(__file__).resolve().parents[1] / "data" / "archive" / "receipts"
    ticket_path = archive / f"{GOLDEN['receipt_id']}.json"
    if not ticket_path.is_file():
        pytest.skip("local archive fixture not available")
    ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
    lines = parse_receipt_lines(ticket["htmlPrintedReceipt"])
    ends = {
        _amount_end_column(line["text"])
        for line in lines
        if line["role"] in {"item", "discount"}
        and line["text"].strip()
        and len(_AMOUNT.findall(line["text"])) == 1
    }
    assert ends == {GOLDEN["amount_column"]}


def test_kg_subline_indented_to_unit_column() -> None:
    archive = Path(__file__).resolve().parents[1] / "data" / "archive" / "receipts"
    ticket_path = archive / f"{GOLDEN['receipt_id']}.json"
    if not ticket_path.is_file():
        pytest.skip("local archive fixture not available")
    ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
    lines = parse_receipt_lines(ticket["htmlPrintedReceipt"])
    kg_lines = [line for line in lines if line.get("role") == "kg_detail"]
    assert kg_lines
    for line in kg_lines:
        assert line["text"].startswith(" " * GOLDEN["kg_detail_prefix_spaces"])


def test_parse_preserves_blank_spacing_lines() -> None:
    archive = Path(__file__).resolve().parents[1] / "data" / "archive" / "receipts"
    ticket_path = archive / f"{GOLDEN['receipt_id']}.json"
    if not ticket_path.is_file():
        pytest.skip("local archive fixture not available")
    ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
    lines = parse_receipt_lines(ticket["htmlPrintedReceipt"])
    blank_count = sum(1 for line in lines if not line["text"])
    assert blank_count >= GOLDEN["blank_line_count_min"]
