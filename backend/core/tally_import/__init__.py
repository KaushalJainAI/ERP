"""One-off TallyPrime -> ERP importer (NIDHI GRAH UDYOG, 10 financial years).

Modules
-------
``source``     read-only SQL access to the Tally Postgres database.
``masters``    chart of accounts, parties and the pure mapping helpers.
``stock``      UOM / item / warehouse masters and the FY 2026-27 stock ledger.
``vouchers``   opening adjustment, voucher -> JournalEntry, closing stock, PCV.
``reconcile``  ERP-vs-Tally reconciliation and the markdown report.

Every ledger-affecting document is created as a draft and pushed through
``.submit()`` so ``GLEntry`` / ``StockLedgerEntry`` / ``Bin`` are only ever
written by the posting services (CLAUDE.md invariants).
"""
