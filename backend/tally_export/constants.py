"""Static mapping tables for the Tally export."""

#: ERP document -> Tally voucher type. A key is ``"app.Model"`` or
#: ``"app.Model:variant"`` (the variant wins). Per-company overrides live in
#: ``TallyExportSettings.voucher_type_overrides``.
DEFAULT_VOUCHER_TYPES = {
    "selling.SalesInvoice": "Sales",
    "selling.SalesInvoice:return": "Credit Note",
    "buying.PurchaseInvoice": "Purchase",
    "buying.PurchaseInvoice:return": "Debit Note",
    "accounts.PaymentEntry:Receive": "Receipt",
    "accounts.PaymentEntry:Pay": "Payment",
    "accounts.PaymentEntry:Internal Transfer": "Contra",
    "accounts.JournalEntry": "Journal",
    "accounts.JournalEntry:Contra Entry": "Contra",
    "accounts.JournalEntry:Credit Note": "Credit Note",
    "accounts.JournalEntry:Debit Note": "Debit Note",
    "stock.StockEntry": "Stock Journal",
}
FALLBACK_VOUCHER_TYPE = "Journal"

#: Which default voucher keys a Tally base type feeds when seeding from the
#: legacy archive (the company's own, possibly custom, voucher type name).
LEGACY_BASE_TO_KEYS = {
    "Sales": ["selling.SalesInvoice"],
    "Purchase": ["buying.PurchaseInvoice"],
    "Credit Note": ["selling.SalesInvoice:return", "accounts.JournalEntry:Credit Note"],
    "Debit Note": ["buying.PurchaseInvoice:return", "accounts.JournalEntry:Debit Note"],
    "Receipt": ["accounts.PaymentEntry:Receive"],
    "Payment": ["accounts.PaymentEntry:Pay"],
    "Contra": [
        "accounts.PaymentEntry:Internal Transfer",
        "accounts.JournalEntry:Contra Entry",
    ],
    "Journal": ["accounts.JournalEntry"],
    "Stock Journal": ["stock.StockEntry"],
}

#: Suggested Tally ledger name of a GST-tagged ERP account, by (direction, component).
#: Tally's old combined ledgers (``C Gst`` ...) are NOT used for new postings; the
#: separate Input / Output ledgers are what GSTR-1 / GSTR-3B need.
GST_LEDGER_NAMES = {
    (direction, component): f"{prefix} {component}"
    for direction, prefix in (
        ("Input", "Input"),
        ("Output", "Output"),
        ("Reverse Charge", "RCM Payable"),
    )
    for component in ("CGST", "SGST", "IGST", "CESS")
}

#: ERP documents that are never sent to Tally: Tally closes its own years, so the
#: year-end Period Closing Voucher would double-close the books.
NEVER_EXPORTED_DOCTYPES = ["accounts.PeriodClosingVoucher"]

#: Voucher names with these prefixes were imported from Tally; they are history.
DEFAULT_EXCLUDED_PREFIXES = ["TLY-"]

#: Godown used when a stock line has no warehouse (Tally's built-in godown).
DEFAULT_GODOWN = "Main Location"

#: Tally's reserved parent groups.
G_DEBTORS = "Sundry Debtors"
G_CREDITORS = "Sundry Creditors"
G_SALES = "Sales Accounts"
G_PURCHASE = "Purchase Accounts"
G_TAXES = "Duties & Taxes"
G_BANK = "Bank Accounts"
G_CASH = "Cash-in-Hand"
G_INDIRECT_EXP = "Indirect Expenses"
G_DIRECT_EXP = "Direct Expenses"
G_INDIRECT_INC = "Indirect Incomes"
G_CURRENT_ASSETS = "Current Assets"
G_CURRENT_LIABS = "Current Liabilities"
G_CAPITAL = "Capital Account"
G_STOCK = "Stock-in-Hand"
