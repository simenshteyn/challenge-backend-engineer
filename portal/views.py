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
        template = (
            "returns/_article_list.html" if is_htmx else "returns/articles.html"
        )

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
