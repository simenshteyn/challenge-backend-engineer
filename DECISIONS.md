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
