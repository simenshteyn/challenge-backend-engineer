"""Tests for the eligibility engine.

This is a starting point — not exhaustive.  You are expected to add tests
that cover your rules and edge cases.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import TypedDict, Unpack

from portal.services.eligibility import evaluate_eligibility
from portal.types import Article, Order


class _ArticleData(TypedDict):
    sku: str
    name: str
    quantity: int
    quantity_returned: int
    price: float
    is_digital: bool
    is_final_sale: bool
    category: str


class _ArticleOverrides(TypedDict, total=False):
    sku: str
    name: str
    quantity: int
    quantity_returned: int
    price: float
    is_digital: bool
    is_final_sale: bool
    category: str


def _make_order(
    articles: list[Article],
    delivery_date: datetime | None = None,
) -> Order:
    return Order(
        order_number="TEST-001",
        email="test@example.com",
        recipient="Test User",
        zip="12345",
        street="Test Street 1",
        city="Testville",
        order_date=datetime(2025, 12, 1, 10, 0),
        delivery_date=delivery_date or datetime(2025, 12, 5, 14, 0),
        articles=articles,
    )


def _make_article(**overrides: Unpack[_ArticleOverrides]) -> Article:
    defaults: _ArticleData = {
        "sku": "TEST-SKU",
        "name": "Test Article",
        "quantity": 1,
        "quantity_returned": 0,
        "price": 19.99,
        "is_digital": False,
        "is_final_sale": False,
        "category": "general",
    }
    defaults.update(overrides)
    return Article(**defaults)


class TestDigitalItems:
    """Digital items should not be returnable."""

    def test_digital_item_is_not_returnable(self) -> None:
        order = _make_order(
            articles=[
                _make_article(sku="EBOOK-01", name="E-Book", is_digital=True),
            ]
        )
        results = evaluate_eligibility(order)
        assert results[0].returnable is False


class TestAlreadyReturned:
    """Fully returned items should not be returnable."""

    def test_fully_returned_is_not_returnable(self) -> None:
        order = _make_order(
            articles=[
                _make_article(quantity=1, quantity_returned=1),
            ]
        )
        results = evaluate_eligibility(order)
        assert results[0].returnable is False

    def test_partially_returned_is_still_returnable(self) -> None:
        """An item with remaining quantity should still be returnable."""
        order = _make_order(
            delivery_date=datetime.now() - timedelta(days=5),
            articles=[_make_article(quantity=3, quantity_returned=1)],
        )
        results = evaluate_eligibility(order)
        assert results[0].returnable is True


class TestReturnWindow:
    """Items past the return window should not be returnable."""

    def test_expired_window_is_not_returnable(self) -> None:
        """Delivery 100 days ago — clearly outside any reasonable window."""
        order = _make_order(
            delivery_date=datetime.now() - timedelta(days=100),
            articles=[_make_article()],
        )
        results = evaluate_eligibility(order)
        assert results[0].returnable is False

    def test_recent_delivery_is_returnable(self) -> None:
        """Delivery 5 days ago — well within a typical return window."""
        order = _make_order(
            delivery_date=datetime.now() - timedelta(days=5),
            articles=[_make_article()],
        )
        results = evaluate_eligibility(order)
        assert results[0].returnable is True


class TestRegularItem:
    """A regular, non-digital, non-final-sale item within the return window
    should be returnable."""

    def test_regular_item_is_returnable(self) -> None:
        order = _make_order(
            delivery_date=datetime.now() - timedelta(days=5),
            articles=[_make_article()],
        )
        results = evaluate_eligibility(order)
        assert results[0].returnable is True


class TestFinalSaleItems:
    """Final-sale items should not be returnable."""

    def test_final_sale_item_is_not_returnable(self) -> None:
        order = _make_order(
            articles=[_make_article(is_final_sale=True)],
        )
        results = evaluate_eligibility(order)
        assert results[0].returnable is False


class TestMatchedRule:
    """The `matched_rule` field should carry a stable rule identifier."""

    def test_digital_match_emits_rule_id(self) -> None:
        order = _make_order(articles=[_make_article(is_digital=True)])
        result = evaluate_eligibility(order)[0]
        assert result.matched_rule == "digital"
        assert result.reason  # human-readable, non-empty

    def test_returnable_item_has_empty_match(self) -> None:
        order = _make_order(
            delivery_date=datetime.now() - timedelta(days=5),
            articles=[_make_article()],
        )
        result = evaluate_eligibility(order)[0]
        assert result.matched_rule == ""
        assert result.reason == ""


class TestRuleOrdering:
    """First matching rule wins — no double-counting."""

    def test_fully_returned_takes_precedence_over_window(
        self, tmp_path: Path
    ) -> None:
        """An already-returned item should report `fully_returned`, not
        `return_window`, even when both would match."""
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(
            "rules:\n"
            "  - type: fully_returned\n"
            "    reason: already returned\n"
            "  - type: return_window\n"
            "    days: 30\n"
            "    reason: window expired\n"
        )
        order = _make_order(
            delivery_date=datetime.now() - timedelta(days=100),
            articles=[_make_article(quantity=1, quantity_returned=1)],
        )
        result = evaluate_eligibility(order, rules_path=rules_file)[0]
        assert result.matched_rule == "fully_returned"


class TestCustomRulesConfig:
    """The engine should accept a custom rules file (used by BR-004 etc.)."""

    def test_custom_window_days(self, tmp_path: Path) -> None:
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(
            "rules:\n"
            "  - type: return_window\n"
            "    days: 7\n"
            "    reason: short window\n"
        )
        order = _make_order(
            delivery_date=datetime.now() - timedelta(days=10),
            articles=[_make_article()],
        )
        result = evaluate_eligibility(order, rules_path=rules_file)[0]
        assert result.returnable is False
        assert result.matched_rule == "return_window"

    def test_now_override(self, tmp_path: Path) -> None:
        """An injected `now` lets us test windows deterministically."""
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(
            "rules:\n"
            "  - type: return_window\n"
            "    days: 30\n"
            "    reason: expired\n"
        )
        order = _make_order(
            delivery_date=datetime(2025, 1, 1),
            articles=[_make_article()],
        )
        # 10 days after delivery — within window
        result = evaluate_eligibility(
            order, rules_path=rules_file, now=datetime(2025, 1, 11)
        )[0]
        assert result.returnable is True
        # 100 days after delivery — outside window
        result = evaluate_eligibility(
            order, rules_path=rules_file, now=datetime(2025, 4, 11)
        )[0]
        assert result.returnable is False
