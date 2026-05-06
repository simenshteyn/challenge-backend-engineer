"""Tests for the Django views."""

import pytest
from django.test import Client

pytestmark = pytest.mark.django_db


@pytest.fixture()
def client() -> Client:
    return Client()


class TestLookupView:
    def test_get_returns_200(self, client: Client) -> None:
        response = client.get("/returns/")
        assert response.status_code == 200

    def test_get_contains_form(self, client: Client) -> None:
        response = client.get("/returns/")
        assert b"order_number" in response.content

    def test_valid_email_redirects(self, client: Client) -> None:
        response = client.post(
            "/returns/",
            {
                "order_number": "RMA-1001",
                "identifier": "alex@example.com",
            },
        )
        assert response.status_code == 302
        assert "/articles/" in response.headers["Location"]

    def test_valid_zip_redirects(self, client: Client) -> None:
        response = client.post(
            "/returns/",
            {
                "order_number": "RMA-1001",
                "identifier": "10115",
            },
        )
        assert response.status_code == 302

    def test_invalid_credentials_shows_error(self, client: Client) -> None:
        response = client.post(
            "/returns/",
            {
                "order_number": "RMA-1001",
                "identifier": "wrong@example.com",
            },
        )
        assert response.status_code == 200
        assert b"not found" in response.content.lower()

    def test_empty_fields_returns_form(self, client: Client) -> None:
        response = client.post(
            "/returns/",
            {
                "order_number": "",
                "identifier": "",
            },
        )
        assert response.status_code == 200


class TestArticlesView:
    def test_unauthenticated_redirects(self, client: Client) -> None:
        response = client.get("/returns/RMA-1001/articles/")
        assert response.status_code == 302

    def test_authenticated_shows_articles(self, client: Client) -> None:
        # Log in first
        client.post(
            "/returns/",
            {
                "order_number": "RMA-1001",
                "identifier": "alex@example.com",
            },
        )
        response = client.get("/returns/RMA-1001/articles/")
        assert response.status_code == 200
        assert b"TSHIRT-BLK-M" in response.content

    def test_cross_order_access_redirects(self, client: Client) -> None:
        """SEC-001 parity: authenticating to one order must not grant access
        to another order via URL substitution."""
        client.post(
            "/returns/",
            {"order_number": "RMA-1001", "identifier": "alex@example.com"},
        )
        response = client.get("/returns/RMA-1002/articles/")
        assert response.status_code == 302
        assert response.headers["Location"] == "/returns/"


class TestReturnableOnlyFilter:
    """FR-001: HTMX-driven filter that hides non-returnable items."""

    @pytest.fixture()
    def authed_client(self, client: Client) -> Client:
        client.post(
            "/returns/",
            {"order_number": "RMA-1001", "identifier": "alex@example.com"},
        )
        return client

    def test_no_filter_shows_all_articles(self, authed_client: Client) -> None:
        response = authed_client.get("/returns/RMA-1001/articles/")
        assert response.status_code == 200
        assert b"TSHIRT-BLK-M" in response.content  # returnable
        assert b"EBOOK-RETURNS" in response.content  # digital, not returnable

    def test_returnable_only_hides_non_returnable(
        self, authed_client: Client
    ) -> None:
        response = authed_client.get(
            "/returns/RMA-1001/articles/?returnable_only=1"
        )
        assert response.status_code == 200
        assert b"TSHIRT-BLK-M" in response.content
        assert b"EBOOK-RETURNS" not in response.content

    def test_htmx_request_returns_partial(self, authed_client: Client) -> None:
        """HTMX swaps just the article list — no <html>, no page chrome."""
        response = authed_client.get(
            "/returns/RMA-1001/articles/",
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert b"<html" not in response.content
        assert b"order-header" not in response.content
        assert b"article-card" in response.content

    def test_htmx_request_with_filter(self, authed_client: Client) -> None:
        response = authed_client.get(
            "/returns/RMA-1001/articles/?returnable_only=1",
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert b"<html" not in response.content
        assert b"TSHIRT-BLK-M" in response.content
        assert b"EBOOK-RETURNS" not in response.content


class TestReturnSubmissionFlow:
    """FR-002: end-to-end articles → confirm → success."""

    @pytest.fixture()
    def authed_client(self, client: Client) -> Client:
        client.post(
            "/returns/",
            {"order_number": "RMA-1001", "identifier": "alex@example.com"},
        )
        return client

    def test_post_articles_with_valid_selection_redirects_to_confirm(
        self, authed_client: Client
    ) -> None:
        response = authed_client.post(
            "/returns/RMA-1001/articles/",
            {"selected": ["TSHIRT-BLK-M"], "qty_TSHIRT-BLK-M": "1"},
        )
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/confirm/")
        assert authed_client.session["return_selection"] == [
            {"sku": "TSHIRT-BLK-M", "qty": 1},
        ]

    def test_post_articles_with_no_selection_redirects_back(
        self, authed_client: Client
    ) -> None:
        response = authed_client.post("/returns/RMA-1001/articles/", {})
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/articles/")
        assert "return_selection" not in authed_client.session

    def test_post_articles_drops_non_selectable_sku(
        self, authed_client: Client
    ) -> None:
        """An attacker can't sneak in a non-returnable SKU via form tampering."""
        response = authed_client.post(
            "/returns/RMA-1001/articles/",
            {
                "selected": ["TSHIRT-BLK-M", "EBOOK-RETURNS"],  # EBOOK is digital
                "qty_TSHIRT-BLK-M": "1",
                "qty_EBOOK-RETURNS": "1",
            },
        )
        assert response.status_code == 302
        # Only the legitimate SKU survives validation.
        assert authed_client.session["return_selection"] == [
            {"sku": "TSHIRT-BLK-M", "qty": 1},
        ]

    def test_post_articles_clamps_qty_to_remaining(
        self, authed_client: Client
    ) -> None:
        """Submitting qty > remaining is silently clamped, not rejected."""
        response = authed_client.post(
            "/returns/RMA-1001/articles/",
            {"selected": ["TSHIRT-BLK-M"], "qty_TSHIRT-BLK-M": "999"},
        )
        assert response.status_code == 302
        # TSHIRT-BLK-M has quantity=1 in orders_raw.json
        assert authed_client.session["return_selection"] == [
            {"sku": "TSHIRT-BLK-M", "qty": 1},
        ]

    def test_get_confirm_without_session_selection_redirects(
        self, authed_client: Client
    ) -> None:
        response = authed_client.get("/returns/RMA-1001/confirm/")
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/articles/")

    def test_get_confirm_with_selection_renders_line_items(
        self, authed_client: Client
    ) -> None:
        authed_client.post(
            "/returns/RMA-1001/articles/",
            {"selected": ["TSHIRT-BLK-M"], "qty_TSHIRT-BLK-M": "1"},
        )
        response = authed_client.get("/returns/RMA-1001/confirm/")
        assert response.status_code == 200
        assert b"TSHIRT-BLK-M" in response.content
        assert b"Confirm return" in response.content

    def test_post_confirm_redirects_to_success_and_clears_session(
        self, authed_client: Client
    ) -> None:
        authed_client.post(
            "/returns/RMA-1001/articles/",
            {"selected": ["TSHIRT-BLK-M"], "qty_TSHIRT-BLK-M": "1"},
        )
        response = authed_client.post("/returns/RMA-1001/confirm/")
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/success/")
        assert "return_selection" not in authed_client.session

    def test_get_success_renders(self, authed_client: Client) -> None:
        response = authed_client.get("/returns/RMA-1001/success/")
        assert response.status_code == 200
        assert b"Return submitted" in response.content
        assert b"RMA-1001" in response.content

    def test_unauthenticated_post_articles_redirects(
        self, client: Client
    ) -> None:
        """Without a prior lookup, POST to /articles/ goes to /returns/."""
        response = client.post(
            "/returns/RMA-1001/articles/",
            {"selected": ["TSHIRT-BLK-M"]},
        )
        assert response.status_code == 302
        assert response.headers["Location"] == "/returns/"

    def test_cross_order_post_blocked(self, authed_client: Client) -> None:
        """SEC-001 invariant extends to POST too — session must match URL."""
        response = authed_client.post(
            "/returns/RMA-1002/articles/",
            {"selected": ["WHATEVER"]},
        )
        assert response.status_code == 302
        assert response.headers["Location"] == "/returns/"
