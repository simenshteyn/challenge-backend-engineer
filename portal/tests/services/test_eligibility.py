"""Tests for the eligibility engine.

This is a starting point — not exhaustive.  You are expected to add tests
that cover your rules and edge cases.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import TypedDict, Unpack

import pytest
from pydantic import ValidationError

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

    def test_category_specific_window_applies(self, tmp_path: Path) -> None:
        """An item in a category with a shorter window expires sooner."""
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(
            "rules:\n"
            "  - type: return_window\n"
            "    days: 30\n"
            "    category_windows:\n"
            "      electronics: 14\n"
            "    reason: 'expired ({days} days)'\n"
        )
        order = _make_order(
            delivery_date=datetime.now() - timedelta(days=20),
            articles=[_make_article(category="electronics")],
        )
        result = evaluate_eligibility(order, rules_path=rules_file)[0]
        assert result.returnable is False
        assert "14" in result.reason

    def test_unmapped_category_falls_back_to_default(
        self, tmp_path: Path
    ) -> None:
        """Categories not listed in `category_windows` use the default `days`."""
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(
            "rules:\n"
            "  - type: return_window\n"
            "    days: 30\n"
            "    category_windows:\n"
            "      electronics: 14\n"
            "    reason: 'expired ({days} days)'\n"
        )
        order = _make_order(
            delivery_date=datetime.now() - timedelta(days=20),
            articles=[_make_article(category="apparel")],
        )
        result = evaluate_eligibility(order, rules_path=rules_file)[0]
        # 20 days ago, default 30-day window — still returnable
        assert result.returnable is True

    def test_empty_category_falls_back_to_default(self, tmp_path: Path) -> None:
        """Articles the mapper couldn't categorise still get the default."""
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(
            "rules:\n"
            "  - type: return_window\n"
            "    days: 30\n"
            "    category_windows:\n"
            "      electronics: 14\n"
            "    reason: 'expired ({days} days)'\n"
        )
        order = _make_order(
            delivery_date=datetime.now() - timedelta(days=40),
            articles=[_make_article(category="")],
        )
        result = evaluate_eligibility(order, rules_path=rules_file)[0]
        # No category override, default 30-day window, 40 days ago — expired
        assert result.returnable is False
        assert "30" in result.reason

    def test_category_override_inside_short_window_is_returnable(
        self, tmp_path: Path
    ) -> None:
        """An apparel item 20 days post-delivery with a 30-day window stays
        returnable even if a sibling category has a shorter window."""
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(
            "rules:\n"
            "  - type: return_window\n"
            "    days: 30\n"
            "    category_windows:\n"
            "      electronics: 14\n"
            "      apparel: 60\n"
            "    reason: 'expired ({days} days)'\n"
        )
        order = _make_order(
            delivery_date=datetime.now() - timedelta(days=45),
            articles=[
                _make_article(category="apparel"),
                _make_article(category="electronics"),
            ],
        )
        results = evaluate_eligibility(order, rules_path=rules_file)
        assert results[0].returnable is True  # apparel: 60-day window
        assert results[1].returnable is False  # electronics: 14-day, 45 ago
        assert "14" in results[1].reason

    def test_negative_days_rejected(self, tmp_path: Path) -> None:
        """`days` must be >= 1 — a negative window is almost always a typo."""
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(
            "rules:\n"
            "  - type: return_window\n"
            "    days: -7\n"
            "    reason: x\n"
        )
        order = _make_order(articles=[_make_article()])
        with pytest.raises(ValidationError):
            evaluate_eligibility(order, rules_path=rules_file)

    def test_negative_category_window_rejected(self, tmp_path: Path) -> None:
        """Per-category overrides must also be >= 1."""
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(
            "rules:\n"
            "  - type: return_window\n"
            "    days: 30\n"
            "    category_windows:\n"
            "      electronics: -7\n"
            "    reason: x\n"
        )
        order = _make_order(articles=[_make_article()])
        with pytest.raises(ValidationError):
            evaluate_eligibility(order, rules_path=rules_file)

    def test_uppercase_category_key_rejected(self, tmp_path: Path) -> None:
        """Mapper normalises to lowercase — config must match."""
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(
            "rules:\n"
            "  - type: return_window\n"
            "    days: 30\n"
            "    category_windows:\n"
            "      Electronics: 14\n"  # capital E — would silently miss
            "    reason: x\n"
        )
        order = _make_order(articles=[_make_article()])
        with pytest.raises(ValidationError):
            evaluate_eligibility(order, rules_path=rules_file)

    def test_unknown_key_rejected(self, tmp_path: Path) -> None:
        """A typo'd field should fail loudly, not silently no-op."""
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(
            "rules:\n"
            "  - type: return_window\n"
            "    days: 30\n"
            "    categori_windows:\n"  # typo of category_windows
            "      electronics: 14\n"
            "    reason: x\n"
        )
        order = _make_order(articles=[_make_article()])
        with pytest.raises(ValidationError):
            evaluate_eligibility(order, rules_path=rules_file)

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
