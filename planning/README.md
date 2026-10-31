# Execution plan: Nidhi Masala ERP

Started 2026-10-31. These documents turn the owner's real working day into build
work. The code stays the source of truth; update these files when a decision
changes. Task ticks are in [checklist.md](../checklist.md) Phase 10, and the gap
history is in [gap_plan.md](../gap_plan.md) section 0A.

**Implementers start with [the implementer guide](00-implementer-guide.md).**

## What we are building

A simple system for three kinds of work, used mostly by people who are not
comfortable with software. Hindi voice is the main way in.

| Workspace | Used by | Daily job |
|---|---|---|
| Orders | Order entry person | Say the order in Hindi, check it, approve it |
| Factory | Factory manager | Move batches, tick dispatch, see workers |
| Accounts | Accountant | Bills, receipts, payments, expenses, GST and TDS |

Admin has all three.

## The documents

| # | Document | Covers | Cards |
|---|---|---|---|
| 0 | [Implementer guide](00-implementer-guide.md) | Rules, recipes, commands, stop conditions | – |
| 1 | [Access and workspaces](01-access-and-workspaces.md) | One access level, landing pages, hidden menus | A |
| 2 | [Orders, dispatch and billing](02-orders-dispatch-billing.md) | Order to bill in one tick | O |
| 3 | [Production and stock](03-production-and-stock.md) | Unprocessed, Grinding, Packing, Ready | P |
| 4 | [Collections and supplier payments](04-collections-and-payables.md) | The 90-day cycle, money to collect, bills to pay | M |
| 5 | [Expenses](05-expenses.md) | Every expense tracked as Paid or Due | E |
| 6 | [Taxes](06-taxes.md) | GST input credit, GST to pay, employee TDS | T |
| 7 | [Alerts and reminders](07-alerts-and-reminders.md) | Email digests and instant alerts, modelled on NGU | N |
| 8 | [Hindi agent and translation](08-hindi-agent-and-translation.md) | Voice orders, prefilled pages, translated messages | H, L |
| 9 | [Go-live](09-go-live.md) | Opening balances, stock count, cutover | G |
| 10 | [Subagent prompt](10-subagent-prompt.md) | The prompt that runs this whole plan | – |

## Owner decisions

| Decision | Date |
|---|---|
| One access level; all staff are trusted; Admin has all three workspaces | 2026-10-31 |
| No lot tracking and nothing tracked after dispatch | 2026-10-31 |
| A batch is a production run: Grinding, Packing, Ready | 2026-10-31 |
| Stock can never go negative; enforce sensible rules in the database | 2026-10-31 |
| Customers pay in 90 days as standard | 2026-10-31 |
| Track collections and supplier payments; overdue reminders go to the responsible user | 2026-10-31 |
| Remind the Admin about pending bills | 2026-10-31 |
| Notifications are by email, in the NGU style, plus an in-app bell | 2026-10-31 |
| Expense tracking is a main goal; status is Paid or Due | 2026-10-31 |
| Tax scope is only employee TDS, GST input credit and tax payable to the government | 2026-10-31 |
| Salary TDS follows the government rules for salaried (Monthly) staff above the taxable limit: new regime by default, old regime as an opt-in per employee, rates kept in a dated table the accountant edits. Daily wagers get none | 2026-10-31 |
| Opening balances for accounts and stock are required | 2026-10-31 |
| Operators enter in the ERP; Tally stays the official books | 2026-10-28 |

## Build order

Cards may run in parallel only within the same wave.

| Wave | Cards | Notes |
|---|---|---|
| 1 Foundations | A-1, A-2, A-3, A-5, P-1, P-2, M-1, N-1, N-2, L-1, L-2, L-3 | Mostly separate apps |
| 2 Order to bill | O-1, O-2, O-3, O-4, O-5, O-6, M-2, M-3 | Same files, run one at a time |
| 3 Money | M-4, M-5, M-7, M-8, M-6, E-1, E-2, E-3, E-4, E-5, E-6, E-8 | M then E |
| 4 Taxes and factory | T-1, T-2, T-3, T-4, T-5, P-3, P-4, P-5, P-6 | T and P are parallel. P-3 needs Q2 |
| 5 Alerts | N-3, N-4, N-5, N-6, N-7, N-8, N-9, E-7 | |
| 6 Agent | H-1 to H-6, L-4 | Check no other session is editing `assistant/` |
| 7 Go-live tools | G-1 to G-7, A-4, A-6, T-6, T-7 | T-6 and T-7 need Q5 |
| 8 Last | L-6, then OWNER cards | |

OWNER cards: A-7, O-7, P-7, E-9, T-8, N-10, H-7, L-5, G-8.

## Open questions

| # | Question | Who | Blocks |
|---|---|---|---|
| Q1 | Has turnover passed ₹5 crore in any year since 2017-18? Do consignments cross the e-way bill limit? | Accountant | O-7, may change O-3 |
| Q2 | Is one grinding run packed into several sizes, or held as bulk powder? **Answered: grind to bulk, then pack per SKU (see doc 03).** | Owner | P-3 onward |
| Q3 | Is ready stock counted in packets or kg? | Owner | P-7 |
| Q4 | What is the standard supplier payment period? (Customers: 90 days, answered) | Owner | Default in M-1 |
| Q5 | Which staff, if any, are above the income tax limit? | Accountant | T-6, T-7 |
| Q6 | Which email account sends alerts, and what is each staff member's address? | Owner | N-10 |

Record each answer here, with the date, when it is given.

## Definition of done for every card

- Database constraints exist, and tests pass.
- Lint is clean.
- New user-facing text is translatable in frontend and backend.
- `backend/docs/API.md` is updated for route, permission or serializer changes.
- Deliberate ERPNext differences are in `backend/docs/erpnext_divergences.md`.
- The card is ticked in `checklist.md`, in one commit.

## Implementation status (updated 2026-10-31)

### ✅ Complete (committed on `plan/phase-10`)

| Wave | Cards done | Commit range |
|---|---|---|
| 1 Foundations | A-1, A-2, A-3, A-5, P-1, P-2, M-1, N-1, N-2, L-1, L-2, L-3 (12/12) | `c8f73e5`–`aa21088` |
| 2 Order to bill | O-1, O-2, O-3, O-4, O-5, O-6, M-2, M-3 (8/8) | `365f222`–`566adf5` |
| 3 Money & Expenses | M-4, M-5, M-6, M-7, M-8, E-1–E-6, E-8 (13/13) | `1a30ef9`–`4c7bdcc` |
| 4 Taxes & Factory | T-1, T-2, T-3, T-4, T-5, P-5 (6/6) | `d4b3aea`–`b6bbe53` |
| 5 Alerts | N-3, N-4, N-5, N-6, N-7, N-8, N-9, E-7 (8/8) | `bc66696`–`7ae8b9d` |
| 6 Agent (partial) | H-1, H-2, H-3, L-6 (4/7) | `cca28f2`–`be91bdb`, `7972600` |
| 7 Go-live (partial) | G-6, G-7 (2/9) | `83c6c0f`, `ef27781` |

**Total committed: 53 cards.**

### 🔶 Remaining (not yet committed)

| Card | Wave | What it does | Notes |
|---|---|---|---|
| H-4 | 6 | Rank items by 90-day customer purchase history | Subagent was mid-implementation |
| H-5 | 6 | Read tools (pending orders, money, stock, GST) | Subagent was mid-implementation |
| H-6 | 6 | Batch, receipt, expense proposals | Subagent was mid-implementation |
| L-4 | 6 | Hindi `.po` full translation and compiled `.mo` | Subagent was mid-implementation |
| A-4 | 7 | Workspace home pages (Orders, Factory, Accounts, Admin) | Subagent was mid-implementation |
| A-6 | 7 | Plain words in i18n keys (Approve, Bill, Dispatch, Paid, Due) | Not started |
| G-1 | 7 | Opening bills model and allocation | Subagent was starting |
| G-2 | 7 | Opening bills generator (dry-run/commit) | Not started |
| G-3 | 7 | Accountant CSV corrections | Not started |
| G-4 | 7 | Integrity check for opening bill outstanding | Not started |
| G-5 | 7 | Keep opening bills and stock out of Tally export | Not started |

### 🚫 Skipped (by plan design)

| Card | Reason |
|---|---|
| A-7 | OWNER: remove demo company |
| O-7 | OWNER: real Tally test |
| P-3, P-4, P-6 | Q2 answered (bulk then pack); unblocked |
| P-7 | OWNER + BLOCKED Q3 |
| E-9 | OWNER: category list |
| T-6, T-7 | Built 2026-10-31 under the owner's TDS decision. Q5 (which staff are above the limit) is still open for the accountant |
| T-8 | OWNER: rule check |
| N-10 | OWNER: turn on notifications |
| H-7 | OWNER: evaluation sentences |
| L-5 | OWNER: Hindi review |
| G-8 | OWNER: cutover |

### Next steps for a resuming session

1. Resume Wave 6 remaining: H-4, H-5, H-6, L-4.
2. Resume Wave 7 remaining: A-4, A-6, G-1, G-2, G-3, G-4, G-5.
3. Wave 8: full test suite (`pytest -q`), `makemigrations --check --dry-run`,
   `npm run build`, `verify_integrity` on SQLite, handover report.
4. Answer open questions Q2, Q5, Q6 to unblock P-3/P-4/P-6 and T-6/T-7.
