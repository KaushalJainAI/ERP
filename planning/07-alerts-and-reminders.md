# 7. Alerts and reminders

## Goal

Nothing important is missed, and nobody is buried in messages. **Email is the main
channel**, because it reaches a person on web and phone with no app to install. An in-app
bell shows the same alerts inside the ERP.

Decision (owner, 2026-10-31): email, in the style NGU already uses. No phone push or
WhatsApp for now.

## What we learn from NGU

Studied: `NGU/Backend/orders/emails.py`, `payments/emails.py`,
`admin_panel/management/commands/send_daily_digest.py`, `payments/.../run_scheduler.py`,
and the admin `Dashboard.tsx`.

| NGU does | ERP does |
|---|---|
| Short, plain-language emails for a non-technical owner | Same, in the reader's language |
| A morning digest at 08:00 instead of many emails | Same, one per person |
| A weekly summary on Monday at 08:30 | Same |
| Instant email only for things needing action now | Same: low stock and integrity failures |
| `run_scheduler` as its own compose service | Same; no Celery |
| Gmail SMTP through `EMAIL_*` env vars, `EMAIL_TIMEOUT=10` | Same env names |
| `ADMIN_ALERT_EMAIL` fallback address | Same |
| Admin dashboard refreshes every 60 seconds | Bell count refreshes every 60 seconds |
| `transaction.on_commit`, so a rolled-back action never emails | Same |
| Best-effort background thread, 3 retries, then a log line | **Improved:** durable `Outbox` row. NGU's own comment says an outbox is the real fix, and mandate 1 requires it |

An email that fails is retried, and after the limit it is marked `dead` and shown to the
Admin. Nothing is silently lost.

## Who gets what

| Rule key | Trigger | Default to | When |
|---|---|---|---|
| `customer_due_soon` | Bill due within 7 days | Accountant, Admin | Morning digest |
| `customer_overdue` | Past due (90 days by default) | Accountant, Admin | Morning digest |
| `supplier_bill_due` | Due within 7 days or late | Accountant, Admin | Morning digest |
| `expense_due` | Due within 7 days or late | Accountant, Admin | Morning digest |
| `tax_due` | GST or TDS due in 7 days, 2 days, or late | Accountant, Admin | Morning digest |
| `itc_at_risk` | Unpaid bill with GST at day 150 or later | Accountant, Admin | Weekly summary |
| `order_pending` | Approved, undispatched past the set days (default 2) | Factory manager, Admin | Morning digest |
| `order_short` | Pending orders need more than Ready holds | Factory manager, Admin | Morning digest |
| `raw_needed` | BOM shows raw material short | Admin | Morning digest |
| `proof_missing` | Saved entry has no proof | Accountant | Morning digest |
| `ready_low` | Ready falls below `critical_stock` | Factory manager, Admin | **Instant**, once a day per item |
| `email_dead` | An email failed all retries | Admin | Next digest and bell |
| `integrity_failed` | `verify_integrity` fails | Admin | **Instant** |

**The Admin receives every bill reminder**, as the owner asked. `AlertRule` stores
recipients, so this table is a default that can change without code.

## Example digest

Subject: `Nidhi ERP: आज का काम, 3 Nov`

```
नमस्ते Kaushal, आज ध्यान देने वाली बातें:

पैसा लेना है
  12 बिल की तारीख निकल गई: ₹4,20,000
  सबसे पुराना: Sharma Traders, ₹85,000, 34 दिन देर

पैसा देना है
  इस हफ्ते 3 बिल: ₹85,000
  बिजली का बिल, ₹12,400, कल तक

टैक्स
  GST ₹31,200, 20 Nov तक

फैक्ट्री
  5 ऑर्डर 2 दिन से ज़्यादा बाकी
  Haldi 500gm की Ready स्टॉक कम: 40 बचा

खोलें: https://erp.../
```

An email with nothing to report is not sent.

## Data

New app `notifications`:

- `AlertRule`: `key` (unique), `is_active`, `recipients` (M2M to User), `days` (nullable
  integer threshold).
- `Notification`: `user`, `rule_key`, `message_key`, `params` (JSON), `link`, `read_at`,
  `created_at`, `dedupe_key`. Unique on `(user, dedupe_key)`.

Add `Item.critical_stock` `(18,3)`, default 0, CHECK `>= 0`. Zero means no alert.

Store a message key and params, not finished text, so each person reads their own
language.

## Email settings

Add to `backend/config/settings.py`, using `env(...)` with these defaults:

| Setting | Default |
|---|---|
| `EMAIL_BACKEND` | `django.core.mail.backends.console.EmailBackend` |
| `EMAIL_HOST` | `smtp.gmail.com` |
| `EMAIL_PORT` | `587` |
| `EMAIL_HOST_USER` | empty |
| `EMAIL_HOST_PASSWORD` | empty |
| `EMAIL_USE_TLS` | `True` |
| `DEFAULT_FROM_EMAIL` | `EMAIL_HOST_USER` |
| `EMAIL_TIMEOUT` | `10` |
| `ADMIN_ALERT_EMAIL` | `EMAIL_HOST_USER` |
| `FRONTEND_URL` | `http://localhost:5173` |

The default backend prints to the console, so development and tests never send real mail.
Staff need real addresses on `User.email`.

## Task cards

### N-1 Email settings
- **Do:** add the settings above; add the variable names, with blank values, to the sample
  env file if one exists.
- **Test:** the settings load with no env vars and use the console backend.

### N-2 Notifications app
- **Depends:** none. **Files:** new `backend/notifications/`, `config/settings.py`,
  `config/urls.py`.
- **Do:** create the app and the two models above; add it to `INSTALLED_APPS`; seed the
  `AlertRule` rows through a function in `notifications/seed.py`, called from
  `seed_defaults`, following `hr/seed.py`. Add `critical_stock` to `Item` with its CHECK,
  serializer field and form field.
- **Test:** the unique constraint, the CHECK, and seeding twice creates no duplicates.

### N-3 Outbox sender
- **Depends:** N-1. **Files:** `notifications/services/mail.py`,
  `notifications/management/commands/send_outbox.py`.
- **Do:** `queue_email(to, subject, body, dedupe_key)` creates `Outbox(channel="email")`,
  ignoring an existing `dedupe_key`. `send_outbox` selects pending rows with
  `next_attempt_at` empty or past (`select_for_update(skip_locked=True)`), sends with
  `send_mail`, and sets `sent`. On failure it increments `attempts`, sets
  `next_attempt_at` to 5, 15 then 60 minutes later, stores `last_error`, and after 4
  attempts sets `dead`.
- **Test:** success, retry timing, dead after 4, a duplicate key is ignored, and one bad
  row does not stop the rest.

### N-4 Rules engine
- **Depends:** N-2, M-4, M-5, E-3, T-3, P-1, O-2. **Files:**
  `notifications/services/rules.py`.
- **Do:** one small function per rule key, each returning a list of findings (message
  key, params, link, record id). Reuse the Collections, Bills to Pay, tax position and
  pending order services. Do not write new money queries. `create_notifications(rule,
  findings)` creates a row per recipient, with `dedupe_key = f"{rule}:{record}:{date}"`.
- **Test:** for every rule: fires on the boundary, does not fire the day before, stops
  when paid, and repeated runs create nothing new.

### N-5 Raw material shortage
- **Depends:** N-4, P-3. **Files:** `notifications/services/shortage.py`.
- **Do:** for pending order shortfalls and items under critical level, multiply through
  the default BOM and compare with Unprocessed `Bin`. Items with no BOM are returned in a
  separate "no recipe" list. Use `Decimal` and quantize to 3 places.
- **Test:** a single-level BOM, several orders sharing one raw material, and no BOM.

### N-6 Digest and weekly emails
- **Depends:** N-3, N-4. **Files:** `notifications/services/digest.py`,
  `commands/send_daily_digest.py`, `commands/send_weekly_summary.py`.
- **Do:** group each user's unread findings by section (as in the example), render inside
  `translation.override(user.language)`, and queue through `queue_email`. Use plain text,
  like NGU. Skip when empty. `dedupe_key = f"digest:{user.pk}:{date}"`. Use
  `timezone.localdate()`, not `.date()`.
- **Test:** Hindi and English output, the empty case, and running twice queues one email.

### N-7 Instant alerts
- **Depends:** N-3, N-4. **Do:** after dispatch and batch completion, use
  `transaction.on_commit` to run `ready_low` for the affected items. `verify_integrity`
  failure queues `integrity_failed`.
- **Test:** a rolled-back dispatch sends nothing; a second drop on one day sends nothing.

### N-8 Scheduler
- **Depends:** N-6. **Files:** `notifications/management/commands/run_scheduler.py`,
  `docker-compose.yml`.
- **Do:** a loop modelled on NGU's `run_scheduler`: `send_outbox` every minute,
  `run_alerts` and `send_daily_digest` at 08:00 IST, `send_weekly_summary` on Monday at
  08:30, `verify_integrity` at 02:00. Catch and log an exception per job, so one failure
  cannot stop the loop. Add a `scheduler` compose service using the backend image. Read
  NGU's `Backend/payments/management/commands/run_scheduler.py` first.
- **Test:** a job that raises does not stop the next one; a job runs once in its slot.

### N-9 Bell and alert settings
- **Depends:** N-2. **Files:** `notifications/views.py`, `urls.py`,
  `frontend/src/components/NotificationBell.tsx`, `TopBar.tsx`,
  `frontend/src/pages/AlertSettingsPage.tsx`.
- **Do:** `GET /api/notifications/` (own rows only), `POST .../{id}/read/`,
  `POST .../read-all/`, and rule settings endpoints. A bell in `TopBar` with an unread
  count polling every 60 seconds, a list, and a tap opening the link. A settings page
  listing each rule, its recipients and days. Update `API.md`.
- **Test:** a user cannot see or mark another's notifications.

### N-10 Turn it on (OWNER)
Provide the sending address and a Gmail app password in the server environment, confirm
each staff email, and set recipients. Check the first digest by hand.

## Done when

- The Admin receives a true morning digest for a week.
- Each email link opens the right page.
- A forced SMTP failure retries and later succeeds.
- Staff say the volume is manageable.
