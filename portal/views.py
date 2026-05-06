from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views import View

from portal.forms import LookupForm
from portal.services.eligibility import evaluate_eligibility
from portal.services.order_store import find_order, get_order


class LookupView(View):
    """Order lookup page – validates order number + email / zip."""

    def get(self, request: HttpRequest) -> HttpResponse:
        return render(request, "returns/lookup.html", {"form": LookupForm()})

    def post(self, request: HttpRequest) -> HttpResponse:
        form = LookupForm(request.POST)
        if form.is_valid():
            order = find_order(
                form.cleaned_data["order_number"],
                form.cleaned_data["identifier"],
            )
            if order is None:
                form.add_error(None, "Order not found or credentials do not match.")
            else:
                # Anti session-fixation: rotate the session ID on successful
                # auth. Also drop any stale in-progress return selection from
                # a prior order so it can't leak across logins.
                request.session.cycle_key()
                request.session.pop("return_selection", None)
                request.session["order_number"] = order.order_number
                return redirect("articles", order_number=order.order_number)

        return render(request, "returns/lookup.html", {"form": form})


class ArticlesView(View):
    """Articles page – shows items in the order with eligibility info."""

    def get(self, request: HttpRequest, order_number: str) -> HttpResponse:
        if request.session.get("order_number") != order_number:
            return redirect("lookup")

        order = get_order(order_number)
        if order is None:
            return redirect("lookup")

        returnable_only = request.GET.get("returnable_only") == "1"

        results = evaluate_eligibility(order)
        if returnable_only:
            results = [r for r in results if r.returnable]

        article_rows = []
        for result in results:
            remaining_qty = max(
                result.article.quantity - result.article.quantity_returned,
                0,
            )
            article_rows.append(
                {
                    "result": result,
                    "remaining_qty": remaining_qty,
                    "quantity_options": list(range(1, remaining_qty + 1)),
                    "selectable": result.returnable and remaining_qty > 0,
                }
            )

        # HTMX swaps just the article list; full GETs return the page chrome.
        is_htmx = request.headers.get("HX-Request") == "true"
        template = "returns/_article_list.html" if is_htmx else "returns/articles.html"

        return render(
            request,
            template,
            {
                "order": order,
                "results": results,
                "article_rows": article_rows,
                "returnable_only": returnable_only,
            },
        )

    def post(self, request: HttpRequest, order_number: str) -> HttpResponse:
        if request.session.get("order_number") != order_number:
            return redirect("lookup")

        order = get_order(order_number)
        if order is None:
            return redirect("lookup")

        # Re-evaluate eligibility on the server — never trust the form's
        # SKU/qty without re-checking. (Same lesson as SEC-001.)
        selectable: dict[str, int] = {}
        for result in evaluate_eligibility(order):
            remaining = result.article.quantity - result.article.quantity_returned
            if result.returnable and remaining > 0:
                selectable[result.article.sku] = remaining

        selection: list[dict[str, int | str]] = []
        for sku in request.POST.getlist("selected"):
            qty_left = selectable.get(sku)
            if qty_left is None:
                continue  # not selectable in the current state — silently drop
            try:
                qty = int(request.POST.get(f"qty_{sku}", "1"))
            except (TypeError, ValueError):
                qty = 1
            qty = max(1, min(qty, qty_left))
            selection.append({"sku": sku, "qty": qty})

        if not selection:
            return redirect("articles", order_number=order_number)

        request.session["return_selection"] = selection
        return redirect("confirm", order_number=order_number)


class ConfirmView(View):
    """Confirmation page – shows what's about to be returned."""

    def get(self, request: HttpRequest, order_number: str) -> HttpResponse:
        if request.session.get("order_number") != order_number:
            return redirect("lookup")

        selection = request.session.get("return_selection")
        if not selection:
            return redirect("articles", order_number=order_number)

        order = get_order(order_number)
        if order is None:
            return redirect("lookup")

        articles_by_sku = {a.sku: a for a in order.articles}
        line_items = []
        total = 0.0
        for entry in selection:
            article = articles_by_sku.get(entry["sku"])
            if article is None:
                continue
            qty = int(entry["qty"])
            subtotal = round(article.price * qty, 2)
            line_items.append({"article": article, "qty": qty, "subtotal": subtotal})
            total += subtotal

        if not line_items:
            return redirect("articles", order_number=order_number)

        return render(
            request,
            "returns/confirm.html",
            {
                "order": order,
                "line_items": line_items,
                "total": round(total, 2),
            },
        )

    def post(self, request: HttpRequest, order_number: str) -> HttpResponse:
        if request.session.get("order_number") != order_number:
            return redirect("lookup")

        if not request.session.get("return_selection"):
            return redirect("articles", order_number=order_number)

        # Demo-mode fire-and-forget: clear the pending selection and move on.
        # A real implementation would persist a Return aggregate here.
        request.session.pop("return_selection", None)
        return redirect("success", order_number=order_number)


class SuccessView(View):
    """Success page – return submitted."""

    def get(self, request: HttpRequest, order_number: str) -> HttpResponse:
        if request.session.get("order_number") != order_number:
            return redirect("lookup")

        order = get_order(order_number)
        if order is None:
            return redirect("lookup")

        return render(request, "returns/success.html", {"order": order})
