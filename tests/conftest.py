from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from lidlplus.archive.site import _content_hash

SAMPLE_HTML = (
    '<html><body><pre>'
    '<span data-art-id="0000001" data-art-description="Bread">Bread</span>'
    '<span id="purchase_list_line_2" class="article css_bold" '
    'data-art-id="0000001" data-art-description="Bread">Bread</span>'
    '<span id="purchase_list_line_2" class="article" data-art-id="0000001" '
    'data-art-description="Bread">                  </span>'
    '<span id="purchase_list_line_2" class="article css_bold" data-art-id="0000001" '
    'data-art-description="Bread">      1,99 A</span>'
    "</pre></body></html>"
)

SAMPLE_TICKET = {
    "id": "t1",
    "date": "2024-01-15T10:00:00",
    "totalAmount": 1.99,
    "logoUrl": "https://example.com/logo.png",
    "barCode": "0888193411850202280224",
    "store": {
        "name": "Lidl Test",
        "address": "Main St",
        "locality": "Berlin",
        "postalCode": "10115",
    },
    "htmlPrintedReceipt": SAMPLE_HTML,
}


def load_archive_payload(www: Path) -> dict:
    data = next(www.glob("*.data.json.gz"))
    return json.loads(gzip.decompress(data.read_bytes()))


def load_receipt_view(www: Path, filename: str) -> dict:
    return json.loads(gzip.decompress((www / filename).read_bytes()))


def _assert_content_addressed(path: Path, ext: str) -> None:
    content = path.read_bytes()
    assert path.name == f"{_content_hash(content)}.{ext}"


def assert_sample_archive_www(www: Path) -> None:
    """Check www/ output: linked assets, content hashes, archive round-trip."""
    index_html = (www / "index.html").read_text(encoding="utf-8")

    data = next(www.glob("*.data.json.gz"))
    app_js = next(www.glob("*.app.js"))
    css = next(www.glob("*.css"))
    favicon = next(www.glob("*.svg"))
    receipt_files = list(www.glob("*.t1.json.gz"))

    _assert_content_addressed(data, "data.json.gz")
    _assert_content_addressed(app_js, "app.js")
    _assert_content_addressed(css, "css")
    _assert_content_addressed(favicon, "svg")
    assert len(receipt_files) == 1
    _assert_content_addressed(receipt_files[0], "t1.json.gz")

    assert data.name in index_html
    assert "window.LIDL_DATA" in index_html
    assert f'href="{css.name}"' in index_html
    assert f'src="{app_js.name}"' in index_html
    assert f'href="{favicon.name}"' in index_html

    payload = load_archive_payload(www)
    assert len(payload["r"]) == 1
    assert "h" not in payload
    receipt = payload["r"][0]
    assert receipt["id"] == "t1"
    assert receipt["store"] == "Lidl Test"
    assert receipt["items"] == ["Bread"]
    assert receipt["total"] == 1.99
    assert receipt["file"] == receipt_files[0].name

    assert payload["p"][0]["name"] == "Bread"
    assert payload["p"][0]["count"] == 1

    view = load_receipt_view(www, receipt["file"])
    assert view["id"] == "t1"
    assert view["logo"] == SAMPLE_TICKET["logoUrl"]
    assert view["barcode"] == SAMPLE_TICKET["barCode"]
    assert view["barcodeImage"] is not None
    assert any(line["role"] == "item" for line in view["lines"])


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    archive = tmp_path / "archive" / "receipts"
    archive.mkdir(parents=True)
    (archive / "t1.json").write_text(json.dumps(SAMPLE_TICKET), encoding="utf-8")
    return tmp_path
