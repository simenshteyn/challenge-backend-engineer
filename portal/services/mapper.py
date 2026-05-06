"""Map raw order payload to domain models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from portal.types import Article, Order


def _parse_dt(value: str) -> datetime:
    """Parse an ISO-8601 string, discarding timezone info for simplicity."""
    return datetime.fromisoformat(value).replace(tzinfo=None)


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list_of_dicts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _as_str(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return default


def _as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


_FINAL_SALE_TAGS = frozenset({"final-sale", "final_sale", "finalsale"})


def _derive_category(item: dict[str, Any]) -> str:
    """Pick the explicit ``category`` field, else the first segment of
    ``product_type`` (e.g. ``"Apparel > T-Shirts"`` -> ``"apparel"``)."""
    explicit = _as_str(item.get("category")).strip()
    if explicit:
        return explicit.lower()
    product_type = _as_str(item.get("product_type")).strip()
    if product_type:
        return product_type.split(">")[0].strip().lower()
    return ""


def _derive_is_digital(item: dict[str, Any], category: str) -> bool:
    raw = item.get("digital")
    if isinstance(raw, bool):
        return raw
    return category == "digital"


def _derive_is_final_sale(item: dict[str, Any]) -> bool:
    raw = item.get("final_sale")
    if isinstance(raw, bool):
        return raw
    return any(
        tag.lower() in _FINAL_SALE_TAGS for tag in _as_str_list(item.get("tags"))
    )


def map_order(raw: dict[str, Any]) -> Order:
    """Map a raw order dict (from ``orders_raw.json``) to an :class:`Order`.

    The raw payload comes from an upstream system.  This mapper converts it
    into our internal domain representation.
    """
    customer = _as_dict(raw.get("customer"))

    recipient = _as_str(raw.get("recipient"))
    if not recipient:
        first_name = _as_str(customer.get("first_name"))
        last_name = _as_str(customer.get("last_name"))
        recipient = " ".join(part for part in [first_name, last_name] if part)

    street = _as_str(raw.get("street"))
    if not street:
        address_line = _as_str(customer.get("address_line"))
        address_extra = _as_str(customer.get("address_line_extra"))
        street = ", ".join(part for part in [address_line, address_extra] if part)

    zip_code = _as_str(raw.get("zip")) or _as_str(customer.get("postal_code"))
    city = _as_str(raw.get("city")) or _as_str(customer.get("city"))

    order_date_raw = _as_str(raw.get("order_date"))
    order_date = _parse_dt(order_date_raw)

    articles: list[Article] = []

    for item in _as_list_of_dicts(raw.get("articles")):
        category = _derive_category(item)
        articles.append(
            Article(
                sku=_as_str(item.get("sku")),
                name=_as_str(item.get("name")),
                quantity=_as_int(item.get("quantity"), default=1),
                quantity_returned=_as_int(item.get("quantity_returned"), default=0),
                price=_as_float(item.get("price")),
                is_digital=_derive_is_digital(item, category),
                is_final_sale=_derive_is_final_sale(item),
                category=category,
            )
        )

    # Derive the effective delivery date from the most recent fulfillment.
    fulfillments = _as_list_of_dicts(raw.get("fulfillments"))
    delivery_date: datetime | None = None
    for f in fulfillments:
        delivered_at = _as_str(f.get("delivered_at"))
        if delivered_at:
            dt = _parse_dt(delivered_at)
            if delivery_date is None or dt > delivery_date:
                delivery_date = dt

    if delivery_date is None:
        delivered_at = _as_str(raw.get("delivered_at"))
        if delivered_at:
            delivery_date = _parse_dt(delivered_at)

    return Order(
        order_number=_as_str(raw.get("order_number")),
        email=_as_str(raw.get("email")),
        recipient=recipient,
        zip=zip_code,
        street=street,
        city=city,
        order_date=order_date,
        delivery_date=delivery_date or order_date,
        articles=articles,
    )
