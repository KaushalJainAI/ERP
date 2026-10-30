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
| Q2 | Is one grinding run packed into several sizes, or held as bulk powder? | Owner | P-3 onward |
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
