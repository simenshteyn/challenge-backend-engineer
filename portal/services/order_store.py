"""Simple JSON-backed order store.

Loads orders from ``portal/data/orders_raw.json`` and provides lookup
helpers used by the views.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

from portal.services.mapper import map_order
from portal.types import Order

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "orders_raw.json"

# How many days ago each order was delivered.  Used to keep the demo
# data realistic regardless of when the challenge is actually run.
_DELIVERY_AGE_DAYS: dict[str, int] = {
    "RMA-1001": 5,  # recent — within a typical return window
    "RMA-1002": 60,  # old — outside a typical 14–30 day window
    "RMA-1003": 3,  # just placed, not yet shipped
}


def _load_raw_orders() -> list[dict[str, Any]]:
    with _DATA_PATH.open() as f:
        loaded: object = json.load(f)

    assert isinstance(loaded, dict), "orders JSON root must be an object"

    orders = loaded.get("orders")
    assert isinstance(orders, list), "orders JSON must include an 'orders' list"
    assert all(isinstance(order, dict) for order in orders), (
        "each order entry must be an object"
    )

    return [cast(dict[str, Any], order) for order in orders]


def _freshen_dates(order: Order) -> Order:
    """Re-anchor dates relative to today so the demo stays realistic."""
    days_ago = _DELIVERY_AGE_DAYS.get(order.order_number)
    if days_ago is None:
        return order
    gap = order.delivery_date - order.order_date
    new_delivery = datetime.now().replace(
        hour=14,
        minute=0,
        second=0,
        microsecond=0,
    ) - timedelta(days=days_ago)
    return replace(order, delivery_date=new_delivery, order_date=new_delivery - gap)


def find_order(order_number: str, identifier: str) -> Order | None:
    """Look up an order by number and verify the email or zip matches.

    Identifier matching is case-insensitive and tolerant of leading or
    trailing whitespace — customers reasonably expect ``Alex@Example.com``
    and ``alex@example.com`` to behave the same.

    Returns the mapped :class:`Order` on success, or ``None`` if the order
    is not found or the identifier does not match.
    """
    normalized = identifier.strip().lower()
    if not normalized:
        return None
    for raw in _load_raw_orders():
        if raw["order_number"] != order_number:
            continue
        email = str(raw.get("email", "")).strip().lower()
        zip_code = str(raw.get("zip", "")).strip().lower()
        if normalized in (email, zip_code):
            return _freshen_dates(map_order(raw))
    return None


def get_order(order_number: str) -> Order | None:
    """Retrieve an order by number (no credential check).

    Used after the user has already been authenticated via the lookup page.
    """
    for raw in _load_raw_orders():
        if raw["order_number"] == order_number:
            return _freshen_dates(map_order(raw))
    return None
