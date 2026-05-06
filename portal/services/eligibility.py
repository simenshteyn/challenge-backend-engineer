"""Return eligibility engine.

Rules are loaded from a YAML config (``portal/data/return_rules.yaml``)
and evaluated in order; the first matching rule marks an article as
not returnable.  Adding a new rule = a new ``_Rule`` subclass plus a
``type`` literal — see :class:`ReturnWindowRule` for the shape.
"""

from __future__ import annotations

import functools
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from portal.types import Article, ArticleEligibility, Order

_DEFAULT_RULES_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "return_rules.yaml"
)


class _Rule(BaseModel):
    """Common fields shared by every rule."""

    # `extra="forbid"`: typo'd YAML keys fail loudly at load time.
    # `frozen=True`: cached `RulesConfig` is shared across requests; freezing
    # prevents accidental mutation from leaking between callers.
    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: str

    def matches(self, article: Article, order: Order, now: datetime) -> bool:
        raise NotImplementedError

    def reason_for(self, article: Article) -> str:
        """Reason text for *article*. Override to interpolate dynamic values."""
        return self.reason


class FullyReturnedRule(_Rule):
    type: Literal["fully_returned"]

    def matches(self, article: Article, order: Order, now: datetime) -> bool:
        return article.quantity_returned >= article.quantity


class DigitalRule(_Rule):
    type: Literal["digital"]

    def matches(self, article: Article, order: Order, now: datetime) -> bool:
        return article.is_digital


class FinalSaleRule(_Rule):
    type: Literal["final_sale"]

    def matches(self, article: Article, order: Order, now: datetime) -> bool:
        return article.is_final_sale


class ReturnWindowRule(_Rule):
    type: Literal["return_window"]
    days: int = Field(ge=1)
    # Per-category overrides. Keys must be lowercase to match the mapper's
    # normalised `Article.category`. Falls back to `days` when absent.
    category_windows: dict[str, Annotated[int, Field(ge=1)]] | None = None

    @field_validator("category_windows")
    @classmethod
    def _keys_must_be_lowercase(
        cls, value: dict[str, int] | None
    ) -> dict[str, int] | None:
        if value is None:
            return value
        bad = [k for k in value if k != k.lower()]
        if bad:
            raise ValueError(f"category_windows keys must be lowercase, got: {bad!r}")
        return value

    def _window_for(self, article: Article) -> int:
        if self.category_windows is None:
            return self.days
        return self.category_windows.get(article.category, self.days)

    def matches(self, article: Article, order: Order, now: datetime) -> bool:
        return now - order.delivery_date > timedelta(days=self._window_for(article))

    def reason_for(self, article: Article) -> str:
        return self.reason.format(days=self._window_for(article))


Rule = Annotated[
    FullyReturnedRule | DigitalRule | FinalSaleRule | ReturnWindowRule,
    Field(discriminator="type"),
]


class RulesConfig(BaseModel):
    rules: list[Rule]


@functools.cache
def _load_rules(path: Path) -> RulesConfig:
    raw = yaml.safe_load(path.read_text())
    return RulesConfig.model_validate(raw)


def evaluate_eligibility(
    order: Order,
    *,
    rules_path: Path | None = None,
    now: datetime | None = None,
) -> list[ArticleEligibility]:
    """Evaluate return eligibility for every article in *order*.

    Args:
        order: The order whose articles to evaluate.
        rules_path: Optional override for the rules config (tests/staging).
        now: Optional clock override; defaults to ``datetime.now()``.
    """
    config = _load_rules(rules_path or _DEFAULT_RULES_PATH)
    current_time = now or datetime.now()
    return [
        _evaluate_article(article, order, config.rules, current_time)
        for article in order.articles
    ]


def _evaluate_article(
    article: Article,
    order: Order,
    rules: list[Rule],
    now: datetime,
) -> ArticleEligibility:
    for rule in rules:
        if rule.matches(article, order, now):
            return ArticleEligibility(
                article=article,
                returnable=False,
                reason=rule.reason_for(article),
                matched_rule=rule.type,
            )
    return ArticleEligibility(
        article=article,
        returnable=True,
        reason="",
        matched_rule="",
    )
