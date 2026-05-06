# Decisions

## Backlog ordering

Picked depth-first to keep commits coherent: BR-001 → BR-002 → BR-004 → SEC-001 → BR-003 → FR-001 → FR-002. Rationale: BR-002 needs BR-001's flags; BR-004 is a natural extension of the rules schema if designed for it; SEC-001 and the FR-* tasks don't block anything else and can be dropped first if time runs out. OPEN-001 deferred.

## BR-001 — mapper gaps

**Decision:** Support two payload shapes in `map_order`. Explicit per-article fields (`digital`, `final_sale`, `category`) win; otherwise derive from `product_type` (first `>`-segment, lowercased) and `tags` (case-insensitive `final-sale` / `final_sale` / `finalsale`).

**Rationale:** `orders_raw.json` carries the explicit fields, but the test fixtures in `conftest.py` only carry `product_type` and `tags` — both shapes are real upstream variants the mapper has to handle. Explicit-wins keeps production data authoritative when both are present.

**Notable sub-decisions:**
- `clearance` tag does **not** imply final-sale — clearance is pricing, return policy is separate.
- `is_digital` derives from `category == "digital"` only, not from `requires_shipping == False`. A non-shipped gift card isn't a digital good in our model.
- Category is lowercased so downstream rules (BR-002/BR-004) can match on stable keys.

**Alternatives considered:**
- OR-ing explicit and derived signals (e.g. `final_sale OR "final-sale" in tags`). Rejected: makes it impossible for upstream to flip an item back to returnable once a tag is in place.
- Treating `requires_shipping == False` as digital. Rejected as above (gift cards).
- Keeping the derivation logic in the eligibility engine instead of the mapper. Rejected: the mapper's job is to produce a clean domain model; rules should read flags, not parse upstream quirks.

**Tests added:** five tests in `TestMapArticleExplicitFields` covering the explicit-field path (which the conftest fixtures don't exercise) and precedence (explicit `false` overriding a `final-sale` tag, `clearance` alone not implying final-sale).

## BR-002 — return eligibility engine

**Decision:** YAML config (`portal/data/return_rules.yaml`) with predefined, parameterized rule types. Rules are evaluated in config order; first match wins, item is marked not returnable, and the rule's `type` is surfaced as `matched_rule` for support tooling. Items that match no rule are returnable with empty `reason`/`matched_rule`.

**Format choice — YAML over JSON:** comments + readable nesting. The `pyyaml` dep is already in `pyproject.toml`. JSON would have been zero-import-surface, but BR-004 will need readable per-category configuration and YAML wins on that axis.

**Rule model — pydantic discriminated union:** each rule type is a `BaseModel` subclass with a `Literal["..."]` `type` field and its own `matches(article, order, now) -> bool` method. The union is `Annotated[..., Field(discriminator="type")]`, so pydantic validates the YAML and dispatches statically. Adding a rule = new subclass + literal; no central `if/elif` to grow.

**Engine signature:** `evaluate_eligibility(order, *, rules_path=None, now=None)`. The two keyword-only overrides exist for testing — production callers (`views.py`, `api.py`) keep the original `evaluate_eligibility(order)` shape.

**Caching:** `functools.cache` on `_load_rules(path)`. Rules are immutable in production; tests that pass a unique `tmp_path` get a fresh load.

**Designed-for-BR-004:** `ReturnWindowRule` already has its own `matches()`; per-category windows will be a `category_windows: dict[str, int] | None = None` field and one extra line in `matches()`. No engine refactor.

**Alternatives considered:**
- **Generic predicate DSL** (e.g. `when: article.is_digital`). Rejected — overkill for four rules; adds a parser to maintain and an eval-string risk.
- **Plain dicts + dispatch table** (no pydantic). Rejected — loses validation of unknown rule types and missing fields, and the plugin gives mypy strict the type narrowing for free.
- **Hardcoding the rules in Python** with a config flag for the window only. Rejected — README explicitly asks for a configurable engine.

**Tests added:** six new tests covering final-sale items (existing suite didn't), the `matched_rule` identifier contract (stable string out, empty when returnable), first-match-wins ordering with two competing rules in `tmp_path`, custom-rules-file support, and `now`-injection for deterministic window tests.

## BR-004 — per-category return windows

**Decision:** Add an optional `category_windows: dict[str, int] | None` field to `ReturnWindowRule`. The window for an article is `category_windows.get(article.category, days)`; `days` is the order-level default. Reason text supports `{days}` interpolation so the customer-facing message reflects the *resolved* window, not the default.

**Why one rule, not many:** The README phrasing ("fall back to the order-level default when a category isn't configured") describes a *single policy* with overrides, not multiple competing policies. Modelling it as one rule keeps "fall back" as a `dict.get`, not a function of YAML ordering. Multiple `return_window` rules with `category:` filters would have worked, but ordering becomes load-bearing — a misplaced default-rule silently breaks the policy. Single-rule is the smaller blast radius.

**Reason templating** lives on a new `reason_for(article)` method on the `_Rule` base (default: returns `self.reason` unchanged). Only `ReturnWindowRule` overrides it. This keeps the polymorphism explicit; rules without dynamic data don't have to think about formatting.

**Schema constraints I added while here:**
- `days: int = Field(ge=1)` — `-7` or `0` fails at load time.
- `category_windows: dict[str, Annotated[int, Field(ge=1)]] | None` — symmetric validation; per-category overrides can't sneak past as `-7`.
- `model_config = ConfigDict(extra="forbid", frozen=True)` on `_Rule`. `extra="forbid"` makes typo'd YAML keys (e.g. `categori_windows:`) fail loudly instead of silently no-op'ing. `frozen=True` means the cached `RulesConfig` (shared across requests via `@functools.cache`) can't be mutated by a misbehaving caller.
- `@field_validator("category_windows")` rejects uppercase keys at load time. The lowercase invariant was previously documented-only; without enforcement, `Electronics: 14` silently misses the lookup. Fail-loud > silently auto-lowercase, since auto-lowercasing hides config drift.

All four pinned by tests in `TestCustomRulesConfig`.

**Lowercase invariant:** `category_windows` keys must be lowercase to match the mapper's normalised `Article.category` (BR-001 decision). Documented inline in both the rule model and the YAML comment; not auto-lowercased on load — the YAML is canonical and a typo should fail loudly via "key didn't match", not silently via case mismatch.

**Empty category falls back to default:** `dict.get("", 30)` → 30. Pinned in `test_empty_category_falls_back_to_default`.

**Alternatives considered:**
- **Multiple `return_window` rules with a `category:` filter.** Rejected — order-dependent.
- **Top-level `return_windows: {default, electronics, ...}` separate from `rules:`.** Rejected — splits one policy across two config locations and breaks rule self-containment.
- **No interpolation; one fixed reason string.** Rejected — message would lie when a category window fires ("30-day window expired" while electronics actually has a 14-day window).

**Tests added:** eight tests covering category-override applies (electronics 14 < default 30), unmapped category falls back to default, empty category falls back to default, multi-article order where two categories resolve to different windows in the same evaluation, plus four pinning the validation contract (`days: -7`, per-category `electronics: -7`, uppercase key `Electronics:`, and a typo'd field name all raise `ValidationError`).

## SEC-001 — IDOR in the DRF articles endpoint

**Finding:** the JSON API checked that *some* lookup had occurred but did not verify that the session's `order_number` matched the requested one. After authenticating to any order they own, a customer could read every other order's data — recipient name, postal address, line items, prices — by substituting the URL path. The HTML view (`portal/views.py:36`) had the correct `!=` check; the DRF view (`portal/api.py:104`) had drifted to a truthiness check.

```python
# Before (vulnerable)
if not request.session.get("order_number"):
    return Response(..., 403)

# After (fixed)
if request.session.get("order_number") != order_number:
    return Response(..., 403)
```

**Impact:** straightforward IDOR. The auth model is "the session binds you to the order you proved you own"; the API silently let any authenticated customer enumerate every order by ID. Order numbers are short and human-readable (`RMA-1001`, `RMA-1002`, …), so enumeration is trivial.

**Why this happened (probably):** the HTML view was written first, the API was added later, and the author copied the *intent* of the auth check but typed the *easier* form. Both endpoints needed the same predicate; only one had it.

**Fix:** one-line change in `portal/api.py`, plus an inline comment that names the SEC-001 invariant so a future refactor doesn't quietly weaken it.

**Tests added (red-then-green):**
- `test_articles_rejects_cross_order_access` (API): authenticate to RMA-1001 with valid creds, request `/api/returns/RMA-1002/articles/`, assert 403. Failed before the fix (200), passes after.
- `test_cross_order_access_redirects` (HTML view): same shape — pins the existing-correct behavior so a future refactor of the HTML view can't silently regress to the same bug. Asserts the 302 + redirect target.

**Why pin both:** the bug existed because the two surfaces diverged. A single test on the API surface would catch *this* bug, but pinning the HTML view too makes it a *symmetric* invariant — the next person adding a third surface (mobile API, partner API, …) has two examples of the contract.

**Out of scope, but observed.** The audit also reviewed: SQL injection (none — no raw SQL), XSS / template injection (none — Django auto-escape on, no `|safe` / `mark_safe` / `format_html` / `{% autoescape off %}`), open redirect (none — `redirect()` only resolves named routes), unsafe deserialization (`yaml.safe_load`, not `yaml.load`), path traversal (data and rules paths computed from `__file__`), shell exec / eval (none), and mass assignment (no `**cleaned_data` splat into models). All clean.

The following are real concerns but are intentionally **not** part of SEC-001 — either documented as the intended model, or scoped at deployment rather than application code:

- **`SECRET_KEY = "dev-secret-key"` and `DEBUG = True`** in `returns_portal/settings.py`. Fine for a localhost challenge, predictable session/CSRF tokens and leaked tracebacks in prod. Standard starter-code caveat.
- **Auth model: email *or* zip, no rate limiting.** Zip codes are guessable and order numbers are sequential (`RMA-1001`, `1002`, …). README documents this as the intended auth model, so any change is a product decision, not a fix.
- **Case-sensitive email comparison in `find_order`.** `alex@example.com` works, `Alex@example.com` doesn't. UX bug, not security.
- **Session not rotated on lookup.** `request.session.cycle_key()` after a successful lookup would close a session-fixation surface. Low impact — only `order_number` is stored.
- **DRF `SessionAuthentication` doesn't enforce CSRF for anonymous requests** (well-known DRF behaviour: `enforce_csrf` is gated inside `authenticate()`, which returns early for anonymous users). At worst an attacker can pin a victim's session to *their own* order — no unauthorised data access.
- **Lookup error message is uniform** — same `"Order not found or credentials do not match."` for both "no such order" and "wrong credentials". Avoids becoming an existence oracle. ✓ (called out as a positive observation.)

## BR-003 — fix and extend the test suite

**Decision:** Scoped down sharply. The "fix the failing suite" half of BR-003 was already accomplished as a side effect of BR-001 (mapper fields) and BR-002 (rules engine) — the four originally-failing tests turned green automatically once the implementation work landed. So BR-003 collapses to "add tests that give confidence", and I picked the two highest-leverage gaps rather than padding the suite.

**Two gaps addressed**, in `portal/tests/services/test_order_store.py` (new file):

1. **`find_order` direct unit tests.** The function is the auth boundary, but until now it was only exercised indirectly via the view tests. Direct coverage on seven cases — valid email, valid zip, wrong email, wrong zip, unknown order, *credentials from a different order* (Lee's email/zip must not unlock Alex's order), and an empty-string identifier (the form layer rejects this, but the function-level contract is now pinned too). The cross-credentials case is the unit-level analogue of the SEC-001 IDOR contract: even with valid creds in the system, they only unlock *their* order.

2. **`orders_raw.json` round-trip.** Parametrised dynamically over every order discovered in the file at import time (via the existing loader, no duplicated path logic), asserts shape invariants — non-empty SKU, populated `delivery_date`, `quantity >= 1`, etc. — without pinning specific field values. Adding a fourth order to the JSON automatically extends the test; this is the "intern adds RMA-1004 with a typo'd `email_adress`" regression class, and the test wouldn't catch it if the parametrisation list were hardcoded.

**Gaps deliberately not addressed** (rationale: marginal value, not worth the suite-noise cost):

- 30-day boundary tests (`>` vs `>=`). Pins one number, won't catch real regressions; the inequality is a documented design choice.
- LookupForm validation. Django's form behaviour is Django's responsibility.
- Coverage of the mapper's `_as_int` / `_as_float` defensive coercion helpers. Internal, exercised transitively, low risk.
- Combined mapper+eligibility integration on a specific fixture. Both sides are well-covered in isolation; the integration is exercised end-to-end through `test_articles_after_lookup_returns_order_and_eligibility` in the API suite.

**Final test count: 61 passing** (was 27 / 4 failing at the start of the challenge). Coverage is concentrated where it pays — the mapper's payload-shape variants, the rules engine's config validation, the auth boundary, and both HTML/API surfaces of the SEC-001 invariant.

## FR-001 — "Show returnable only" filter

**Decision:** Server-side HTMX filter via a query parameter (`?returnable_only=1`). The articles page extracts its list rendering into a partial (`_article_list.html`); the toggle's `hx-get` swaps just that partial into `<div id="article-list">`. `hx-push-url="true"` keeps the browser URL in sync so back/refresh/share-link all behave correctly.

**Why server-side, not Alpine `x-show`:** the README is explicit ("using HTMX"), and the server already has the `returnable` flag — duplicating that decision in client JS would just be a place for the two to drift. The HTMX response also keeps state in the URL, which is the right shape for "I want to share a filtered link with support."

**Filter applied at the typed boundary:** filtering `results: list[ArticleEligibility]` *before* building the loosely-typed `article_rows` dicts keeps mypy strict happy without resorting to `cast()` and reads more naturally — "filter the eligibility results, then render".

**SKU-keyed Alpine selection state.** The pre-FR-001 template keyed `selected['item_{forloop.counter}']` — stable when the list never changed, broken the moment FR-001 filters reduce the list (a checked TSHIRT at index 1 would migrate to a checked HOODIE at index 1 after toggle). Switched to `selected['{sku|escapejs}']`, which is stable across renders and pre-builds the foundation FR-002 will need anyway (the submission has to identify items by SKU, not by render order). `escapejs` defends against the unlikely-but-possible apostrophe in a SKU string.

**HTMX detection via header, not a separate URL.** `request.headers.get("HX-Request") == "true"` picks the partial template; otherwise full page. One endpoint, one URL, two render paths. Avoids a second route and keeps `hx-push-url` clean — the URL the browser shows after the swap is the same URL a fresh visitor would type.

**Toggle deliberately *outside* the form.** It's not part of the return submission; placing it inside would have HTMX include every form field as a query param by default. Outside-the-form is also semantically right — the toggle is a view filter, not part of "what I want to return".

**API surface deliberately left untouched.** README only asks for the HTML toggle. Adding a `?returnable_only=` filter to the DRF `articles` endpoint would be unrequested scope. If the API needs it later, it's one `if` away.

**What I could *not* verify from tests.** The Django test client renders templates and asserts response bytes, so the server-side filter, partial extraction, and HX-Request branching are all covered. The *HTMX wiring itself* — toggle click triggers AJAX, response swaps the target, Alpine rebinds the new checkboxes against the existing `selected` scope — runs in the browser and can't be exercised from pytest. Verified manually in dev.

**Tests added (4):** `test_no_filter_shows_all_articles`, `test_returnable_only_hides_non_returnable`, `test_htmx_request_returns_partial` (asserts `<html>` and page chrome are absent, article cards present), `test_htmx_request_with_filter` (combined contract).

**Setup gotcha for fresh checkouts.** The README's quickstart goes `uv sync` → `pytest` → `runserver`, but the `runserver` flow uses Django sessions and the SQLite DB has never been migrated, so the first POST to `/returns/` raises `OperationalError: no such table: django_session`. `pytest` doesn't hit this because pytest-django builds the test DB from migrations on every run. Reviewers will need `uv run python manage.py migrate` once before `runserver`. Not a regression introduced by FR-001 — the same gap exists on `main` — but FR-001 is the first task that actually exercises sessions in the browser, so it surfaced here.

## FR-002 — return submission flow

**Decision:** Three views (`ArticlesView.post`, `ConfirmView`, `SuccessView`), POST-Redirect-GET pattern, selection carried between steps via `request.session["return_selection"]`. Articles → `/confirm/` → `/success/`, with each step idempotent on refresh.

**No domain model.** The README asks for an end-to-end flow, not a returns persistence layer — and the repo has no model, no DB usage besides sessions, and no upstream API to call. `ConfirmView.post` is fire-and-forget: it clears `return_selection` from the session and redirects. A real implementation would persist a Return aggregate and kick off a workflow; that's out of scope and intentionally so.

**Re-validate selection on the server, every time.** This is the SEC-001 lesson applied a second time: the form's submitted SKU/qty pairs are filtered against a freshly-evaluated `selectable` map (re-running `evaluate_eligibility(order)`), and qty is clamped to `[1, remaining_qty]`. A tampered POST that injects a non-returnable SKU is silently dropped — pinned in `test_post_articles_drops_non_selectable_sku`. Silent drop > 400 error, because the form layer prevents it for honest users; a tampered submission that filters down to "nothing valid" simply redirects back to articles.

**SKU-keyed form fields, not indexed.** Builds on the FR-001 SKU-keying decision: `name="selected" value="<sku>"` for checkboxes, `name="qty_<sku>"` for the qty selects. Server reads `request.POST.getlist("selected")` (multi-value) and `request.POST.get(f"qty_{sku}")`. Stable identifiers across the filter→select→submit sequence.

**Cancel preserves selection.** The Cancel button on the confirm page is just an `<a href>` back to `/articles/` — session is untouched, the user keeps their picks. Free UX win; alternative (Cancel = clear session) was rejected as user-hostile.

**Auth + cross-order checks on every view.** `ArticlesView.post`, `ConfirmView.get/post`, and `SuccessView.get` all open with the same `request.session.get("order_number") != order_number` guard from SEC-001. Pinned by `test_unauthenticated_post_articles_redirects` and `test_cross_order_post_blocked`.

**Form action explicit, not blank.** The articles form posts to `{% url 'articles' order.order_number %}` rather than `action=""`. With FR-001's `?returnable_only=1` query param in the URL, an empty action would post to the filtered URL — harmless but confusing in DevTools. Explicit action keeps the canonical URL on submission regardless of filter state.

**Tests added (10):** valid submission → confirm + session populated, empty submission → back to articles, tampered SKU dropped, qty clamping, confirm-without-session redirects, confirm renders line items, confirm POST clears session and redirects, success page renders with order number, unauthenticated POST blocked, cross-order POST blocked.

**What I could *not* verify from tests.** Same caveat as FR-001: the Django test client renders templates and asserts response bytes, so the server-side flow is fully covered. Browser-side concerns — CSRF token actually round-tripping, Continue button enabling on Alpine state change, the redirect chain in DevTools — need a manual click-through.

**Final test count: 75 passing** (was 27 / 4 failing at the start of the challenge). End-to-end flow exercisable from the browser: lookup → articles → (filter) → confirm → success.
