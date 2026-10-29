"""RBAC seed data for the Tally export (imported by ``seed_defaults``).

Accounts Manager (and System Manager) only: the export hands the accountant's
books to Tally and the maps decide which Tally ledger every rupee lands in.
The batch / voucher log has no update or delete route at all.
"""

TALLY_EXPORT_LABELS = (
    "tally_export.TallyExportSettings",
    "tally_export.TallyLedgerMap",
    "tally_export.TallyStockItemMap",
    "tally_export.TallyExportBatch",
    "tally_export.TallyExportedVoucher",
)
