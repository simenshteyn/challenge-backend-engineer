"""Tests for the JSON-backed order store."""

from __future__ import annotations

import pytest

from portal.services.order_store import _load_raw_orders, find_order, get_order

# Discover order numbers at import time so adding a new entry to
# `orders_raw.json` is automatically validated by the round-trip test
# below. Reusing the loader avoids duplicating the data path.
_ALL_ORDER_NUMBERS = [str(raw["order_number"]) for raw in _load_raw_orders()]


class TestFindOrder:
    """`find_order` is the auth boundary — pin the credential checks directly.

    Indirect coverage via the views isn't enough: SEC-001 was an IDOR in the
    API session check, but the same auth model lives in `find_order`. Direct
    tests here mean a refactor of either side surfaces a clear failure.
    """

    def test_valid_email_returns_order(self) -> None:
        order = find_order("RMA-1001", "alex@example.com")
        assert order is not None
        assert order.order_number == "RMA-1001"

    def test_valid_zip_returns_order(self) -> None:
        order = find_order("RMA-1001", "10115")
        assert order is not None
        assert order.order_number == "RMA-1001"

    def test_wrong_email_returns_none(self) -> None:
        assert find_order("RMA-1001", "attacker@example.com") is None

    def test_wrong_zip_returns_none(self) -> None:
        assert find_order("RMA-1001", "00000") is None

    def test_unknown_order_returns_none(self) -> None:
        assert find_order("RMA-9999", "alex@example.com") is None

    def test_credentials_from_other_order_do_not_match(self) -> None:
        """A real customer's email/zip must not unlock a different customer's
        order — even though both are valid identifiers in isolation."""
        assert find_order("RMA-1001", "lee@example.com") is None
        assert find_order("RMA-1001", "80331") is None

    def test_empty_identifier_returns_none(self) -> None:
        """The form layer rejects empty identifiers, but pin the contract
        at the function boundary too — an empty string must never match."""
        assert find_order("RMA-1001", "") is None

    def test_whitespace_only_identifier_returns_none(self) -> None:
        """After normalisation, a whitespace-only string is empty."""
        assert find_order("RMA-1001", "   ") is None


class TestFindOrderNormalisation:
    """OPEN-001: identifiers should be case-insensitive and whitespace-tolerant.
    Customers reasonably expect `Alex@Example.com` and `alex@example.com` to
    match the same record."""

    def test_uppercase_email_matches(self) -> None:
        order = find_order("RMA-1001", "ALEX@EXAMPLE.COM")
        assert order is not None
        assert order.order_number == "RMA-1001"

    def test_mixed_case_email_matches(self) -> None:
        order = find_order("RMA-1001", "Alex@Example.com")
        assert order is not None

    def test_whitespace_padded_email_matches(self) -> None:
        order = find_order("RMA-1001", "  alex@example.com  ")
        assert order is not None

    def test_whitespace_padded_zip_matches(self) -> None:
        order = find_order("RMA-1001", "  10115 ")
        assert order is not None


class TestOrdersRawRoundTrip:
    """The production data file must parse and map cleanly through the
    real loader + mapper. Catches regressions when someone adds a new
    order with a typo'd field name or missing required value."""

    @pytest.mark.parametrize("order_number", _ALL_ORDER_NUMBERS)
    def test_order_loads_with_required_shape(self, order_number: str) -> None:
        order = get_order(order_number)
        assert order is not None
        assert order.order_number == order_number
        assert order.email
        assert order.recipient
        assert order.zip
        assert order.delivery_date
        assert order.articles, "every order should have at least one article"
        for article in order.articles:
            assert article.sku
            assert article.name
            assert article.quantity >= 1
            assert article.price >= 0
