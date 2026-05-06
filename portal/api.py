# mypy: disable-error-code="misc,untyped-decorator"
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.reverse import reverse

from portal.forms import LookupForm
from portal.services.eligibility import evaluate_eligibility
from portal.services.order_store import find_order, get_order
from portal.types import Article, ArticleEligibility, Order


class LookupRequestSerializer(serializers.Serializer[dict[str, Any]]):
    order_number = serializers.CharField()
    identifier = serializers.CharField()


class LookupResponseSerializer(serializers.Serializer[dict[str, Any]]):
    order_number = serializers.CharField()
    articles_url = serializers.CharField()


class ArticleSerializer(serializers.Serializer[dict[str, Any]]):
    sku = serializers.CharField()
    name = serializers.CharField()
    quantity = serializers.IntegerField()
    quantity_returned = serializers.IntegerField()
    price = serializers.FloatField()
    is_digital = serializers.BooleanField()
    is_final_sale = serializers.BooleanField()
    category = serializers.CharField()


class EligibilityEntrySerializer(serializers.Serializer[dict[str, Any]]):
    article = ArticleSerializer()
    returnable = serializers.BooleanField()
    reason = serializers.CharField()
    matched_rule = serializers.CharField()
    remaining_qty = serializers.IntegerField()
    quantity_options = serializers.ListField(child=serializers.IntegerField())
    selectable = serializers.BooleanField()


class OrderSerializer(serializers.Serializer[dict[str, Any]]):
    order_number = serializers.CharField()
    email = serializers.CharField()
    recipient = serializers.CharField()
    zip = serializers.CharField()
    street = serializers.CharField()
    city = serializers.CharField()
    order_date = serializers.DateTimeField()
    delivery_date = serializers.DateTimeField()


class ArticlesResponseSerializer(serializers.Serializer[dict[str, Any]]):
    order = OrderSerializer()
    results = EligibilityEntrySerializer(many=True)


class ReturnsViewSet(viewsets.ViewSet):
    """Headless endpoints mirroring the lookup/articles browser flow."""

    @action(detail=False, methods=["post"], url_path="lookup")
    def lookup(self, request: Request) -> Response:
        request_serializer = LookupRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)

        form = LookupForm(request_serializer.validated_data)
        if not form.is_valid():
            return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)

        order = find_order(
            form.cleaned_data["order_number"],
            form.cleaned_data["identifier"],
        )
        if order is None:
            return Response(
                {"detail": "Order not found or credentials do not match."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        request.session["order_number"] = order.order_number

        payload = {
            "order_number": order.order_number,
            "articles_url": reverse(
                "returns-articles",
                kwargs={"pk": order.order_number},
                request=request,
            ),
        }
        response_serializer = LookupResponseSerializer(payload)
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="articles")
    def articles(self, request: Request, pk: str | None = None) -> Response:
        order_number = pk or ""
        # SEC-001: must match, not just exist — otherwise authenticating to
        # any order grants read access to every order via URL substitution.
        if request.session.get("order_number") != order_number:
            return Response(
                {"detail": "Order lookup is required before viewing articles."},
                status=status.HTTP_403_FORBIDDEN,
            )

        order = get_order(order_number)
        if order is None:
            return Response(
                {"detail": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        results = evaluate_eligibility(order)
        payload = {
            "order": self._serialize_order(order),
            "results": [self._serialize_result(result) for result in results],
        }

        response_serializer = ArticlesResponseSerializer(payload)
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    def _serialize_order(self, order: Order) -> dict[str, Any]:
        return {
            "order_number": order.order_number,
            "email": order.email,
            "recipient": order.recipient,
            "zip": order.zip,
            "street": order.street,
            "city": order.city,
            "order_date": order.order_date,
            "delivery_date": order.delivery_date,
        }

    def _serialize_result(self, result: ArticleEligibility) -> dict[str, Any]:
        remaining_qty = max(result.article.quantity - result.article.quantity_returned, 0)
        return {
            "article": self._serialize_article(result.article),
            "returnable": result.returnable,
            "reason": result.reason,
            "matched_rule": result.matched_rule,
            "remaining_qty": remaining_qty,
            "quantity_options": list(range(1, remaining_qty + 1)),
            "selectable": result.returnable and remaining_qty > 0,
        }

    def _serialize_article(self, article: Article) -> dict[str, Any]:
        return asdict(article)
