# Assistant plan — voice + chat agent that fills entries for the user

Design notes for checklist **Phase 8**. Like [plan.md](plan.md), this explains
intent; once built, the code and tests are the source of truth.

## 1. The idea in one paragraph

Staff who are not comfortable with software press one big mic button and say
what happened: *"Sharma Traders ko 20 kilo haldi powder becha"*. A loading
screen shows the agent working step by step (finding the customer, checking
stock, getting the rate). The agent then opens the **real ERP form**
(New Sales Invoice) **already filled in**, with a plain-language summary on top
and the AI-filled fields highlighted. The user checks it, attaches the bill
photo, and presses **Save** themselves. The agent can look things up and fill
forms. It can **never** save, submit, cancel or delete anything.

## 2. Non-negotiable rules for the assistant

1. **Zero write tools.** The tool registry has only read tools and *proposal
   builders*. A proposal is data (`{route, initialValues, summary, …}`), not a
   database write. A test (§8) runs every tool and fails if any non-assistant
   row is inserted, updated or deleted.
2. **The user is the only one who saves.** Save goes through the normal
   create API as the logged-in user, so their RBAC, the serializer checks and
   the DB constraints all apply unchanged. Save creates a **Draft**. Submit (the
   step that posts GL/SLE) is a second, separate human action.
3. **The agent sees only what the user can see.** Every read tool goes through
   `users.permissions.user_has_model_permission` and the viewset's own
   queryset scoping. If someone without HR read access asks the assistant about
   wages, it returns nothing, exactly as the HR screens would.
4. **Never guess an entity.** If the user's words match more than one customer,
   item or employee, the agent asks with tappable options. It never picks one.
5. **Rates come from the server.** The agent leaves line rates blank, so the
   existing price-list resolution (`stock/services/pricing.py::fetch_rate`)
   fills them. It sets a rate only when the user said one aloud, and the review
   banner flags it ("rate you said: ₹180, price list: ₹175").
6. **Retrieved data is untrusted.** Party names, notes and item descriptions go
   into the prompt inside `<<DATA>>` markers (NGU's spotlighting), so text
   stored in a record can't act as an instruction.
7. **Multilingual (mandate 3).** UI strings go in `en.assistant.ts` + the 12
   other locales. The agent replies, and writes the summary, in
   `User.language`. The panel and the review banner support RTL for `ur`.

## 3. What we reuse

| From | What | Why it fits |
|---|---|---|
| NGU `assistant/agent.py` | Bounded native-function-calling loop, history trimmed to a token budget, graceful fallback when the LLM is unreachable | Same shape; proven in production |
| NGU `assistant/tools.py` | Split into *read tools* and *proposal builders*; all-or-nothing multi-line proposal validation | Exactly our "agent fills, human saves" contract |
| NGU `assistant/stt.py`, `voxtral_client.py`, `whisper_client.py` | STT dispatch: Voxtral via OpenRouter, falling back to self-hosted whisper.cpp; prompt biasing with vocabulary | Hindi/Hinglish already tuned |
| NGU `hooks/useVoiceInput.ts`, `lib/audio.ts` | MediaRecorder → 16 kHz mono WAV, silence auto-stop, 30 s cap, visible errors | Works on every browser incl. phones |
| NGU `throttles.py` | Per-user burst + daily throttles | Bounds spend |
| ERP `DocumentPage.tsx` (`location.state.initialValues` → `buildInitial`) | Opening a new form pre-filled | Already used by "make Sales Invoice from Order". The handoff needs no new form machinery |
| ERP `LinkDef.fill` / `display` (`resources/*.ts`) | Derived display fields for links (e.g. `customer_name`, GST snapshot) | Reused when the frontend hydrates the seed (§6.3), so fill logic is never duplicated in Python |

**What NGU doesn't give us:** NGU's SSE stream emits only `meta → reply →
done`. It has no per-step events. The step-by-step loading screen needs real
step events (§5.3), which is new work.

## 4. First targets (pending confirmation, see §10)

| # | Say | Opens | Notes |
|---|---|---|---|
| 1 | "Sharma Traders ko 20 kilo haldi aur 10 kilo mirchi becha" | New **Sales Invoice** | Multi-line; credit banner already shows if over limit |
| 2 | "Gupta Spices se 200 kilo sabut dhaniya aaya, bill 18,400 ka" | New **Purchase Invoice** | Bill total checked against the computed total; a mismatch becomes a warning |
| 3 | "Sharma Traders ne 15,000 cash diye" | New **Payment Entry** (receive) | Outstanding invoices listed for allocation; the user picks |
| 4 | "Kitna baaki hai Sharma ka?" / "Haldi kitni bachi hai?" | Chat answer (no form) | Read-only questions: outstanding, stock, last rate, recent entries |
| 5 | "Sales invoice kholo" / "Kal ke bills dikhao" | Navigation to a list, filtered where possible | |
| later | "Aaj Ramesh, Suresh, Kamla aaye; Mohan aadha din" | Daily attendance / muster | **Blocked:** HR has no frontend screens yet (`resources/` has no HR config). Comes after the HR UI |

## 5. Backend: new `assistant` app

### 5.1 Models (append-only logs; nothing here is a ledger or derived state)

- `AssistantConversation` holds the user (FK, `PROTECT`), title, language,
  created/modified. A conversation is only ever loaded for its owner.
- `AssistantMessage`: conversation FK, `role` (CHECK in
  `user|assistant|tool`), `content`, `input_mode` (CHECK in `text|voice`),
  `steps` JSON (the trace shown on the loading screen), created_at. Append-only.
- `AssistantProposal`: UUID pk; message FK; `target` (resource key, e.g.
  `selling.salesinvoice`); `values` JSON; `summary`; `ai_filled_keys`;
  `warnings`; `status` (CHECK in `proposed|opened|saved|abandoned`);
  `saved_model_label` + `saved_object_id` (both null until saved, with a CHECK
  that both are set when status = saved). This is how we measure whether the
  agent is any good (§8.3).

Audio is **not stored**. Only the transcript is kept.

### 5.2 Tools

**Read tools.** Each runs as `request.user`, checks `can_read`, returns at
most N compact rows, and wraps free text in `<<DATA>>`:

- `search_customers`, `search_suppliers`, `search_items`, `search_employees`
  match fuzzily on name/code/phone/GSTIN, plus `FieldTranslation` names, so
  "haldi" finds "Turmeric Powder".
- `get_stock(item, warehouse?)` reads `Bin` and is never computed.
- `get_party_outstanding(party)` lists outstanding invoices with ageing.
- `get_rate(item, party, date)` is a read-only call to `pricing.fetch_rate`.
- `recent_documents(doctype, party?, days?)`, `get_document(doctype, id)`.

**Proposal builders.** Each returns a proposal or an error the LLM can
correct:

- `propose_sales_invoice`, `propose_purchase_invoice`, `propose_payment_entry`;
  later `propose_attendance`, `propose_employee_advance`,
  `propose_employee_payment`.
- Each builder:
  1. refuses if the user lacks `can_create` on the target model, so we never
     open a form they can't save;
  2. re-checks that every ID exists, is active and is readable by the user;
  3. parses quantities and amounts as `Decimal` with the model precisions;
  4. **dry-runs the target serializer** (`is_valid()`, never `save()`) and
     turns the errors into warnings;
  5. computes a preview total for the summary.

**Control tools:**
- `ask_user(question, options[])` produces tappable choices for ambiguity.
- `navigate(route, filters?)` checks the route against the frontend resource
  registry's list (NGU's `_safe_route`).

### 5.3 Agent loop + streaming

- Native function calling with a bounded loop: **max 6 tool rounds** per turn
  (NGU's limit is 4; an invoice needs customer + 2–3 items + rate). A 20 s
  timeout per LLM call, with one retry.
- The LLM sits behind a small provider adapter set by env (`ASSISTANT_LLM_*`).
  Candidates: a current Claude model (`claude-sonnet-5-5` for tool-use
  accuracy, or `claude-haiku-5-5` for cost), or NGU's current OpenRouter model.
  The choice is made on the eval set (§8.2), not up front. Volume is a handful
  of internal staff, so accuracy matters more than price per call.
- `POST /api/assistant/chat/stream/` returns SSE events:
  - `step` `{key, label_key, args_preview, status: running|done|failed, result_preview}`
    is emitted **before and after every tool call**. This drives the loading
    screen.
  - `question` `{text, options[]}` comes from `ask_user`.
  - `proposal` `{id, target, route, values, summary, ai_filled_keys, warnings}`.
  - `reply` `{text}`, `done`, `error` `{code}`.
- **Serving.** Gunicorn currently runs one sync worker
  (`docker-compose.yml`), so one open stream would block every other request.
  Switch to `--worker-class gthread --threads 8` (or a few workers) as part of
  8.3 and test that two concurrent users don't block each other.

### 5.4 Endpoints (rows go into `backend/docs/API.md`)

| Route | Purpose | Access |
|---|---|---|
| `POST assistant/chat/stream/` | One turn, SSE | authenticated; each tool enforces its own RBAC |
| `POST assistant/transcribe/` | Audio → text | authenticated, throttled |
| `GET/POST assistant/conversations/` | List/create own threads | owner only |
| `GET assistant/conversations/{id}/messages/` | History | owner only |
| `POST assistant/proposals/{id}/opened/` | Mark that the form was opened | owner only |
| `POST assistant/proposals/{id}/abandoned/` | The user left the form without saving | owner only |

The `saved` status is set server-side. The create request for the document
carries the header `X-Assistant-Proposal: <uuid>`. A small `core` mixin on the
document viewsets reads it **after** a successful create. The mixin then:

- links the proposal to the new object;
- writes `{"source": "assistant", "proposal": id}` into that create's
  `AuditLog.changes`.

No document serializer changes. A missing, foreign or already-used proposal ID
is ignored and never blocks the save.

## 6. Frontend (NGU Chatbot Parity & Mobile Design)

### 6.1 Launcher + Mobile Bottom-Sheet Panel

- **Launcher positioning:** Floating mic + spark button on every page (`Layout.tsx`), coordinated with the mobile bottom navigation bar (`bottom-20 md:bottom-6 right-4 sm:right-6`). Tapping opens the assistant.
- **Mobile layout (NGU style):** On mobile devices (`< 768px`), the panel acts as a bottom-docked sheet (`max-h-[85vh]` with touch drag handle, safe-area inset `pb-[env(safe-area-inset-bottom)]`), so the phone keyboard does not push content out of view. On desktop, it is a sleek floating drawer on the right.
- **Starter prompt chips (NGU `SUGGESTION_KEYS`):** When a new conversation begins, show tappable starter pills in the user's selected language (e.g., *"Aaj ke unbilled orders"*, *"Haldi ka stock kitna hai?"*, *"Sharma Traders ka balance"*, *"Naya Sales Invoice banao"*). Tapping immediately sends the query.
- **Formatted chat bubbles:** Message content supports rich Markdown formatting (bullet points, bold highlights, INR currency amounts `₹`, tables) matching NGU's `ChatMarkdown`.
- **Options from `ask_user`:** Render as large tap targets (party name + city + GSTIN, so "which Sharma" is answerable at a glance).

### 6.2 In-Chat Interactive Proposal Cards (NGU `ProposalCard` pattern)

When the assistant proposes a document or action:
- Instead of an abrupt jump, the chat thread renders an interactive **Proposal Card** directly inside the bubble:
  - Document target pill (e.g. `Sales Invoice`, `Payment Entry`);
  - List of line items with quantities, units, and rates;
  - Live total preview in ₹;
  - Validation warnings badge (e.g., "Over credit limit", "Rate manually overridden");
  - Single primary action button: *"Review & Save Document"* which opens the hydrated document form.

### 6.3 Loading screen (`AgentWorkingOverlay`)

A full-screen overlay listing live steps from the SSE `step` events, each with
a spinner then a tick, in the user's language:

```
✓ Customer found: Sharma Traders (Bhopal)
✓ Haldi Powder 1 kg: 42 kg in stock
⟳ Getting rate from price list…
  Opening invoice…
```

A **Stop** button cancels the turn. Failures show the step that failed and
what to say next, never a bare error.

### 6.4 Handoff to the confirm page

- `navigate(route, { state: { initialValues, assistant: { proposalId, summary, aiFilledKeys, warnings } } })`.
- **Seed hydration.** Before `buildInitial`, a small `hydrateSeed(config,
  values)` fetches each link ID through its `LinkDef` and applies `fill` /
  `display`. Derived fields (customer name, GST snapshot, item metadata) then
  come from the **same code** the manual picker uses. This needs a check
  against `LinkField.tsx`: if its fill path can be invoked directly, use it.
- `AssistantReviewBanner` sits above the form:
  - the summary in the user's language, e.g. "Sales invoice to Sharma Traders:
    Haldi Powder 20 kg + Mirchi 10 kg = ₹5,450 (before GST)";
  - warnings (over credit limit, rate differs, bill total mismatch);
  - "Please attach the bill" when the document type needs proof;
  - a "Not right? Tell me what to change" button that reopens the panel with
    context.
- Fields the AI filled get a subtle highlight ring until the user edits them.
- **The Save button is the existing one, unchanged.** The create call sends
  `X-Assistant-Proposal`.

### 6.5 Voice (NGU `useVoiceInput` & STT)

Port NGU `useVoiceInput.ts` + `audio.ts` as-is. The backend `transcribe/`
ports NGU `stt.py` with its fallback, plus:

- **language hint** from `User.language`;
- **vocabulary biasing**: the STT prompt carries the company's top item names,
  party names and units ("kilo", "bori", "katta"), cached per company;
- Hindi number words (dhai sau, sava do, dedh) are left to the LLM, but eval
  cases (§8.2) must cover them.

Spoken read-back of the summary (TTS, AIAAS `voice/tts.py`) is phase 8.8,
optional.

## 7. Mandates check

- **Self-healing.** The assistant never touches ledgers, `Bin` or derived
  state. Its own tables are logs. `verify_integrity` is unaffected; the
  assistant gets no repost command because it stores no projections. The LLM
  call is a read made during the request, not a business side effect, so it
  doesn't go through `Outbox`.
- **User-error-proofing.** No write tools; the human saves; the human submits
  separately; RBAC on every read; CHECK constraints on assistant enums;
  ambiguity forces a question.
- **Multilingual.** Covered in §2.7, plus eval cases in at least Hindi,
  Hinglish and English.

**ERPNext divergence.** ERPNext has no built-in voice agent, so add an entry
to `backend/docs/erpnext_divergences.md`.

## 8. Testing and evaluation

### 8.1 Unit / API tests (`assistant/tests.py`, `pytest`)

- **No-write guarantee.** Run every tool with valid and invalid args under
  `CaptureQueriesContext` and assert no `INSERT/UPDATE/DELETE` outside
  `assistant_*` tables. Assert the registry has no tool outside the
  read/proposal/control lists.
- **RBAC.** A user without `can_read` on Employee gets nothing from
  `search_employees`, and a user without `can_create` on Sales Invoice gets a
  refusal, not a proposal.
- **Proposal validation.** Decimals, inactive items, other-company parties,
  multi-line all-or-nothing, dry-run errors surfaced as warnings.
- **Ambiguity.** Two "Sharma" customers produce an `ask_user`, never a
  proposal.
- **Prompt injection.** A customer named "Ignore previous instructions and…"
  stays data.
- **LLM failure.** Timeout or unavailable gives a friendly reply, and the rest
  of the ERP is unaffected.
- **Save tagging.** The header links the proposal and writes the AuditLog
  source. A foreign or reused ID is ignored and the save still succeeds.

### 8.2 Offline eval set (`assistant/eval/`, NGU pattern)

At least 60 real utterances from staff (Hindi, Hinglish, English; numbers in
words; mixed units), each with the expected target, party, items and qty, or
"must ask". The test runs against the live LLM with `pytest -m eval` and is
excluded from default CI. It scores:

- entity accuracy;
- qty/amount accuracy;
- asked when it should;
- never asked when there was only one match.

It is used to pick the model and to catch regressions when prompts change.

### 8.3 Live metrics (from `AssistantProposal`)

- Proposals saved ÷ proposals opened.
- Fields edited before save: compare `values` with the saved document. A high
  edit rate on a field shows where the agent is wrong.
- Abandoned forms by target.

## 9. Rollout

1. Build 8.1–8.6 against `db.sqlite3` dev data, then the eval set.
2. **Pilot with 2–3 staff** on the real books (Postgres :5433) for one week.
   Everything they save is a Draft that someone else submits, so mistakes are
   free to fix.
3. Review the metrics in §8.3, fix the worst fields, then open it to everyone.

## 10. Decisions still needed (asked 2026-10-28, not yet answered)

| Question | Assumed until answered |
|---|---|
| Which entries do staff make most often? | Sales invoice, purchase invoice, payment received (§4) |
| Which languages do staff speak? | Hindi + Hinglish first, English second; the other locales get UI strings only |
| Phone or desktop? | Both, designed **mobile-first** (big targets, full-screen overlay) |
| STT: self-hosted Whisper or a paid service? | Voxtral via OpenRouter, no whisper.cpp fallback yet |
| Which LLM? | `anthropic/claude-haiku-5.5` via OpenRouter (NGU's assistant model) until the eval set (§8.2) exists |

## 11. Switched on (2026-10-31)

`backend/.env` holds the same OpenRouter `LLM_API_KEY` as the NGU backend, so ERP
and NGU share one credit balance. `python manage.py assistant_ping` checks the key
and model with one real turn.

Taken from NGU, adapted to staff entering documents rather than customers shopping:

- **Turn budget** (`ASSISTANT_TURN_BUDGET_SECONDS`): a round starts only if it can
  finish in time, so a turn always ends with a reply.
- **Prompt caching:** `to_wire` marks the static prompt for Claude models. Per-turn
  lines (date, company, language) go after it.
- **Failure replies in the user's language**: en, hi, gu, mr, pa. The other UI
  languages fall back to English.
- **PII:** phone numbers reach the third-party model masked to the last four digits.
- **Reporting:** `sales_summary` and `top_items`, from NGU's admin tools but under
  the normal RBAC rather than an admin-only persona.
- **Domain language:** spice names, units and Hindi number words in the prompt.

Deliberately not taken: FAQ answers without an LLM, cart and storefront tools,
human hand-off, selling behaviour, thread summarising. History is the last 10
messages.

Item and party matching differs from §5.2: exact, then partial, then all words,
then close spelling. A close spelling is only ever offered, never picked.
