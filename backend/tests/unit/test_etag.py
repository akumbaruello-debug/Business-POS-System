"""ETag / If-Match concurrency helper tests.

Covers the M1 helper behavior:
* ETag generation/parsing
* If-Match parsing
* Version mismatch behavior
* Malformed values
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.concurrency import check_if_match, make_etag, strip_quotes
from app.errors import InvalidHeader, VersionMismatch
from app.util import format_etag, parse_etag
from app.validation.headers import parse_if_match


class TestMakeETag:
    """make_etag helper tests."""

    def test_strong_etag_format(self) -> None:
        """ETag is quoted ISO-8601."""
        ts = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        etag = make_etag(ts)
        # Strong etag = quoted value (no W/ prefix)
        assert etag.startswith('"')
        assert etag.endswith('"')
        assert "W/" not in etag

    def test_iso8601_value(self) -> None:
        """Inner value is ISO-8601."""
        ts = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        etag = make_etag(ts)
        inner = strip_quotes(etag)
        # Should round-trip
        parsed = datetime.fromisoformat(inner)
        assert parsed == ts


class TestParseEtag:
    """parse_etag helper tests."""

    def test_strong(self) -> None:
        """Strong ETag (no W/ prefix)."""
        result = parse_etag('"2025-01-01T00:00:00+00:00"')
        assert result == "2025-01-01T00:00:00+00:00"

    def test_weak(self) -> None:
        """Weak ETag (W/ prefix)."""
        result = parse_etag('W/"2025-01-01T00:00:00+00:00"')
        assert result == "2025-01-01T00:00:00+00:00"

    def test_malformed(self) -> None:
        """Malformed ETag returns None."""
        assert parse_etag("not-an-etag") is None
        assert parse_etag("2025-01-01") is None  # no quotes
        assert parse_etag("") is None


class TestFormatEtag:
    """format_etag helper tests."""

    def test_wraps_in_quotes(self) -> None:
        """format_etag adds surrounding quotes."""
        assert format_etag("2025-01-01T00:00:00") == '"2025-01-01T00:00:00"'


class TestStripQuotes:
    """strip_quotes helper tests."""

    def test_strips(self) -> None:
        """Quoted string has its quotes removed."""
        assert strip_quotes('"abc"') == "abc"

    def test_unquoted_unchanged(self) -> None:
        """Unquoted string passes through."""
        assert strip_quotes("abc") == "abc"

    def test_single_quote_no_op(self) -> None:
        """Single quote not stripped (no matching pair)."""
        assert strip_quotes('"abc') == '"abc'

    def test_short_quoted(self) -> None:
        """A string of 1 char (just quotes) → empty."""
        assert strip_quotes('""') == ""


class TestCheckIfMatch:
    """check_if_match helper tests."""

    def test_none_skips_check(self) -> None:
        """No If-Match → no check."""
        # Should not raise
        check_if_match(provided=None, current_etag='"abc"')

    def test_match_passes(self) -> None:
        """Matching ETag → no raise."""
        check_if_match(provided='"abc"', current_etag='"abc"')

    def test_mismatch_raises(self) -> None:
        """Mismatched ETag → VersionMismatch."""
        with pytest.raises(VersionMismatch):
            check_if_match(provided='"abc"', current_etag='"xyz"')


class TestParseIfMatch:
    """parse_if_match header parser tests."""

    def test_absent_returns_none(self) -> None:
        """Missing If-Match → None."""
        assert parse_if_match(None) is None
        assert parse_if_match("") is None

    def test_valid_returns_parsed(self) -> None:
        """Valid If-Match → IfMatchParsed."""
        result = parse_if_match('"2025-01-01T00:00:00+00:00"')
        assert result is not None
        assert result.etag == "2025-01-01T00:00:00+00:00"
        assert result.is_weak is False

    def test_malformed_raises_invalid_header(self) -> None:
        """Malformed If-Match → InvalidHeader."""
        with pytest.raises(InvalidHeader):
            parse_if_match("not-an-etag")

    def test_weak_prefix(self) -> None:
        """Weak ETag is supported."""
        result = parse_if_match('W/"2025-01-01T00:00:00+00:00"')
        assert result is not None
        assert result.etag == "2025-01-01T00:00:00+00:00"
