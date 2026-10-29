"""Ledger / stock-item name resolution, settings and legacy seeding."""

import re
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from django.db import transaction

from tally_export import constants as C
from tally_export.models import (
    TallyExportSettings,
    TallyLedgerMap,
    TallyStockItemMap,
)

PARTY_ACCOUNT_TYPES = ("Receivable", "Payable")


def get_settings(company) -> TallyExportSettings:
    """The company's settings row (created on first use with sane defaults)."""
    obj, _ = TallyExportSettings.objects.get_or_create(
        company=company,
        defaults={
            "tally_company_name": company.name,
            "excluded_prefixes": list(C.DEFAULT_EXCLUDED_PREFIXES),
        },
    )
    return obj


def voucher_type_for(settings: TallyExportSettings, doctype: str, variant: str = ""):
    """Tally voucher type of an ERP document (variant first, then the doctype,
    company overrides over the defaults)."""
    table = {**C.DEFAULT_VOUCHER_TYPES, **(settings.voucher_type_overrides or {})}
    if variant:
        hit = table.get(f"{doctype}:{variant}")
        if hit:
            return hit
    return table.get(doctype) or C.FALLBACK_VOUCHER_TYPE


def is_party_account(account, party: Optional[str]) -> bool:
    """A line that must go to the *party's* Tally ledger, not the control account."""
    return bool(party) and account.account_type in PARTY_ACCOUNT_TYPES


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Unmapped:
    """One thing the export cannot name in Tally yet."""

    kind: str  # "account" | "party" | "item"
    key: tuple  # account: (id,)  party: (type, name, account_id)  item: (id,)

    def as_dict(self):
        return {"kind": self.kind, "key": list(self.key)}


class MapIndex:
    """All maps of one company, loaded once per export run."""

    def __init__(self, company):
        self.company = company
        self.accounts: Dict[int, str] = {}
        self.parties: Dict[Tuple[str, str], List[Tuple[int, str]]] = {}
        for m in TallyLedgerMap.objects.filter(company=company):
            if m.party:
                self.parties.setdefault((m.party_type, m.party), []).append(
                    (m.account_id, m.tally_ledger)
                )
            else:
                self.accounts[m.account_id] = m.tally_ledger
        self.items: Dict[int, Tuple[str, str]] = {
            m.item_id: (m.tally_item, m.tally_unit)
            for m in TallyStockItemMap.objects.filter(company=company)
        }

    def ledger(self, account, party_type: Optional[str], party: Optional[str]):
        """``(tally ledger name | None, Unmapped | None)`` for a GL line."""
        if is_party_account(account, party):
            hits = self.parties.get((party_type or "", party or ""), [])
            for acc_id, name in hits:
                if acc_id == account.pk:
                    return name, None
            if hits:  # mapped under another control account of the same party
                return hits[0][1], None
            return None, Unmapped("party", (party_type or "", party, account.pk))
        name = self.accounts.get(account.pk)
        if name:
            return name, None
        return None, Unmapped("account", (account.pk,))

    def item(self, item):
        hit = self.items.get(item.pk)
        if hit:
            return hit, None
        return None, Unmapped("item", (item.pk,))


# --------------------------------------------------------------------------
# Suggestions (what an unmapped thing would be called / grouped as in Tally)
# --------------------------------------------------------------------------

_SALE = re.compile(r"sale", re.I)
_PURCHASE = re.compile(r"purchase", re.I)


def legacy_group_names(company) -> Dict[str, str]:
    """ERP group-account name -> Tally group name, from the imported Tally groups."""
    from legacy.models import TallyRecord

    out: Dict[str, str] = {}
    qs = TallyRecord.objects.filter(company=company, kind="group").exclude(erp_name="")
    for rec in qs.order_by("fiscal_year").only("erp_name", "name"):
        out[rec.erp_name] = rec.name  # latest year wins
    return out


def suggest_group(account, legacy_groups: Optional[Dict[str, str]] = None) -> str:
    """Tally parent group for a new ledger created from an ERP account."""
    legacy_groups = legacy_groups or {}
    node, seen = account.parent, set()
    while node is not None and node.pk not in seen:
        seen.add(node.pk)
        if node.name in legacy_groups:
            return legacy_groups[node.name]
        node = node.parent
    at = account.account_type
    by_type = {
        "Bank": C.G_BANK,
        "Cash": C.G_CASH,
        "Tax": C.G_TAXES,
        "Stock": C.G_STOCK,
        "Cost of Goods Sold": C.G_DIRECT_EXP,
        "Round Off": C.G_INDIRECT_EXP,
        "Receivable": C.G_DEBTORS,
        "Payable": C.G_CREDITORS,
    }
    if at in by_type:
        return by_type[at]
    root = account.root_type
    if root == "Income":
        return C.G_SALES if _SALE.search(account.name) else C.G_INDIRECT_INC
    if root == "Expense":
        return C.G_PURCHASE if _PURCHASE.search(account.name) else C.G_INDIRECT_EXP
    if root == "Equity":
        return C.G_CAPITAL
    if root == "Liability":
        return C.G_CURRENT_LIABS
    return C.G_CURRENT_ASSETS


def party_group(party_type: str) -> str:
    return C.G_DEBTORS if party_type == "Customer" else C.G_CREDITORS


# --------------------------------------------------------------------------
# Seeding from the legacy Tally archive
# --------------------------------------------------------------------------

_BASEUNITS = re.compile(r"<BASEUNITS>\s*([^<]*?)\s*</BASEUNITS>")


def _control_account(company, party_type: str):
    from accounts.models import Account

    want = "Receivable" if party_type == "Customer" else "Payable"
    return (
        Account.objects.filter(company=company, account_type=want, is_group=False)
        .order_by("id")
        .first()
    )


@transaction.atomic
def seed_from_legacy(company) -> Dict[str, int]:
    """Create maps from the imported Tally masters (``legacy.TallyRecord``).

    A Tally *ledger* record links to an ERP Account, Customer or Supplier; the
    Tally ``name`` (exact spelling, spaces and all) becomes the ledger name.
    A Tally *stock item* record links to an ERP Item. Existing maps are never
    overwritten. Voucher type names (custom ones such as "Sales Gst") are taken
    from the company's most recent year of vouchers.
    """
    from accounts.models import Account
    from buying.models import Supplier
    from legacy.models import TallyRecord
    from selling.models import Customer
    from stock.models import Item

    counts = Counter()
    existing_ledgers = {
        (m.account_id, m.party_type, m.party)
        for m in TallyLedgerMap.objects.filter(company=company)
    }
    existing_items = set(
        TallyStockItemMap.objects.filter(company=company).values_list(
            "item_id", flat=True
        )
    )
    ledgers = (
        TallyRecord.objects.filter(company=company, kind="ledger")
        .exclude(erp_links=[])
        .order_by("fiscal_year", "id")
    )
    # Latest year wins: iterate oldest -> newest and keep the last per link.
    latest: Dict[Tuple[str, str], str] = {}
    for rec in ledgers.only("name", "erp_links"):
        for link in rec.erp_links or []:
            latest[(link["doctype"], link["name"])] = rec.name
    parties_ctrl: Dict[str, Optional[object]] = {}
    for (doctype, name), tally_name in latest.items():
        if doctype == "accounts.Account":
            acct = Account.objects.filter(
                company=company, name=name, is_group=False
            ).first()
            if acct and (acct.pk, "", "") not in existing_ledgers:
                TallyLedgerMap.objects.create(
                    company=company,
                    account=acct,
                    tally_ledger=tally_name,
                    source="legacy",
                )
                existing_ledgers.add((acct.pk, "", ""))
                counts["accounts"] += 1
        elif doctype in ("selling.Customer", "buying.Supplier"):
            ptype = "Customer" if doctype == "selling.Customer" else "Supplier"
            model = Customer if ptype == "Customer" else Supplier
            if not model.objects.filter(company=company, name=name).exists():
                continue
            if ptype not in parties_ctrl:
                parties_ctrl[ptype] = _control_account(company, ptype)
            ctrl = parties_ctrl[ptype]
            if ctrl is None or (ctrl.pk, ptype, name) in existing_ledgers:
                continue
            TallyLedgerMap.objects.create(
                company=company,
                account=ctrl,
                party_type=ptype,
                party=name,
                tally_ledger=tally_name,
                source="legacy",
            )
            existing_ledgers.add((ctrl.pk, ptype, name))
            counts["parties"] += 1

    items = (
        TallyRecord.objects.filter(company=company, kind="stock_item")
        .exclude(erp_links=[])
        .order_by("fiscal_year", "id")
    )
    latest_item: Dict[str, Tuple[str, str]] = {}
    for rec in items.only("name", "erp_links", "raw"):
        m = _BASEUNITS.search(rec.raw or "")
        unit = m.group(1) if m else ""
        for link in rec.erp_links or []:
            if link["doctype"] == "stock.Item":
                latest_item[link["name"]] = (rec.name, unit)
    for code, (tally_name, unit) in latest_item.items():
        item = Item.objects.filter(code=code).select_related("stock_uom").first()
        if item is None or item.pk in existing_items:
            continue
        TallyStockItemMap.objects.create(
            company=company,
            item=item,
            tally_item=tally_name,
            tally_unit=unit or item.stock_uom.name,
            source="legacy",
        )
        existing_items.add(item.pk)
        counts["items"] += 1

    # The company's own voucher type names, from its latest year of vouchers.
    settings = get_settings(company)
    latest_fy = (
        TallyRecord.objects.filter(company=company, kind="voucher")
        .order_by("-fiscal_year")
        .values_list("fiscal_year", flat=True)
        .first()
    )
    if latest_fy:
        names: Dict[str, Counter] = {}
        for base, vt in TallyRecord.objects.filter(
            company=company, kind="voucher", fiscal_year=latest_fy
        ).values_list("base_voucher_type", "voucher_type"):
            if base and vt:
                names.setdefault(base, Counter())[vt] += 1
        overrides = dict(settings.voucher_type_overrides or {})
        for base, counter in names.items():
            best = counter.most_common(1)[0][0]
            for key in C.LEGACY_BASE_TO_KEYS.get(base, []):
                if best != C.DEFAULT_VOUCHER_TYPES.get(key) and key not in overrides:
                    overrides[key] = best
                    counts["voucher_types"] += 1
        if overrides != (settings.voucher_type_overrides or {}):
            settings.voucher_type_overrides = overrides
            settings.save(update_fields=["voucher_type_overrides", "modified_at"])
    return dict(counts)
