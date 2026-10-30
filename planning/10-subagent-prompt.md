# 10. Prompt to run the whole plan

Paste everything inside the block below into a new Claude Code session, at the repository
root, using the strongest model available.

## Before you start

1. Commit or stash the current work. Worktrees start from the last commit, and over 100
   files were uncommitted when this was written, including this plan.
2. Close other sessions working on this repository.
3. Answer as many questions in [README.md](README.md) as you can.
4. Take a backup of the real database.

## The prompt

```text
You are the lead engineer for the Nidhi Masala ERP. Your job is to finish the plan in
planning/ by coordinating subagents. You design, review and merge. They implement.

FIRST, READ:
- CLAUDE.md
- planning/README.md
- planning/00-implementer-guide.md
- every other document in planning/
- checklist.md Phase 10

PRE-FLIGHT. Stop and tell me if any of these fail:
1. `git status --short` is clean, and planning/ is committed. If not, list what is dirty
   and ask me to commit it. Do not commit or stash it yourself.
2. No other session is editing this repository. Check with ListAgents.
3. Tests pass on main: `python -m pytest -q` from backend/, with DATABASE_URL set to a
   SQLite file, and `npm run build` from frontend/. Record the baseline, including any
   failure that already exists.
4. Create the branch `plan/phase-10` from main. All merges go there. Never push it, and
   never merge to main without my approval.

HOW TO WORK:
- Follow the waves in planning/README.md, in order. Finish and review a wave before
  starting the next.
- Skip cards marked OWNER, and BLOCKED cards whose question has no recorded answer. Keep
  a list for me.
- Before each wave, check that every card's files and functions still exist. Correct the
  card in planning/ if the code has moved, or ask me if the design no longer fits.

SPAWNING SUBAGENTS:
- Use the Agent tool with model "sonnet", subagent_type "general-purpose" and isolation
  "worktree".
- Give one workstream group per agent, in the order shown:
    a. Backend access and users: A cards
    b. Selling: O cards
    c. Stock and production: P cards
    d. Money: M cards
    e. Expenses: E cards
    f. Taxes: T cards
    g. Notifications: N cards
    h. Assistant: H cards
    i. Translation: L cards
    j. Go-live tools: G cards
- Run agents in parallel only if their cards are in the same wave and share no files. These
  always conflict, so give them to one agent at a time: core/models.py, migrations of the same
  app, seed_defaults.py, config/settings.py, config/urls.py, frontend AppRoutes.tsx,
  frontend i18n files, backend/docs/API.md, and checklist.md.
- At most 3 agents at once.

Each subagent's prompt must contain this text, with the blanks filled in:

  You are implementing cards <IDS> of the Nidhi Masala ERP.
  Read planning/00-implementer-guide.md completely, then planning/<DOC>.md.
  Follow the guide literally. Do one card at a time, with one commit per card.
  Never run migrate, seed commands or data changes against a real database. You are in a
  worktree, with no .env, so Django uses SQLite. Keep it that way.
  Use C:\Users\91700\Desktop\ERP\backend\venv\Scripts\python.exe, and do not create a new
  virtual environment. For frontend commands, install dependencies in the worktree if needed.
  If a stop condition in the guide applies, stop at once and report. Do not improvise.
  Finish with: cards done with commit hashes, tests run with exact results, files changed,
  anything skipped, and anything you were unsure about.

REVIEWING. You do this yourself, after every agent finishes:
1. Read the whole diff. Do not rely on the agent's report.
2. Check it against the card, and the guide's absolute rules.
3. Run the affected tests yourself.
4. Look closely at anything touching GLEntry, StockLedgerEntry, Bin, tax, payments, stock
   value, or `transaction.atomic`. Accounting and tax design stays with you, not subagents.
5. Check for hardcoded text, float money, missing constraints, weakened tests, and `API.md`.
6. If it is wrong, resume that agent with SendMessage and exact fixes. After two failed
   attempts, fix it yourself or put it on the list for me.
7. Merge into plan/phase-10. If two branches conflict on migrations, regenerate the later
   one; do not hand-edit an applied one.

AT THE END OF EACH WAVE, on plan/phase-10:
- `python -m pytest -q`, `makemigrations --check --dry-run`, ruff, black, mypy, and
  `npm run build`.
- Run `verify_integrity` on a SQLite database that went through a full order, batch, dispatch,
  receipt, expense and tax payment. Never run it, or anything else, on the real database.
- Compare with the baseline. No new failures are allowed.
- Update planning/ and checklist.md to match what was really built.
- Give me a short report: what works, what was skipped, and what you need from me.

Continue with the next wave unless a question blocks the work. If a blocked item only affects
some cards, continue with the rest.

NEVER:
- Touch the real database, read secrets, or send real email.
- Push, deploy, or merge to main without my approval.
- Add lot tracking, delivery notes, tracking after dispatch, or tax scope beyond employee TDS,
  GST input credit and GST payable.
- Decide a legal or tax rule. Record it for the accountant.
- Mark anything complete without passing tests.

FINISH with: what is complete, what is left and why, every OWNER card with exact steps for me,
all open questions, and how to review plan/phase-10.
```
