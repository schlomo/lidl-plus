"""Product name extraction from Lidl receipt HTML.

See AGENTS.md § TDD (required) — tests call lidlplus.archive.receipt directly.
"""

from __future__ import annotations

import html

from lidlplus.archive.receipt import extract_items, normalize_item_name


def test_normalize_deposit_grille_line():
    assert normalize_item_name("1€ DP Grille-0532217", "0532217") == "Grille"


def test_normalize_art_id_suffix():
    assert normalize_item_name("Heißgetränke-0485258", "0485258") == "Heißgetränke"


def test_normalize_html_entity_in_attribute():
    assert normalize_item_name("Norweg. R&auml;ucherlachs") == "Norweg. Räucherlachs"


def test_normalize_acute_accent_to_apostrophe():
    assert normalize_item_name("Ben & Jerry´s Peanut") == "Ben & Jerry's Peanut"


def test_extract_items_uses_utf8_attribute_not_entity_text():
    receipt_html = (
        '<span data-art-id="001" data-art-description="Bio Möhren">Bio M&ouml;hren</span>'
        '<span data-art-id="0532217" data-art-description="1€ DP Grille-0532217">1&euro; DP Grille-0532217</span>'
    )
    assert extract_items(receipt_html) == ["Bio Möhren", "Grille"]


def test_pfand_line_unchanged():
    assert normalize_item_name("Pfand 0,25 7% M", "0001950") == "Pfand 0,25 7% M"
