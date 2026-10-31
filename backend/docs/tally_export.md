# Tally export: ERP to Tally Prime

The ERP is where everything is entered. The official books and the GST return stay
in Tally Prime. This feature hands the ERP's entries to Tally as import files, so
the accountant only imports and files.

Where: **Accounts > Tally Export** (needs the *Accounts Manager* role).

## How to export (accountant)

1. **Pick the dates** (or leave them empty for everything pending). The page shows
   the **pre-flight check**: every voucher that will be sent, its Tally voucher type,
   its debit and credit totals (they must be equal), and a *Pending* pill: vouchers
   never exported plus vouchers cancelled since they were exported.
2. **Map anything Tally does not know yet.** Unmapped ledgers, customers / suppliers
   and stock items are listed with a suggested Tally name. Edit the name if it is
   spelled differently in Tally and press *Save*. Export stays blocked until
   everything is mapped, unless you tick **export new masters**: then the suggested
   names are used as they are and a second file with the missing ledgers and stock
   items is created for Tally.
3. Press **Export to Tally**. The browser downloads one XML file (or a zip with
   `1-masters.xml` and `2-vouchers.xml` when new masters were created).
4. In **Tally Prime**: open the company named in *Settings* (the name must match),
   then **Import > Masters** for `1-masters.xml` (only if there is one), then
   **Import > Vouchers** for `2-vouchers.xml` / the vouchers file. Tally reports how
   many vouchers were created; anything it rejects is listed in its import log.
5. File GST in Tally as usual.

Every export is logged (who, when, date range, SHA-256 of the file, vouchers) and can be
downloaded again byte for byte. *Re-export* builds a new batch with the same
vouchers as they stand now. Because each voucher carries a stable id (see below),
importing it again **alters** the voucher in Tally instead of duplicating it.

A voucher cancelled in the ERP after it was exported is sent as a **Cancel** for the
same voucher number and id, so Tally cancels it too.

## What is exported, and how

Vouchers are built from the immutable ledgers, never from the document screens, so
every posting document is exportable, including types added later.

| ERP document | Tally voucher type |
|---|---|
| Sales Invoice | Sales (a sales return: Credit Note) |
| Purchase Invoice | Purchase (a purchase return: Debit Note) |
| Payment Entry, Receive / Pay / Internal Transfer | Receipt / Payment / Contra |
| Journal Entry | Journal (entry type Contra: Contra, Credit Note: Credit Note, Debit Note: Debit Note) |
| Stock Entry (receipt, issue, transfer, manufacture, repack) | Stock Journal (quantities from the stock ledger) |
| anything else that posts to the GL (for example Stock Reconciliation) | Journal |

*Settings > Voucher type names in Tally* changes the name per company, for example
`Sales Gst` where the company has its own Sales voucher type. *Take mappings from
Tally history* fills these in from the imported Tally years.

Never exported: documents named `TLY-...` (the ten years already imported from
Tally; the prefixes are a setting) Opening Entry journals, Opening Stock reconciliations and opening bills (planning 09, the balances Tally already holds). A receipt that settles an opening bill is exported On Account, since Tally has no bill of that name.

Each voucher has: `VCHTYPE`, `ACTION="Create"`, `DATE` (YYYYMMDD), `VOUCHERNUMBER`
(the ERP document number), `REFERENCE` (bill number / cheque / payment reference
where there is one), `NARRATION`, `PARTYLEDGERNAME`, party GSTIN and place of supply
for invoices, and `REMOTEID` / `GUID` = a UUID derived from company + document type +
number (same document, same id, always).

Signs follow Tally: a debit is `ISDEEMEDPOSITIVE Yes` with a negative `AMOUNT`, a credit
is `No` with a positive amount. A voucher's ledger entries plus the accounting
allocations of its stock lines add up to zero.

Invoices carry `ALLINVENTORYENTRIES.LIST`: stock item, quantity and unit, rate, amount,
godown (the warehouse name) and batch, with the sales / purchase ledger as the
accounting allocation. GST (CGST / SGST / IGST) and round-off go as separate ledger
entries. Party lines carry a bill allocation (`New Ref` for the invoice itself,
`Agst Ref` for a payment or note against an invoice, `On Account` otherwise).

Stock valuation is left to Tally: the ERP's *Stock In Hand* / *Cost of Goods Sold* legs
are not sent. What they net to on a purchase (the stock cost) is part of the
purchase ledger amount. Non-stock lines (freight, services) of an invoice stay on the
sales / purchase ledger as a separate ledger entry.

## Mapping

* **Ledger map** (Accounts > Tally Ledgers): ERP account, optionally one customer /
  supplier, to a Tally ledger name. Debtors / Creditors lines go to the *party's*
  ledger, never to the control account.
* **Stock item map** (Accounts > Tally Stock Items): ERP item to Tally stock item name and unit.
* Both can be edited by the Accounts Manager; every change is audited.
* New ledgers get their Tally parent group from the account (an imported Tally group
  if there is one, otherwise by type: Bank Accounts, Cash-in-Hand, Duties & Taxes,
  Sales Accounts, Purchase Accounts, Indirect Expenses ...); customers go under
  Sundry Debtors, suppliers under Sundry Creditors, with GSTIN, state, address and PIN.
  New stock items carry unit, HSN and GST rate where the ERP has them.

## Things to know

* The Tally company name (*Settings*) must be exactly the open company, or Tally
  refuses the import.
* Godowns are the ERP warehouse names. The ERP's default warehouse is called
  `Main Location`, Tally's built-in godown; any other warehouse must exist as a godown in
  Tally first.
* Not confirmed against a live Tally Prime here (see the review notes in the task
  report): `ACTION="Cancel"` handling of a voucher id, the Stock Journal
  `INVENTORYENTRIESIN/OUT.LIST` layout, and that Tally derives GST return values from
  the stock item's GST details when only the tax ledger amounts are sent. Import a
  one-day file into a test company first.
* Self-healing: `python manage.py verify_integrity` checks that every stored export
  file still matches its SHA-256 and that the batch counts match the logged vouchers.
