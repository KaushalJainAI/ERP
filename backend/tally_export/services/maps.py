"""Ledger / stock-item name resolution and settings."""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


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


#: ERP group-account names that differ from the Tally group they came from.
_ERP_TO_TALLY_GROUP = {
    "Sundry Debtors (Group)": "Sundry Debtors",
    "Sundry Creditors (Group)": "Sundry Creditors",
}


def erp_group_names(company) -> Dict[str, str]:
    """ERP group-account name -> Tally group name, where the two differ."""
    return dict(_ERP_TO_TALLY_GROUP)


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


def suggest_ledger(account) -> str:
    """Tally ledger name proposed for an unmapped ERP account: the conventional
    ``Input CGST`` / ``Output IGST`` ... for a GST-tagged account, else its name."""
    tag = (account.gst_direction, account.gst_component)
    return C.GST_LEDGER_NAMES.get(tag) or account.name


def party_group(party_type: str) -> str:
    return C.G_DEBTORS if party_type == "Customer" else C.G_CREDITORS
