# 8. Hindi agent and translation

## Goal

A person says what happened in Hindi. The agent opens the right page, filled in. The
person checks it and approves. Every message they see, from any part of the system, is in
their language.

The agent's core rule stays: it fills forms and never saves anything.

## Current state (checked 2026-10-31)

**Warning:** another session was changing `backend/assistant/` while this plan was
written. Re-read those files before starting, and do not assume line numbers.

Already improved by that work: shared `find_party` and `find_item`, spelling tolerance
with `close_spelling`, a stock warehouse that skips groups, a Nidhi-specific prompt,
and sales figure tools.

Still missing:

| Gap | Fix |
|---|---|
| No `propose_sales_order`; the prompt maps "sold" to invoice | H-1 |
| `/selling/sales-orders/new` not in `SAFE_ROUTES` | H-1 |
| `/reports/general-ledger` in `SAFE_ROUTES` is not a real route | H-1 |
| Warehouse is simply the first non-group one | H-2 |
| Only 39 items have Hindi names, no customer does, no customer has a phone | H-3 |
| Latin and Devanagari spellings are not compared | H-3 |
| No ranking by what a customer usually buys | H-4 |
| No tools for pending orders, overdue, expenses, GST | H-5 |
| No proposals for batches, receipts as Money screens, expenses | H-6 |
| Summaries and warnings are English | L-1 |
| No eval set | H-7 |

## Pages the agent opens

| Say | Opens |
|---|---|
| "Sharma ji ka 20 peti haldi 500 gram ka order likho" | New order, filled |
| "Haldi ki peesai shuru karo 500 kilo" | New batch |
| "Gupta se 200 kilo dhaniya aaya, bill 18,400" | Purchase bill |
| "Sharma ji ne 15,000 diye" | Receipt |
| "Bijli ka bill 12,400 diya" | Expense, Paid |
| "GST kitna bharna hai?" | GST Position |
| "Kaun se order baaki hain?" | Pending Orders |
| "Kitna paisa baaki hai?" | Money to Collect |
| "Ramesh ka hisaab dikhao" | Workers page |

## Task cards

### H-1 Order proposals
- **Depends:** O-1. **Files:** `backend/assistant/proposals.py`, `tools.py`, `agent.py`,
  `prompts.py`, `tests.py`; `frontend/src/components/assistant/AssistantPanel.tsx`.
- **Do:** add `propose_sales_order`, copying `propose_sales_invoice`, with
  `SalesOrderSerializer`, target `selling.SalesOrder`, route `/selling/sales-orders/new`,
  `transaction_date` and `delivery_date`. Register it in `PROPOSAL_BUILDERS`, the tool
  schemas in `agent.py`, and the step-label map in `AssistantPanel.tsx`. Change the prompt
  so sold, order and "likho" mean a Sales Order; an invoice proposal is kept for an explicit
  request. Add `/selling/sales-orders/new` to `SAFE_ROUTES`, and correct the general ledger
  route to `/accounts/reports/general-ledger`.
- **Test:** a two-line Hindi order gives a valid proposal; an ambiguous item asks; the
  existing no-write test still passes and covers the new builder.

### H-2 Correct warehouses
- **Depends:** P-1. **Do:** in `_stock_warehouse`, use `Company.ready_warehouse` for sales
  and `Company.unprocessed_warehouse` for purchases, falling back to today's behaviour.
- **Test:** each direction picks the right warehouse.

### H-3 Hindi and Latin matching
- **Files:** `backend/assistant/tools.py`, new `backend/assistant/transliterate.py`,
  `backend/assistant/aliases.py`.
- **Do:**
  1. `to_latin(text)`: convert Devanagari to a simple Latin form, then normalise (lowercase;
     collapse `aa` to `a`, `ee` to `i`, `sh` to `s`, `v` to `w`; drop a final `a`). Prefer a
     small maintained library if one is already installed; otherwise a table is fine.
  2. In `find_matches`, compare `to_latin` forms in `_spelling_score`, so "शर्मा" and
     "Sharma" match.
  3. Add common aliases: haldi, mirchi, dhaniya, jeera, rai, and units peti, katta, bori.
  4. Strip honorifics: ji, sahab, bhai, seth.
  5. Keep the rule: a close match is a question, never a silent choice.
- **Test:** a table of at least 30 pairs, including Hindi number words (dedh, dhai, sava,
  paune) passing through the agent's quantity prompt examples.

### H-4 Rank by habit
- **Depends:** O-3. **Do:** when several items match and a customer is known, sort by that
  customer's last 90 days of order lines, and show their usual pack first. If one item
  covers at least 80% of their matching lines, return it with a warning to confirm, rather
  than a question. Show town and last order date in customer choices.
- **Test:** ranking order; no history keeps asking; no silent pick without a warning.

### H-5 Read tools
- **Depends:** O-5, M-4, M-5, E-6, T-3. **Do:** `pending_orders`, `money_to_collect`,
  `bills_to_pay`, `expense_summary`, `gst_position`, `stock_stages`. Each calls the same
  service as its screen, uses `wrap_data` for text, and checks read permission.
- **Test:** the no-write test covers them; values equal the screens.

### H-6 More proposals
- **Depends:** P-3, E-4, M-6, T-4. **Do:** `propose_batch`, `propose_receipt` (uses
  oldest-first allocation), `propose_expense`, and navigation to the new pages.
- **Test:** each makes a valid proposal and writes no document.

### H-7 Evaluation (OWNER for sentences)
Collect at least 60 real staff sentences in Hindi and Hinglish, with number words and mixed
units, as `backend/assistant/eval/cases.json`. Run through `pytest -m eval`, excluded from
default runs. Score party, item, quantity, and asking when it should.

**Target before the pilot:** 90% correct party, item and quantity, and zero silent guesses.
Then pilot with the order entry person for a week (checklist 8.7).

## Translation

### Current state

- 729 places are marked for translation.
- Hindi has only 5 translated messages, and the other 11 languages have no files.
- No compiled `.mo` file exists, so even those 5 do not show.
- About 94 `ValidationError` messages are not marked.
- The language choice already reaches the backend through `Accept-Language`.

### L-1 Mark everything
- **Do:** wrap all user-visible backend strings, including `ValidationError`, API
  `error` values, and assistant summaries, warnings and questions, in `_()` with named
  placeholders. Do not translate log lines, or machine error codes. Do this one app per
  commit. Tests asserting English text keep working, because tests run in English.
- **Test:** the full suite passes.

### L-2 Guard test
- **Depends:** L-1. **Files:** `backend/core/test_i18n.py`.
- **Do:** an AST test that fails on a `ValidationError`, `DRFValidationError` or
  `AttachmentError` message that is a plain string literal; a test that every `hi` msgid is
  translated; a test that placeholders match between msgid and msgstr.
- **Test:** the guard itself fails on a sample bad file.

### L-3 Build setup
- **Do:** add `compilemessages` to the backend Dockerfile, install `gettext` in the image,
  and keep `.mo` files out of git. Add the `makemessages` command to `docs/i18n.md`, and
  correct that file's language list (it omits Marathi).
- **Test:** build the image, or if Docker is unavailable, report that it was not verified.

### L-4 Hindi
- **Depends:** L-1. **Do:** run `makemessages -l hi`, and translate every entry using staff
  words: बिल, स्टॉक, बाकी, उधार. Keep placeholders exact. Compile, and check one real error
  in Hindi over the API with `Accept-Language: hi`.
- **Test:** L-2 passes for Hindi.

### L-5 Hindi review (OWNER)
A staff member reads the 50 most common messages and corrects them.

### L-6 Other languages
- **Depends:** all screen work finished. **Do:** generate drafts for `as bn gu kn ml mr or
  pa ta te ur`, and mark every entry `fuzzy` so they read as unreviewed. Do not report them
  as finished. Django ignores fuzzy entries, so these languages fall back to English until
  someone reviews them.
- **Test:** the placeholder test passes for all languages.

## Done when

- The eval target is met and the pilot is accepted.
- A Hindi user meets no English on any daily screen or error.
- The translation guard runs with the normal test suite.
