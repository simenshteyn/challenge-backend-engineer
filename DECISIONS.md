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
