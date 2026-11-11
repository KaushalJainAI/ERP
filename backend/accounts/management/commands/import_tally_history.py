"""Import historical Tally vouchers into the ERP as historical documents.

Sales Invoices (with items), Purchase Invoices (with items/expenses),
Credit/Debit Notes, and Payment Entries (Receipts/Payments) are created
as submitted historical rows (is_historical=True, status=Historical).
They post nothing to the General Ledger or Stock Ledger, preserving
all existing TLY-* Journal Entries and Trial Balance invariants to the paisa.
"""

from __future__ import annotations

import gzip
import json
import os
import sys
from collections import defaultdict
from decimal import Decimal
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Sum

from accounts.models import (
    Account,
    AccountType,
    InvoiceStatus,
    JournalEntry,
    PaymentEntry,
    PaymentEntryReference,
)
from buying.models import PurchaseInvoice, PurchaseInvoiceItem, Supplier, SupplierGroup
from core.models import Company, DocStatus
from selling.models import Customer, CustomerGroup, SalesInvoice, SalesInvoiceItem
from stock.models import Item, ItemGroup, UOM

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
EXPENSE_PLACEHOLDER_CODE = "TALLY-EXPENSE"


def norm(s: str | None) -> str:
    """Normalize whitespace and lowercase for reliable string comparison."""
    if not s:
        return ""
    return " ".join(s.strip().split()).lower()


class Command(BaseCommand):
    help = "Import historical Tally vouchers from tally_history.jsonl.gz."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            default="tally_history.jsonl.gz",
            help="Path to tally_history.jsonl.gz file",
        )
        parser.add_argument(
            "--company",
            default="NIDHI GRAH UDYOG",
            help="Company name (default: NIDHI GRAH UDYOG)",
        )
        parser.add_argument(
            "--year",
            help="Filter import to a specific financial year (e.g. 2024-25)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Simulate import and output reconciliation report without saving changes",
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            default=False,
            help="Persist changes to the database",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Batch size for bulk insertion (default: 500)",
        )

    def handle(self, *args, **options):
        source_path = options["source"]
        company_name = options["company"]
        fy_filter = options["year"]
        commit = options["commit"]
        dry_run = options["dry_run"] or not commit
        batch_size = options["batch_size"]

        if not os.path.exists(source_path):
            raise CommandError(f"Source file not found: {source_path}")

        try:
            company = Company.objects.get(name=company_name)
        except Company.DoesNotExist:
            raise CommandError(f"Company '{company_name}' not found.")

        mode_str = "DRY-RUN (no changes will be saved)" if dry_run else "COMMIT (persisting to database)"
        self.stdout.write(self.style.NOTICE(f"\n--- Tally History Import ---"))
        self.stdout.write(f"Source:  {source_path}")
        self.stdout.write(f"Company: {company.name} (ID: {company.id})")
        if fy_filter:
            self.stdout.write(f"Year:    {fy_filter}")
        self.stdout.write(f"Mode:    {mode_str}\n")

        # Invariant baselines
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM accounts_glentry")
            gl_before = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM stock_stockledgerentry")
            sle_before = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM stock_bin")
            bin_before = cur.fetchone()[0]
            cur.execute("SELECT sum(debit), sum(credit) FROM accounts_glentry WHERE NOT is_cancelled")
            tb_row = cur.fetchone()
            tb_dr_before = tb_row[0] or Decimal("0.00")
            tb_cr_before = tb_row[1] or Decimal("0.00")

        # Ensure prerequisite placeholder item & customer exist
        tally_item = self._ensure_placeholder_item()
        cash_customer = self._ensure_cash_customer(company)
        default_cust_group = CustomerGroup.objects.filter(name__icontains="Sundry Debtors").first() or CustomerGroup.objects.first()
        default_supp_group = SupplierGroup.objects.filter(name__icontains="Sundry Creditors").first() or SupplierGroup.objects.first()

        # Cache existing records for fast lookups
        self.stdout.write("Caching master data and existing document keys...")
        items_by_name: dict[str, Item] = {norm(it.name): it for it in Item.objects.all()}
        items_by_code: dict[str, Item] = {norm(it.code): it for it in Item.objects.all()}

        customers_by_name: dict[str, Customer] = {
            norm(c.name): c for c in Customer.objects.filter(company=company)
        }
        suppliers_by_name: dict[str, Supplier] = {
            norm(s.name): s for s in Supplier.objects.filter(company=company)
        }
        accounts_by_name: dict[str, Account] = {
            norm(a.name): a for a in Account.objects.filter(company=company)
        }

        # Existing historical document names (to guarantee idempotency)
        existing_si = set(
            SalesInvoice.objects.filter(company=company).values_list("name", flat=True)
        )
        existing_pi = set(
            PurchaseInvoice.objects.filter(company=company).values_list("name", flat=True)
        )
        existing_pe = set(
            PaymentEntry.objects.filter(company=company).values_list("name", flat=True)
        )

        # Existing supplier bills for uniqueness constraint
        seen_supplier_bills = {
            (b.lower(), s_id)
            for b, s_id in PurchaseInvoice.objects.filter(company=company)
            .exclude(docstatus=DocStatus.CANCELLED)
            .exclude(bill_no="")
            .values_list("bill_no", "supplier_id")
        }

        # Journal map for resolving source_journal FK and journal accounts
        self.stdout.write("Caching Journal Entries...")
        journals_by_name: dict[str, JournalEntry] = {
            j.name: j
            for j in JournalEntry.objects.filter(
                company=company, name__startswith="TLY-"
            ).only("id", "name", "posting_date")
        }

        # Cache journal lines for resolving accounts
        self.stdout.write("Caching Journal lines for account resolution...")
        with connection.cursor() as cur:
            cur.execute("""
                SELECT j.name, l.account_id, l.debit, l.credit
                FROM accounts_journalentryline l
                JOIN accounts_journalentry j ON l.journal_entry_id = j.id
                WHERE j.company_id = %s AND j.name LIKE 'TLY-%%'
            """, [company.id])
            journal_lines_by_name: dict[str, list[tuple[int, Decimal, Decimal]]] = defaultdict(list)
            for jname, acc_id, dr, cr in cur.fetchall():
                journal_lines_by_name[jname].append((acc_id, Decimal(str(dr)), Decimal(str(cr))))

        default_receivable_id = (
            company.default_receivable_account_id
            or accounts_by_name.get("sundry debtors (ngu)", accounts_by_name.get("sundry debtors")).id
        )
        default_payable_id = (
            company.default_payable_account_id
            or accounts_by_name.get("sundry creditors (ngu)", accounts_by_name.get("sundry creditors")).id
        )
        default_income_id = (
            company.default_income_account_id
            or accounts_by_name.get("sales gst @ 5% (ngu)", accounts_by_name.get("gst sales @5%")).id
        )
        default_expense_id = (
            company.default_expense_account_id
            or accounts_by_name.get("purchase accounts (ngu)", accounts_by_name.get("purchase")).id
        )

        # Statistics trackers
        stats_created: dict[str, int] = defaultdict(int)
        stats_skipped: dict[str, int] = defaultdict(int)
        stats_already: dict[str, int] = defaultdict(int)
        skip_reasons: dict[str, int] = defaultdict(int)
        stats_by_fy: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

        # Queues for batch creation
        si_to_create: list[SalesInvoice] = []
        si_items_to_create: list[tuple[str, list[SalesInvoiceItem]]] = []

        pi_to_create: list[PurchaseInvoice] = []
        pi_items_to_create: list[tuple[str, list[PurchaseInvoiceItem]]] = []

        pe_to_create: list[PaymentEntry] = []

        # Maps for oldest-first allocations per party
        # party_name -> list of (doc_name, posting_date, amount, remaining)
        customer_invoices: dict[str, list[dict[str, Any]]] = defaultdict(list)
        customer_receipts: dict[str, list[dict[str, Any]]] = defaultdict(list)
        supplier_invoices: dict[str, list[dict[str, Any]]] = defaultdict(list)
        supplier_payments: dict[str, list[dict[str, Any]]] = defaultdict(list)

        def flush_batches():
            nonlocal si_to_create, si_items_to_create, pi_to_create, pi_items_to_create, pe_to_create

            if si_to_create:
                SalesInvoice.objects.bulk_create(si_to_create, batch_size=batch_size)
                # Resolve IDs for items
                created_si_map = {
                    inv.name: inv.id
                    for inv in SalesInvoice.objects.filter(
                        name__in=[inv.name for inv in si_to_create]
                    ).only("id", "name")
                }
                flat_items: list[SalesInvoiceItem] = []
                for inv_name, item_list in si_items_to_create:
                    inv_id = created_si_map.get(inv_name)
                    if inv_id:
                        for it in item_list:
                            it.sales_invoice_id = inv_id
                            flat_items.append(it)
                if flat_items:
                    SalesInvoiceItem.objects.bulk_create(flat_items, batch_size=batch_size)
                si_to_create = []
                si_items_to_create = []

            if pi_to_create:
                PurchaseInvoice.objects.bulk_create(pi_to_create, batch_size=batch_size)
                created_pi_map = {
                    inv.name: inv.id
                    for inv in PurchaseInvoice.objects.filter(
                        name__in=[inv.name for inv in pi_to_create]
                    ).only("id", "name")
                }
                flat_pi_items: list[PurchaseInvoiceItem] = []
                for inv_name, item_list in pi_items_to_create:
                    inv_id = created_pi_map.get(inv_name)
                    if inv_id:
                        for it in item_list:
                            it.purchase_invoice_id = inv_id
                            flat_pi_items.append(it)
                if flat_pi_items:
                    PurchaseInvoiceItem.objects.bulk_create(flat_pi_items, batch_size=batch_size)
                pi_to_create = []
                pi_items_to_create = []

            if pe_to_create:
                PaymentEntry.objects.bulk_create(pe_to_create, batch_size=batch_size)
                pe_to_create = []

        # Read JSONL file and process
        self.stdout.write("Reading vouchers and processing...")
        total_read = 0

        # We enclose the whole run in an atomic block so dry-run rolls back cleanly
        with transaction.atomic():
            with gzip.open(source_path, "rt", encoding="utf-8") as gz_in:
                for line in gz_in:
                    total_read += 1
                    if total_read % 10000 == 0:
                        self.stdout.write(f"  Processed {total_read} vouchers...")

                    rec = json.loads(line)
                    fy = rec["fy"]
                    if fy_filter and fy != fy_filter:
                        continue

                    vchtype = rec["vchtype"]
                    target_doctype = rec["target_doctype"]
                    journal_name = rec["journal_name"]

                    if not target_doctype or not journal_name:
                        # Stays as Journal only
                        stats_skipped[vchtype] += 1
                        skip_reasons["No target doctype (stays Journal)"] += 1
                        stats_by_fy[fy]["skipped"] += 1
                        continue

                    journal = journals_by_name.get(journal_name)
                    if not journal:
                        stats_skipped[vchtype] += 1
                        skip_reasons["Source journal not found"] += 1
                        stats_by_fy[fy]["skipped"] += 1
                        continue

                    raw_party = rec["party"]
                    party_key = norm(raw_party)

                    # --- 1. SALES INVOICE ---
                    if target_doctype == "SalesInvoice":
                        if journal_name in existing_si:
                            stats_already[vchtype] += 1
                            stats_by_fy[fy]["already"] += 1
                            continue

                        # Resolve Customer
                        if party_key == "cash" or not party_key:
                            customer = cash_customer
                        else:
                            customer = customers_by_name.get(party_key)
                            if not customer:
                                # Dual counterparty: check if exists in suppliers
                                supp = suppliers_by_name.get(party_key)
                                customer = Customer.objects.create(
                                    name=raw_party,
                                    company=company,
                                    customer_group=default_cust_group,
                                    gstin=getattr(supp, "gstin", "") or "",
                                    gst_category=getattr(supp, "gst_category", "Unregistered"),
                                )
                                customers_by_name[party_key] = customer

                        # Resolve accounts from journal lines
                        j_lines = journal_lines_by_name.get(journal_name, [])
                        debit_to_id = default_receivable_id
                        income_acc_id = default_income_id
                        for acc_id, dr, cr in j_lines:
                            if dr > ZERO and debit_to_id == default_receivable_id:
                                debit_to_id = acc_id
                            elif cr > ZERO and income_acc_id == default_income_id:
                                income_acc_id = acc_id

                        # Prepare items
                        inv_items: list[SalesInvoiceItem] = []
                        net_total = ZERO
                        for itm in rec["inventory_entries"]:
                            s_name = itm["stockitemname"]
                            item_obj = items_by_name.get(norm(s_name)) or items_by_code.get(norm(s_name))
                            if not item_obj:
                                item_obj = tally_item

                            qty = Decimal(itm["actualqty"] or itm["billedqty"] or "1.000")
                            rate = Decimal(itm["rate"] or "0.00")
                            amt = Decimal(itm["amount"] or "0.00")
                            if amt == ZERO and qty > ZERO and rate > ZERO:
                                amt = (qty * rate).quantize(CENT)
                            net_total += amt

                            inv_items.append(
                                SalesInvoiceItem(
                                    item=item_obj,
                                    qty=qty,
                                    rate=rate,
                                    amount=amt,
                                    is_historical=True,
                                )
                            )

                        if not inv_items:
                            # Non-stock / placeholder line
                            header_amt = abs(Decimal(rec["header_amount"]))
                            inv_items.append(
                                SalesInvoiceItem(
                                    item=tally_item,
                                    qty=Decimal("1.000"),
                                    rate=header_amt,
                                    amount=header_amt,
                                    is_historical=True,
                                )
                            )
                            net_total = header_amt

                        # Calculate GST and ties
                        is_ret = rec["is_return"]
                        sign = Decimal("-1") if is_ret else Decimal("1")

                        # Adjust line item signs if return
                        if is_ret:
                            for itm_obj in inv_items:
                                itm_obj.qty = -abs(itm_obj.qty)
                                itm_obj.amount = -abs(itm_obj.amount)

                        gst_total = ZERO
                        for le in rec["ledger_entries"]:
                            lname = le["ledgername"].lower()
                            if any(g in lname for g in ["gst", "cgst", "sgst", "igst", "cess"]):
                                lamt = abs(Decimal(le["amount"]))
                                gst_total += lamt

                        net_total = (abs(net_total) * sign).quantize(CENT)
                        gst_total = (abs(gst_total) * sign).quantize(CENT)
                        grand_total = net_total + gst_total

                        party_amt_raw = Decimal(rec["party_amount"]) if rec["party_amount"] else abs(Decimal(rec["header_amount"]))
                        party_amt = (abs(party_amt_raw) * sign).quantize(CENT)
                        rounding = (party_amt - grand_total).quantize(CENT)
                        rounded_total = party_amt

                        si = SalesInvoice(
                            name=journal_name,
                            company=company,
                            customer=customer,
                            posting_date=rec["date"],
                            due_date=rec["date"],
                            docstatus=DocStatus.SUBMITTED,
                            is_historical=True,
                            source_journal=journal,
                            is_return=rec["is_return"],
                            net_total=net_total,
                            total_taxes_and_charges=gst_total,
                            rounding_adjustment=rounding,
                            grand_total=grand_total,
                            rounded_total=rounded_total,
                            outstanding_amount=ZERO,
                            status=InvoiceStatus.HISTORICAL,
                            update_stock=False,
                            debit_to_id=debit_to_id,
                            income_account_id=income_acc_id,
                        )

                        si_to_create.append(si)
                        si_items_to_create.append((journal_name, inv_items))
                        existing_si.add(journal_name)
                        stats_created[vchtype] += 1
                        stats_by_fy[fy]["created"] += 1

                        if not rec["is_return"]:
                            customer_invoices[customer.name].append({
                                "name": journal_name,
                                "date": rec["date"],
                                "amount": party_amt,
                                "remaining": party_amt,
                            })

                    # --- 2. PURCHASE INVOICE ---
                    elif target_doctype == "PurchaseInvoice":
                        if journal_name in existing_pi:
                            stats_already[vchtype] += 1
                            stats_by_fy[fy]["already"] += 1
                            continue

                        # Resolve Supplier
                        supplier = suppliers_by_name.get(party_key)
                        if not supplier:
                            cust = customers_by_name.get(party_key)
                            supplier = Supplier.objects.create(
                                name=raw_party or "General Supplier",
                                company=company,
                                supplier_group=default_supp_group,
                                gstin=getattr(cust, "gstin", "") or "",
                                gst_category=getattr(cust, "gst_category", "Unregistered"),
                            )
                            suppliers_by_name[party_key] = supplier

                        # Resolve accounts
                        j_lines = journal_lines_by_name.get(journal_name, [])
                        credit_to_id = default_payable_id
                        expense_acc_id = default_expense_id
                        for acc_id, dr, cr in j_lines:
                            if cr > ZERO and credit_to_id == default_payable_id:
                                credit_to_id = acc_id
                            elif dr > ZERO and expense_acc_id == default_expense_id:
                                expense_acc_id = acc_id

                        # Prepare items
                        pi_items: list[PurchaseInvoiceItem] = []
                        net_total = ZERO
                        for itm in rec["inventory_entries"]:
                            s_name = itm["stockitemname"]
                            item_obj = items_by_name.get(norm(s_name)) or items_by_code.get(norm(s_name))
                            if not item_obj:
                                item_obj = tally_item

                            qty = Decimal(itm["actualqty"] or itm["billedqty"] or "1.000")
                            rate = Decimal(itm["rate"] or "0.00")
                            amt = Decimal(itm["amount"] or "0.00")
                            if amt == ZERO and qty > ZERO and rate > ZERO:
                                amt = (qty * rate).quantize(CENT)
                            net_total += amt

                            pi_items.append(
                                PurchaseInvoiceItem(
                                    item=item_obj,
                                    qty=qty,
                                    rate=rate,
                                    amount=amt,
                                    is_historical=True,
                                )
                            )

                        if not pi_items:
                            # Expense bill without inventory entries (721 vouchers)
                            header_amt = abs(Decimal(rec["header_amount"]))
                            pi_items.append(
                                PurchaseInvoiceItem(
                                    item=tally_item,
                                    qty=Decimal("1.000"),
                                    rate=header_amt,
                                    amount=header_amt,
                                    is_historical=True,
                                )
                            )
                            net_total = header_amt

                        is_ret = rec["is_return"]
                        sign = Decimal("-1") if is_ret else Decimal("1")

                        # Adjust line item signs if return
                        if is_ret:
                            for itm_obj in pi_items:
                                itm_obj.qty = -abs(itm_obj.qty)
                                itm_obj.amount = -abs(itm_obj.amount)

                        gst_total = ZERO
                        for le in rec["ledger_entries"]:
                            lname = le["ledgername"].lower()
                            if any(g in lname for g in ["gst", "cgst", "sgst", "igst", "cess"]):
                                lamt = abs(Decimal(le["amount"]))
                                gst_total += lamt

                        net_total = (abs(net_total) * sign).quantize(CENT)
                        gst_total = (abs(gst_total) * sign).quantize(CENT)
                        grand_total = net_total + gst_total

                        party_amt_raw = Decimal(rec["party_amount"]) if rec["party_amount"] else abs(Decimal(rec["header_amount"]))
                        party_amt = (abs(party_amt_raw) * sign).quantize(CENT)
                        rounding = (party_amt - grand_total).quantize(CENT)
                        rounded_total = party_amt

                        raw_bill_num = rec["reference"] or rec["vouchernumber"] or journal_name
                        bill_num = raw_bill_num[:50]
                        supp_key = (bill_num.lower(), supplier.id)
                        if supp_key in seen_supplier_bills:
                            bill_num = f"{raw_bill_num[:38]} ({fy})"
                            supp_key = (bill_num.lower(), supplier.id)
                            if supp_key in seen_supplier_bills:
                                bill_num = f"{raw_bill_num[:28]} ({journal_name})"
                                supp_key = (bill_num.lower(), supplier.id)
                        seen_supplier_bills.add(supp_key)

                        pi = PurchaseInvoice(
                            name=journal_name,
                            company=company,
                            supplier=supplier,
                            posting_date=rec["date"],
                            due_date=rec["date"],
                            bill_no=bill_num[:50],
                            bill_date=rec["date"],
                            docstatus=DocStatus.SUBMITTED,
                            is_historical=True,
                            source_journal=journal,
                            is_return=rec["is_return"],
                            net_total=net_total,
                            total_taxes_and_charges=gst_total,
                            rounding_adjustment=rounding,
                            grand_total=grand_total,
                            rounded_total=rounded_total,
                            outstanding_amount=ZERO,
                            status=InvoiceStatus.HISTORICAL,
                            update_stock=False,
                            credit_to_id=credit_to_id,
                            expense_account_id=expense_acc_id,
                        )

                        pi_to_create.append(pi)
                        pi_items_to_create.append((journal_name, pi_items))
                        existing_pi.add(journal_name)
                        stats_created[vchtype] += 1
                        stats_by_fy[fy]["created"] += 1

                        if not rec["is_return"]:
                            supplier_invoices[supplier.name].append({
                                "name": journal_name,
                                "date": rec["date"],
                                "amount": party_amt,
                                "remaining": party_amt,
                            })

                    # --- 3. PAYMENT ENTRY ---
                    elif target_doctype == "PaymentEntry":
                        if journal_name in existing_pe:
                            stats_already[vchtype] += 1
                            stats_by_fy[fy]["already"] += 1
                            continue

                        j_lines = journal_lines_by_name.get(journal_name, [])
                        if not j_lines:
                            stats_skipped[vchtype] += 1
                            skip_reasons["No journal lines for payment"] += 1
                            stats_by_fy[fy]["skipped"] += 1
                            continue

                        amt = abs(Decimal(rec["header_amount"]))
                        if vchtype == "Receipt":
                            payment_type = "Receive"
                            party_type = "Customer"
                            # Debit = paid_to (bank/cash), Credit = paid_from (customer/debtors)
                            dr_lines = [l for l in j_lines if l[1] > ZERO]
                            cr_lines = [l for l in j_lines if l[2] > ZERO]
                            paid_to_id = max(dr_lines, key=lambda x: x[1])[0] if dr_lines else default_receivable_id
                            paid_from_id = max(cr_lines, key=lambda x: x[2])[0] if cr_lines else default_receivable_id
                        else:
                            payment_type = "Pay"
                            party_type = "Supplier"
                            # Credit = paid_from (bank/cash), Debit = paid_to (supplier/expense)
                            dr_lines = [l for l in j_lines if l[1] > ZERO]
                            cr_lines = [l for l in j_lines if l[2] > ZERO]
                            paid_to_id = max(dr_lines, key=lambda x: x[1])[0] if dr_lines else default_payable_id
                            paid_from_id = max(cr_lines, key=lambda x: x[2])[0] if cr_lines else default_payable_id

                        pe = PaymentEntry(
                            name=journal_name,
                            company=company,
                            payment_type=payment_type,
                            party_type=party_type,
                            party_name=raw_party or ("Cash Customer" if payment_type == "Receive" else "General Supplier"),
                            paid_from_id=paid_from_id,
                            paid_to_id=paid_to_id,
                            paid_amount=amt,
                            received_amount=amt,
                            posting_date=rec["date"],
                            reference_no=rec["vouchernumber"] or "",
                            docstatus=DocStatus.SUBMITTED,
                            is_historical=True,
                            source_journal=journal,
                        )

                        pe_to_create.append(pe)
                        existing_pe.add(journal_name)
                        stats_created[vchtype] += 1
                        stats_by_fy[fy]["created"] += 1

                        if payment_type == "Receive":
                            customer_receipts[pe.party_name].append({
                                "name": journal_name,
                                "date": rec["date"],
                                "amount": amt,
                                "remaining": amt,
                            })
                        else:
                            supplier_payments[pe.party_name].append({
                                "name": journal_name,
                                "date": rec["date"],
                                "amount": amt,
                                "remaining": amt,
                            })

                    # Periodic flush
                    if len(si_to_create) + len(pi_to_create) + len(pe_to_create) >= batch_size:
                        flush_batches()

            # Final flush of created documents
            flush_batches()

            # --- 4. OLDEST-FIRST PAYMENT REFERENCES (FOR DISPLAY ONLY) ---
            self.stdout.write("Linking historical receipts and payments oldest-first for display...")
            pe_refs_to_create: list[PaymentEntryReference] = []

            # Map PE name to ID
            all_pe_names = [r["name"] for rec_list in customer_receipts.values() for r in rec_list]
            all_pe_names.extend([p["name"] for pay_list in supplier_payments.values() for p in pay_list])
            pe_ids_by_name = {
                pe.name: pe.id
                for pe in PaymentEntry.objects.filter(name__in=all_pe_names).only("id", "name")
            }

            # Settle Customer receipts oldest-first
            for cust_name, receipts in customer_receipts.items():
                invoices = customer_invoices.get(cust_name, [])
                if not invoices:
                    continue

                receipts.sort(key=lambda x: (x["date"], x["name"]))
                invoices.sort(key=lambda x: (x["date"], x["name"]))

                inv_idx = 0
                for r in receipts:
                    pe_id = pe_ids_by_name.get(r["name"])
                    if not pe_id:
                        continue

                    while r["remaining"] > ZERO and inv_idx < len(invoices):
                        inv = invoices[inv_idx]
                        if inv["remaining"] <= ZERO:
                            inv_idx += 1
                            continue

                        alloc = min(r["remaining"], inv["remaining"])
                        if alloc > ZERO:
                            pe_refs_to_create.append(
                                PaymentEntryReference(
                                    payment_entry_id=pe_id,
                                    reference_doctype="Sales Invoice",
                                    reference_name=inv["name"],
                                    allocated_amount=alloc,
                                )
                            )
                            r["remaining"] -= alloc
                            inv["remaining"] -= alloc

                        if inv["remaining"] <= ZERO:
                            inv_idx += 1

            # Settle Supplier payments oldest-first
            for supp_name, payments in supplier_payments.items():
                invoices = supplier_invoices.get(supp_name, [])
                if not invoices:
                    continue

                payments.sort(key=lambda x: (x["date"], x["name"]))
                invoices.sort(key=lambda x: (x["date"], x["name"]))

                inv_idx = 0
                for p in payments:
                    pe_id = pe_ids_by_name.get(p["name"])
                    if not pe_id:
                        continue

                    while p["remaining"] > ZERO and inv_idx < len(invoices):
                        inv = invoices[inv_idx]
                        if inv["remaining"] <= ZERO:
                            inv_idx += 1
                            continue

                        alloc = min(p["remaining"], inv["remaining"])
                        if alloc > ZERO:
                            pe_refs_to_create.append(
                                PaymentEntryReference(
                                    payment_entry_id=pe_id,
                                    reference_doctype="Purchase Invoice",
                                    reference_name=inv["name"],
                                    allocated_amount=alloc,
                                )
                            )
                            p["remaining"] -= alloc
                            inv["remaining"] -= alloc

                        if inv["remaining"] <= ZERO:
                            inv_idx += 1

            if pe_refs_to_create:
                PaymentEntryReference.objects.bulk_create(pe_refs_to_create, batch_size=batch_size)
                self.stdout.write(f"Created {len(pe_refs_to_create)} PaymentEntryReference display links.")

            # --- 5. INVARIANT VERIFICATION & REPORT ---
            self.stdout.write("\nChecking ledger and stock invariants...")
            with connection.cursor() as cur:
                cur.execute("SELECT count(*) FROM accounts_glentry")
                gl_after = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM stock_stockledgerentry")
                sle_after = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM stock_bin")
                bin_after = cur.fetchone()[0]
                cur.execute("SELECT sum(debit), sum(credit) FROM accounts_glentry WHERE NOT is_cancelled")
                tb_row_after = cur.fetchone()
                tb_dr_after = tb_row_after[0] or Decimal("0.00")
                tb_cr_after = tb_row_after[1] or Decimal("0.00")

            gl_diff = gl_after - gl_before
            sle_diff = sle_after - sle_before
            bin_diff = bin_after - bin_before
            tb_dr_diff = tb_dr_after - tb_dr_before
            tb_cr_diff = tb_cr_after - tb_cr_before

            self.stdout.write(f"  GL Entry count change:    {gl_diff} (expected 0)")
            self.stdout.write(f"  StockLedgerEntry change:  {sle_diff} (expected 0)")
            self.stdout.write(f"  Bin count change:         {bin_diff} (expected 0)")
            self.stdout.write(f"  Trial Balance Dr change:  Rs.{tb_dr_diff:.2f} (expected Rs.0.00)")
            self.stdout.write(f"  Trial Balance Cr change:  Rs.{tb_cr_diff:.2f} (expected Rs.0.00)")

            if gl_diff != 0 or sle_diff != 0 or bin_diff != 0 or tb_dr_diff != ZERO or tb_cr_diff != ZERO:
                raise CommandError("Invariant check failed! Historical documents must NOT alter GL, Stock, or Trial Balance.")

            # Print Summary Report
            self.stdout.write(self.style.SUCCESS("\n========================================================"))
            self.stdout.write(self.style.SUCCESS(f"              TALLY IMPORT REPORT ({mode_str})"))
            self.stdout.write(self.style.SUCCESS("========================================================"))
            self.stdout.write(f"Total vouchers read: {total_read}\n")

            self.stdout.write("DOCUMENT CREATION BREAKDOWN:")
            for vt in sorted(stats_created.keys() | stats_already.keys() | stats_skipped.keys()):
                c = stats_created[vt]
                a = stats_already[vt]
                s = stats_skipped[vt]
                self.stdout.write(f"  {vt:18} -> Created: {c:6} | Already exist: {a:6} | Skipped: {s:6}")

            self.stdout.write("\nYEAR-WISE BREAKDOWN:")
            for fy in sorted(stats_by_fy.keys()):
                c = stats_by_fy[fy]["created"]
                a = stats_by_fy[fy]["already"]
                s = stats_by_fy[fy]["skipped"]
                self.stdout.write(f"  {fy:10} -> Created: {c:6} | Already exist: {a:6} | Skipped: {s:6}")

            if skip_reasons:
                self.stdout.write("\nSKIP REASONS:")
                for reason, count in skip_reasons.items():
                    self.stdout.write(f"  {reason}: {count}")

            self.stdout.write(self.style.SUCCESS("========================================================\n"))

            if dry_run:
                self.stdout.write(self.style.WARNING("Dry run complete. Rolling back transaction -- no data was committed."))
                transaction.set_rollback(True)
            else:
                self.stdout.write(self.style.SUCCESS("Commit complete! All historical documents safely saved to database."))

    def _ensure_placeholder_item(self) -> Item:
        item = Item.objects.filter(code=EXPENSE_PLACEHOLDER_CODE).first()
        if not item:
            group = ItemGroup.objects.get(name="All Item Groups")
            uom = UOM.objects.get(name="Nos")
            item = Item.objects.create(
                code=EXPENSE_PLACEHOLDER_CODE,
                name="Tally Purchase Expense",
                item_group=group,
                stock_uom=uom,
                is_stock_item=False,
                standard_rate=Decimal("0.00"),
            )
            self.stdout.write(f"Created non-stock placeholder item '{item.name}' ({item.code}).")
        return item

    def _ensure_cash_customer(self, company: Company) -> Customer:
        cust = Customer.objects.filter(name="Cash Customer", company=company).first()
        if not cust:
            group = CustomerGroup.objects.filter(name__icontains="Sundry Debtors").first() or CustomerGroup.objects.first()
            cust = Customer.objects.create(
                name="Cash Customer",
                company=company,
                customer_group=group,
                gst_category="Unregistered",
            )
            self.stdout.write(f"Created default '{cust.name}'.")
        return cust
