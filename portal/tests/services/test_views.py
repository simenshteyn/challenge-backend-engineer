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
