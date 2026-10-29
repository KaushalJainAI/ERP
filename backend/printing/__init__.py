"""Print data for the printable document formats (Tax Invoice, Credit / Debit Note,
Delivery Challan, Purchase Order, Payment Receipt / Voucher).

No models and no writes: every endpoint is a read-only projection of an existing
document (``printing.builders``) that returns exactly what the print template needs,
already computed server-side. Labels are translated by the frontend (react-i18next);
only the amount-in-words text is generated here (``printing.words``).
"""
