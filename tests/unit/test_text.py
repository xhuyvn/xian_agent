"""Unit tests for text normalization, alias normalization, and diacritics stripping."""

from __future__ import annotations

import unicodedata
import pytest

from xn.text import normalize_alias, normalize_text, strip_diacritics


def test_normalize_alias_composed_and_decomposed() -> None:
    """Verify composed and decomposed forms of the same name produce identical normalized alias."""
    name_composed = "Hàn Lập"
    name_decomposed = unicodedata.normalize("NFD", name_composed)
    assert name_composed != name_decomposed

    res_composed = normalize_alias(name_composed)
    res_decomposed = normalize_alias(name_decomposed)
    assert res_composed == "hàn lập"
    assert res_decomposed == "hàn lập"
    assert res_composed == res_decomposed

    # Additional Vietnamese term with multiple marks
    pill_composed = "Trúc Cơ Đan"
    pill_decomposed = unicodedata.normalize("NFD", pill_composed)
    assert normalize_alias(pill_composed) == normalize_alias(pill_decomposed) == "trúc cơ đan"


def test_normalize_alias_case_and_whitespace_folded() -> None:
    """Verify case is folded and multiple whitespace runs collapse to a single space."""
    assert normalize_alias("  HÀN   LẬP  ") == "hàn lập"
    assert normalize_alias("Hàn\t\n  Lập") == "hàn lập"
    assert normalize_alias("  MA   HOÀNG  QUANG   ") == "ma hoàng quang"


def test_normalize_alias_surrounding_quotes_and_punctuation_stripped() -> None:
    """Verify specified surrounding quotes and punctuation characters are stripped."""
    # ASCII and unicode quotation marks: " ' “ ” ‘ ’
    assert normalize_alias('"Hàn Lập"') == "hàn lập"
    assert normalize_alias("'Hàn Lập'") == "hàn lập"
    assert normalize_alias("“Hàn Lập”") == "hàn lập"
    assert normalize_alias("‘Hàn Lập’") == "hàn lập"
    assert normalize_alias("”Hàn Lập“") == "hàn lập"
    assert normalize_alias("’Hàn Lập‘") == "hàn lập"

    # Surrounding brackets, parens, and punctuation: . , : ; ! ? ( ) [ ]
    assert normalize_alias("(Hàn Lập)") == "hàn lập"
    assert normalize_alias("[Hàn Lập]") == "hàn lập"
    assert normalize_alias(".Hàn Lập.") == "hàn lập"
    assert normalize_alias(",Hàn Lập,") == "hàn lập"
    assert normalize_alias(":Hàn Lập:") == "hàn lập"
    assert normalize_alias(";Hàn Lập;") == "hàn lập"
    assert normalize_alias("!Hàn Lập!") == "hàn lập"
    assert normalize_alias("?Hàn Lập?") == "hàn lập"

    # Nested / combined surrounding whitespace and punctuation
    assert normalize_alias('  " \' “ ( [ Hàn Lập ] ) ” \' "  ') == "hàn lập"
    assert normalize_alias("?!... Hàn Lập ...!?") == "hàn lập"

    # Internal punctuation is preserved
    assert normalize_alias("Hàn, Lập") == "hàn, lập"
    assert normalize_alias("A-B") == "a-b"


def test_normalize_alias_punctuation_only_gives_empty() -> None:
    """Verify punctuation-only, whitespace-only, and empty input return empty string."""
    assert normalize_alias("\" ' “ ” ‘ ’ . , : ; ! ? ( ) [ ]") == ""
    assert normalize_alias("...") == ""
    assert normalize_alias("  ??? !!!  ") == ""
    assert normalize_alias("   ") == ""
    assert normalize_alias("") == ""


def test_normalize_alias_idempotent() -> None:
    """Verify normalize_alias is idempotent across varied inputs."""
    samples = [
        '  " ‘ Hàn Lập ’ "  ',
        "Đại Đạo Vô Môn",
        "  [ ( Ma Đầu ) ]  ",
        "!!! ???",
        "",
        "hàn lập",
    ]
    for s in samples:
        norm1 = normalize_alias(s)
        norm2 = normalize_alias(norm1)
        norm3 = normalize_alias(norm2)
        assert norm1 == norm2 == norm3


def test_strip_diacritics_han_lap() -> None:
    """Verify strip_diacritics maps 'Hàn Lập' to 'Han Lap'."""
    assert strip_diacritics("Hàn Lập") == "Han Lap"
    assert strip_diacritics("hàn lập") == "han lap"


def test_strip_diacritics_dai_dao() -> None:
    """Verify strip_diacritics maps 'Đại Đạo' to 'Dai Dao'."""
    assert strip_diacritics("Đại Đạo") == "Dai Dao"
    assert strip_diacritics("đại đạo") == "dai dao"


def test_strip_diacritics_hoa_variants() -> None:
    """Verify strip_diacritics maps both 'hòa' and 'hoà' to 'hoa'."""
    assert strip_diacritics("hòa") == "hoa"
    assert strip_diacritics("hoà") == "hoa"
    assert strip_diacritics("hòa") == strip_diacritics("hoà")


def test_normalize_text_unchanged() -> None:
    """Verify normalize_text produces NFC only, preserving case and content."""
    # Preserves case
    assert normalize_text("Hàn Lập") == "Hàn Lập"
    assert normalize_text("HÀN LẬP") == "HÀN LẬP"

    # Normalizes NFD to NFC
    nfd_text = unicodedata.normalize("NFD", "Hàn Lập")
    nfc_text = normalize_text(nfd_text)
    assert unicodedata.is_normalized("NFC", nfc_text)
    assert nfc_text == "Hàn Lập"
