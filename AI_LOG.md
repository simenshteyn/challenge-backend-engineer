# AI Log

- **Tool:** Claude Code (Anthropic, Opus 4.7) used as a pair-programming assistant throughout the challenge.

- **Prompt summary:** For each backlog task: asked the assistant for a plan + the judgment calls it implied, pushed back where I disagreed (or asked "what would you recommend?" when it could've gone either way), then for the implementation as one batch — code, tests, and the `DECISIONS.md` entry. Before every commit, asked for a careful self-review. This consistently surfaced real issues that would otherwise have shipped: dead code in a tuple-typed dict (`(remaining, 1)` where the `1` was never read), a hardcoded `parametrize` list in the orders round-trip test that defeated its own stated purpose of catching "new orders with typo'd fields", missing symmetric `Field(ge=1)` on the per-category window dict values, the SEC-001 IDOR itself (caught during the security audit pass — the assistant compared the HTML view's `!= order_number` check against the DRF view's `not session.get(...)` and named the divergence). Commit message wording also went through the assistant; I edited where I wanted a different emphasis.

- **How I verified the output:**
    - Read every diff before staging — the assistant occasionally over-scoped or made stylistic choices I tightened (e.g. it initially proposed `Field(ge=0)` for the return-window when I'd previously voted `ge=1`; the inconsistency only surfaced because I re-read the diff).
    - Ran `make test` and `make lint` after every change. Suite went from `27 passing / 4 failing` at the start to `82 passing` at the end.
    - Walked the full `lookup → articles → (filter) → confirm → success` flow in a real browser for FR-001 and FR-002 — HTMX swap behavior, `hx-push-url` URL state, browser back/refresh, selection survival across the toggle, CSRF round-trip on POST. None of this is testable from pytest.
    - Read every `DECISIONS.md` entry end-to-end before pushing the related commit, confirming the rationales reflect *my* choices, not just options the assistant presented. Tightened wording in places where it sounded like AI-output rather than something I'd say.

- **What didn't work / had to be corrected:**
    - The assistant didn't notice `python manage.py migrate` was missing from the README's quickstart until the dev server crashed during my first FR-001 manual test. Pytest didn't catch it because pytest-django builds a fresh test DB from migrations on every run; the persistent dev `db.sqlite3` had never been migrated. Noted in `DECISIONS.md` under FR-001.
    - First proposed implementation of FR-001 kept `selected['item_{forloop.counter}']` as the Alpine state key, which was already broken once the filter could change the index numbering of items. Caught at the design-discussion stage and switched to SKU-keyed selection — which then made FR-002's submission flow trivial (the keys were already stable identifiers).
    - The OPEN-001 review pass caught two normalisation gaps in the original `find_order` change (case-insensitivity but not whitespace; whitespace-only identifiers slipping past the empty-identifier guard).
