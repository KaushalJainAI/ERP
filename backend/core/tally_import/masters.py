"""Chart of accounts, parties and the pure mapping helpers.

The helpers at the top of the module are pure functions (no DB, no Django
models) so they are unit-testable without the Tally database.
"""

import datetime
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, Iterable, List, Optional, Set, Tuple

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

COMPANY_NAME = "NIDHI GRAH UDYOG"
COMPANY_ABBR = "NGU"

ROOTS = [  # (account name, root_type)
    ("Assets", "Asset"),
    ("Liabilities", "Liability"),
    ("Equity", "Equity"),
    ("Income", "Income"),
    ("Expenses", "Expense"),
]
ROOT_NAME = {rt: n for n, rt in ROOTS}

DEBTORS_LEAF = "Sundry Debtors"
CREDITORS_LEAF = "Sundry Creditors"
STOCK_IN_HAND = "Stock In Hand"
DIFF_OPENING = "Difference in Opening Balances"
CHANGE_IN_STOCK = "Change in Stock"
PNL_LEDGER = "Profit & Loss A/c"

#: Accounts the importer creates itself; ledger/group names must never collide
#: with these (the loser gets a suffix).
RESERVED_ACCOUNT_NAMES = {
    *(n for n, _ in ROOTS),
    DEBTORS_LEAF,
    CREDITORS_LEAF,
    STOCK_IN_HAND,
    DIFF_OPENING,
    CHANGE_IN_STOCK,
}

#: group (matched case-insensitively on reservedname-or-name) -> account_type
ACCOUNT_TYPE_BY_GROUP = {
    "bank accounts": "Bank",
    "bank od a/c": "Bank",
    "cash-in-hand": "Cash",
    "duties & taxes": "Tax",
    "stock-in-hand": "Stock",
}

#: Tally unit name (lower-cased) -> ERP UOM name. Seeded UOMs are
#: Nos, Kg, Meter, Litre, Box, Set and are reused where they match.
UOM_MAP = {
    "k.g": "Kg",
    "kg": "Kg",
    "kgs": "Kg",
    "ltr.": "Litre",
    "ltr": "Litre",
    "litr": "Litre",
    "no.": "Nos",
    "nos": "Nos",
    "no": "Nos",
    "pcs": "Pcs",
    "pic": "Pcs",
    "pics": "Pcs",
    "box": "Box",
    "drzn": "Dozen",
    "gm": "Gram",
    "mtr": "Meter",
    "qtly": "Quintal",
    "not applicable": "Nos",
    "": "Nos",
}

_WS = re.compile(r"\s+")
_TWO = Decimal("0.01")
ZERO = Decimal("0.00")


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------


def clean_name(name: Optional[str], max_len: int = 200) -> str:
    """Trim and collapse runs of whitespace ("Shri Kasturi  Builders" ->
    "Shri Kasturi Builders"); truncate to ``max_len``."""
    return _WS.sub(" ", (name or "").strip())[:max_len]


def assign_unique_names(
    raw_names: Iterable[str],
    reserved: Iterable[str] = (),
    max_len: int = 200,
    clash_suffix: str = " ({n})",
) -> Dict[str, str]:
    """Map every raw (Tally) name to a unique cleaned name.

    Names are processed in sorted order so the result is deterministic. A name
    that collides with ``reserved`` or an earlier name gets `` (2)``, `` (3)``...
    """
    taken: Set[str] = set(reserved)
    out: Dict[str, str] = {}
    for raw in sorted(set(raw_names)):
        base = clean_name(raw, max_len)
        cand = base
        n = 2
        while cand in taken:
            suffix = clash_suffix.format(n=n)
            cand = base[: max_len - len(suffix)] + suffix
            n += 1
        taken.add(cand)
        out[raw] = cand
    return out


def resolve_group_names(
    group_names: Iterable[str], reserved: Iterable[str], suffix: str = " (Group)"
) -> Dict[str, str]:
    """Group accounts yield to ledger/special leaves: on a clash the *group* is
    renamed ``<name> (Group)`` (ledger "Freight & Cartage" vs group of the same
    name)."""
    taken: Set[str] = set(reserved)
    out: Dict[str, str] = {}
    for raw in sorted(set(group_names)):
        base = clean_name(raw)
        cand = base
        if cand in taken:
            cand = base + suffix
            n = 2
            while cand in taken:
                cand = f"{base}{suffix} {n}"
                n += 1
        taken.add(cand)
        out[raw] = cand
    return out


def group_key(name: str, reservedname: Optional[str] = None) -> str:
    """Stable identity of a Tally group: its reserved (system) name if it has
    one, else its name."""
    return (reservedname or name or "").strip()


def root_type_for(primary_key: str, is_revenue: bool, is_deemed_positive: bool) -> str:
    """Root type of a primary group.

    revenue + deemed-positive -> Expense; revenue -> Income;
    not revenue + deemed-positive -> Asset; otherwise Liability.
    Override: "Capital Account" (and therefore its subtree) -> Equity.
    """
    if (primary_key or "").strip().lower() == "capital account":
        return "Equity"
    if is_revenue:
        return "Expense" if is_deemed_positive else "Income"
    return "Asset" if is_deemed_positive else "Liability"


def account_type_for(chain_keys: Iterable[str]) -> Optional[str]:
    """account_type of a ledger from the groups above it (direct group first)."""
    for key in chain_keys:
        t = ACCOUNT_TYPE_BY_GROUP.get((key or "").strip().lower())
        if t:
            return t
    return None


def party_kind(chain_keys: Iterable[str]) -> str:
    """'debtor' / 'creditor' / 'account' from the groups above a ledger."""
    lowered = {(k or "").strip().lower() for k in chain_keys}
    if "sundry debtors" in lowered:
        return "debtor"
    if "sundry creditors" in lowered:
        return "creditor"
    return "account"


def drcr(amount) -> Tuple[Decimal, Decimal]:
    """Tally amount (negative = debit, positive = credit) -> (debit, credit),
    both non-negative and quantized to paise."""
    a = Decimal(str(amount)).quantize(_TWO, rounding=ROUND_HALF_UP)
    if a < 0:
        return -a, ZERO
    return ZERO, a


def signed_debit(amount) -> Decimal:
    """Tally amount -> debit-positive Decimal (what the ERP balance uses)."""
    return -Decimal(str(amount)).quantize(_TWO, rounding=ROUND_HALF_UP)


def fiscal_year_name(fy: str) -> str:
    """'2024-25' -> '2024-2025'."""
    start = int(fy[:4])
    return f"{start}-{start + 1}"


def fiscal_year_dates(fy: str) -> Tuple[datetime.date, datetime.date]:
    start = int(fy[:4])
    return datetime.date(start, 4, 1), datetime.date(start + 1, 3, 31)


def year_code(fy: str) -> str:
    """'2024-25' -> '2425' (used in TLY-<code>-... document names)."""
    start = int(fy[:4])
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def je_name(fy: str, seq) -> str:
    """TLY-2425-000123 for an int sequence, TLY-2425-OPEN for a tag."""
    code = year_code(fy)
    if isinstance(seq, int):
        return f"TLY-{code}-{seq:06d}"
    return f"TLY-{code}-{seq}"


def normalise_uom(tally_unit: Optional[str]) -> str:
    """Tally unit name -> ERP UOM name ('k.g' -> 'Kg', 'ltr.' -> 'Litre')."""
    key = (tally_unit or "").strip().lower()
    if key in UOM_MAP:
        return UOM_MAP[key]
    return clean_name(tally_unit, 40).title()


def money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(_TWO, rounding=ROUND_HALF_UP)


# -- GST helpers -----------------------------------------------------------

_GST_TAG_A = re.compile(r"^\s*(input|output)\W*(cgst|sgst|igst|cess)\b", re.I)
_GST_TAG_B = re.compile(r"^\s*(cgst|sgst|igst|cess)\W*(input|output)\b", re.I)
_HSN_TAG = re.compile(r"<HSNCODE>\s*([^<]*?)\s*</HSNCODE>")


def gst_tag_for_ledger(name: str) -> Optional[Tuple[str, str]]:
    """(gst_direction, gst_component) for an unambiguous Tally GST ledger name
    such as "Output CGST" or "IGST - Input"; None otherwise. Tally ledgers like
    "C Gst" carry both input and output and are deliberately NOT tagged."""
    n = name or ""
    m = _GST_TAG_A.match(n)
    if m:
        direction, comp = m.group(1), m.group(2)
    else:
        m = _GST_TAG_B.match(n)
        if not m:
            return None
        comp, direction = m.group(1), m.group(2)
    return direction.capitalize(), comp.upper()


def normalise_hsn(code: Optional[str]) -> Optional[str]:
    """HSN/SAC must be 4, 6 or 8 digits; anything else -> None. An odd length
    (3, 5, 7: a lost leading zero, e.g. 9109100 for 09109100) is repaired by
    prefixing one zero."""
    c = (code or "").strip()
    if c.isascii() and c.isdigit() and len(c) in (3, 5, 7):
        c = "0" + c
    if c.isascii() and c.isdigit() and len(c) in (4, 6, 8):
        return c
    return None


def hsn_from_xml(raw_xml: Optional[str]) -> Optional[str]:
    """Last non-empty <HSNCODE> text of a Tally stock item XML record."""
    found = [c for c in _HSN_TAG.findall(raw_xml or "") if c]
    return found[-1] if found else None


def item_gst_flags(taxability: Optional[str], non_gst: Optional[str] = None):
    """(is_nil_exempt, is_non_gst) from Tally's item taxability."""
    t = (taxability or "").strip().lower()
    ng = (non_gst or "").strip().lower() in ("yes", "true", "1")
    if ng or t in ("non-gst", "non gst"):
        return False, True
    return t in ("exempt", "nil rated", "nil-rated", "nil rated/exempt"), False


def item_tax_template_name(
    taxability: Optional[str], non_gst: Optional[str], igst_rate
) -> Optional[str]:
    """Seeded ItemTaxTemplate name for a Tally item (None if no rate known)."""
    nil_exempt, is_non_gst = item_gst_flags(taxability, non_gst)
    if is_non_gst:
        return "Non-GST"
    if nil_exempt:
        return "Exempted" if "exempt" in (taxability or "").lower() else "Nil Rated"
    if igst_rate is None:
        return None
    rate = Decimal(igst_rate).normalize()
    return f"GST {rate:f}%"


def state_code_from_name(statename: Optional[str]) -> Optional[str]:
    """Tally ledstatename -> 2-digit GST state code (None if unknown)."""
    from core.gst_states import STATES

    n = clean_name(statename).lower()
    if not n:
        return None
    for code, sname, _abbr, _ut, active in STATES:
        if active and sname.lower() == n:
            return code
    aliases = {
        "orissa": "21",
        "pondicherry": "34",
        "jammu and kashmir": "01",
        "chattisgarh": "22",
        "telengana": "36",
    }
    return aliases.get(n)


def party_gst_fields(gstin, reg_type, state_name):
    """Tally party GST data -> (gstin, gst_category, state_code, gstin_invalid).

    * GSTIN present: category "Registered Regular"; state from the GSTIN prefix if
      it validates, else None (flagged invalid, GSTIN kept).
    * No GSTIN: "Consumer" if Tally says Consumer, else "Unregistered"; state from
      Tally's ledstatename.
    """
    from gst.validators import check_gstin

    g = (gstin or "").strip().upper() or None
    if g:
        res = check_gstin(g)
        if res.valid:
            return g, "Registered Regular", res.state_code, False
        return g, "Registered Regular", None, True
    cat = (
        "Consumer" if (reg_type or "").strip().lower() == "consumer" else "Unregistered"
    )
    return None, cat, state_code_from_name(state_name), False


# --------------------------------------------------------------------------
# Per-year classification of groups and ledgers (pure, takes row dicts)
# --------------------------------------------------------------------------


def group_chain(groups: Dict[str, dict], start: str) -> List[str]:
    """Group *keys* from ``start`` up to and including its primary group.
    ``groups`` maps group name -> row dict. Stops at 'Primary' / unknown parent
    and guards against cycles."""
    chain: List[str] = []
    seen: Set[str] = set()
    cur = start
    while cur in groups and cur not in seen:
        seen.add(cur)
        row = groups[cur]
        chain.append(group_key(row["name"], row.get("reservedname")))
        parent = row.get("parent")
        if not parent or parent == "Primary":
            break
        cur = parent
    return chain


def primary_group_name(groups: Dict[str, dict], start: str) -> Optional[str]:
    """Raw name of the primary group above/at ``start`` (None if unresolved)."""
    seen: Set[str] = set()
    cur = start
    while cur in groups and cur not in seen:
        seen.add(cur)
        parent = groups[cur].get("parent")
        if not parent or parent == "Primary":
            return cur
        cur = parent
    return None


def root_type_of_group(groups: Dict[str, dict], name: str) -> Optional[str]:
    prim = primary_group_name(groups, name)
    if prim is None:
        return None
    row = groups[prim]
    return root_type_for(
        group_key(row["name"], row.get("reservedname")),
        bool(row.get("isrevenue")),
        bool(row.get("isdeemedpositive")),
    )


@dataclass
class LedgerClass:
    kind: str  # 'debtor' | 'creditor' | 'account'
    root_type: str
    account_type: Optional[str]
    chain: List[str]
    parent: str  # raw Tally parent group name ('Primary' for P&L A/c)


def classify_ledger(groups: Dict[str, dict], ledger: dict) -> LedgerClass:
    parent = ledger.get("parent") or "Primary"
    if parent not in groups:  # 'Primary' (Profit & Loss A/c) or unknown
        return LedgerClass("account", "Equity", None, [], parent)
    chain = group_chain(groups, parent)
    root = root_type_of_group(groups, parent) or "Liability"
    return LedgerClass(party_kind(chain), root, account_type_for(chain), chain, parent)


# --------------------------------------------------------------------------
# Master data consolidated over all years
# --------------------------------------------------------------------------


@dataclass
class LedgerHistory:
    """One Tally ledger name across all years."""

    name: str
    by_year: Dict[str, dict] = field(default_factory=dict)  # fy -> row
    class_by_year: Dict[str, LedgerClass] = field(default_factory=dict)  # fy -> cls

    def latest(self, kind: Optional[str] = None, kinds: Optional[Set[str]] = None):
        """(fy, row, cls) of the latest year (optionally of the given kind(s))."""
        for fy in sorted(self.by_year, reverse=True):
            cls = self.class_by_year[fy]
            if kind and cls.kind != kind:
                continue
            if kinds and cls.kind not in kinds:
                continue
            return fy, self.by_year[fy], cls
        return None


def latest_non_empty(history: LedgerHistory, column: str, kind: str):
    """Latest non-empty value of ``column`` over years in which the ledger was
    of ``kind`` ("latest year wins", falling back to earlier years if blank)."""
    for fy in sorted(history.by_year, reverse=True):
        if history.class_by_year[fy].kind != kind:
            continue
        v = history.by_year[fy].get(column)
        if v is not None and str(v).strip() not in ("", "0"):
            return str(v).strip()
    return None


def consolidate_ledgers(
    years: List[Tuple[str, str]],  # [(cy, fy)] in fy order
    groups_by_cy: Dict[str, Dict[str, dict]],
    ledgers_by_cy: Dict[str, List[dict]],
) -> Dict[str, LedgerHistory]:
    out: Dict[str, LedgerHistory] = {}
    for cy, fy in years:
        groups = groups_by_cy[cy]
        for row in ledgers_by_cy[cy]:
            h = out.setdefault(row["name"], LedgerHistory(row["name"]))
            h.by_year[fy] = row
            h.class_by_year[fy] = classify_ledger(groups, row)
    return out


# --------------------------------------------------------------------------
# DB builders (imported lazily so the pure helpers stay importable without Django)
# --------------------------------------------------------------------------


@dataclass
class Chart:
    company: object
    # Tally ledger name -> leaf Account (only ledgers that are accounts in some year)
    accounts: Dict[str, object] = field(default_factory=dict)
    # Tally ledger name -> ERP root_type of that account
    account_root: Dict[str, str] = field(default_factory=dict)
    group_accounts: Dict[str, object] = field(default_factory=dict)
    debtors: object = None
    creditors: object = None
    stock_in_hand: object = None
    diff_opening: object = None
    change_in_stock: object = None
    pnl: object = None
    # Tally ledger name -> Customer.name / Supplier.name
    customers: Dict[str, str] = field(default_factory=dict)
    suppliers: Dict[str, str] = field(default_factory=dict)
    # fy -> ledger name -> 'debtor' | 'creditor' | 'account'
    kind: Dict[str, Dict[str, str]] = field(default_factory=dict)
    # report extras
    notes: Dict[str, object] = field(default_factory=dict)

    def all_account_ids(self) -> Dict[int, object]:
        accs = {}
        for a in list(self.accounts.values()) + list(self.group_accounts.values()):
            accs[a.pk] = a
        for a in (
            self.debtors,
            self.creditors,
            self.stock_in_hand,
            self.diff_opening,
            self.change_in_stock,
            self.pnl,
        ):
            if a is not None:
                accs[a.pk] = a
        return accs


def setup_company(gstin: Optional[str] = None):
    """Create company NGU (periodic inventory), deactivate the demo ACME."""
    from core.models import Company

    company, created = Company.objects.get_or_create(
        abbr=COMPANY_ABBR,
        defaults={
            "name": COMPANY_NAME,
            "default_currency": "INR",
            "country": "India",
            "gstin": gstin,
            "state_id": "23",  # Madhya Pradesh (tally_full company_ref state)
            "gst_category": "Registered Regular" if gstin else "Unregistered",
            "enable_perpetual_inventory": False,
            "is_active": True,
        },
    )
    if not created and company.enable_perpetual_inventory:
        company.enable_perpetual_inventory = False
        company.save(update_fields=["enable_perpetual_inventory"])
    Company.objects.filter(abbr="ACME").update(is_active=False)
    return company


def setup_fiscal_years(years: List[Tuple[str, str]]):
    """One FiscalYear per Tally year (global, reused by name), all open."""
    from core.models import FiscalYear

    created = 0
    for _cy, fy in years:
        start, end = fiscal_year_dates(fy)
        _obj, c = FiscalYear.objects.get_or_create(
            name=fiscal_year_name(fy),
            defaults={"start_date": start, "end_date": end, "is_closed": False},
        )
        created += int(c)
    return created


def build_chart(src, years, company, log=print) -> Chart:
    """Create the chart of accounts + parties from the union of all years.

    ``years`` is a list of ``YearInfo`` in fy order. Latest year wins for
    attributes. Idempotent (get_or_create) so a later partial run reuses it.
    """
    from accounts.models import Account
    from buying.models import Supplier, SupplierGroup
    from core.models import State
    from selling.models import Customer, CustomerGroup

    pairs = [(y.cy, y.fy) for y in years]
    groups_by_cy: Dict[str, Dict[str, dict]] = {}
    ledgers_by_cy: Dict[str, List[dict]] = {}
    for y in years:
        groups_by_cy[y.cy] = {g["name"]: g for g in src.groups(y.cy)}
        ledgers_by_cy[y.cy] = src.ledgers(y.cy)
    hist = consolidate_ledgers(pairs, groups_by_cy, ledgers_by_cy)

    chart = Chart(company=company)
    for y in years:
        chart.kind[y.fy] = {
            n: h.class_by_year[y.fy].kind
            for n, h in hist.items()
            if y.fy in h.class_by_year
        }

    # latest record of every group name (latest year wins)
    latest_groups: Dict[str, dict] = {}
    latest_groups_by_year_of: Dict[str, str] = {}
    for y in years:
        for name, row in groups_by_cy[y.cy].items():
            latest_groups[name] = row
            latest_groups_by_year_of[name] = y.cy
    # root type per group computed within the year the group was last seen
    group_root: Dict[str, str] = {}
    for name in latest_groups:
        cy = latest_groups_by_year_of[name]
        group_root[name] = root_type_of_group(groups_by_cy[cy], name) or "Liability"

    # ---- names ----
    account_ledgers = {
        n: h for n, h in hist.items() if h.latest(kind="account") is not None
    }
    ledger_names = assign_unique_names(account_ledgers, reserved=RESERVED_ACCOUNT_NAMES)
    group_names = resolve_group_names(
        latest_groups, set(ledger_names.values()) | RESERVED_ACCOUNT_NAMES
    )
    chart.notes["group_renamed"] = {
        g: n for g, n in group_names.items() if n != clean_name(g)
    }
    chart.notes["ledger_renamed"] = {r: n for r, n in ledger_names.items() if n != r}

    def acct(name, root_type, parent, is_group, account_type=None):
        obj, _ = Account.objects.get_or_create(
            company=company,
            name=name,
            defaults={
                "root_type": root_type,
                "parent": parent,
                "is_group": is_group,
                "account_type": account_type,
            },
        )
        return obj

    # ---- roots ----
    roots = {rt: acct(n, rt, None, True) for n, rt in ROOTS}

    # ---- group accounts, parents before children ----
    def depth(g):
        d, seen, cur = 0, set(), g
        while latest_groups[cur].get("parent") in latest_groups and cur not in seen:
            seen.add(cur)
            cur = latest_groups[cur]["parent"]
            d += 1
        return d

    for g in sorted(latest_groups, key=lambda x: (depth(x), x)):
        row = latest_groups[g]
        parent_name = row.get("parent")
        parent = (
            chart.group_accounts[parent_name]
            if parent_name in chart.group_accounts
            else roots[group_root[g]]
        )
        chart.group_accounts[g] = acct(group_names[g], group_root[g], parent, True)

    sd = chart.group_accounts.get("Sundry Debtors")
    sc = chart.group_accounts.get("Sundry Creditors")
    sih = chart.group_accounts.get("Stock-in-hand")

    # ---- control + special leaves ----
    chart.debtors = acct(
        DEBTORS_LEAF, "Asset", sd or roots["Asset"], False, "Receivable"
    )
    chart.creditors = acct(
        CREDITORS_LEAF, "Liability", sc or roots["Liability"], False, "Payable"
    )
    chart.stock_in_hand = acct(
        STOCK_IN_HAND, "Asset", sih or roots["Asset"], False, "Stock"
    )
    chart.diff_opening = acct(DIFF_OPENING, "Equity", roots["Equity"], False)
    chart.change_in_stock = acct(CHANGE_IN_STOCK, "Expense", roots["Expense"], False)

    # ---- ledger leaves ----
    for raw in sorted(account_ledgers):
        h = account_ledgers[raw]
        _fy, _row, cls = h.latest(kind="account")
        if cls.parent in chart.group_accounts:
            parent = chart.group_accounts[cls.parent]
        else:
            parent = roots[cls.root_type]
        a = acct(ledger_names[raw], cls.root_type, parent, False, cls.account_type)
        chart.accounts[raw] = a
        chart.account_root[raw] = a.root_type
        if raw == PNL_LEDGER:
            chart.pnl = a
    if chart.pnl is None:  # no P&L ledger in the books: create one
        chart.pnl = acct(PNL_LEDGER, "Equity", roots["Equity"], False)

    # ---- GST tags on Tally's own Tax ledgers (unambiguous names only) ----
    by_tag: Dict[Tuple[str, str], List[str]] = {}
    tax_ledgers = [r for r, a in chart.accounts.items() if a.account_type == "Tax"]
    for raw in tax_ledgers:
        tag = gst_tag_for_ledger(raw)
        if tag:
            by_tag.setdefault(tag, []).append(raw)
    tagged, untagged = [], []
    for raw in sorted(tax_ledgers):
        tag = gst_tag_for_ledger(raw)
        if tag and len(by_tag[tag]) == 1:
            a = chart.accounts[raw]
            if a.gst_direction is None:
                taken = Account.objects.filter(
                    company=company, gst_direction=tag[0], gst_component=tag[1]
                ).exists()
                if not taken:
                    a.gst_direction, a.gst_component = tag
                    a.save(
                        update_fields=["gst_direction", "gst_component", "modified_at"]
                    )
            tagged.append((raw, tag[0], tag[1]))
        else:
            untagged.append(raw)
    chart.notes["gst_tagged"] = tagged
    chart.notes["gst_untagged_tax"] = untagged

    # ---- parties ----
    all_cg = CustomerGroup.objects.get_or_create(
        name="All Customer Groups", defaults={"is_group": True}
    )[0]
    all_sg = SupplierGroup.objects.get_or_create(
        name="All Supplier Groups", defaults={"is_group": True}
    )[0]
    default_cg = CustomerGroup.objects.get_or_create(
        name=DEBTORS_LEAF, defaults={"parent": all_cg, "is_group": False}
    )[0]
    default_sg = SupplierGroup.objects.get_or_create(
        name=CREDITORS_LEAF, defaults={"parent": all_sg, "is_group": False}
    )[0]

    def party_groups(model, root_group, default, top_key):
        """Sub-groups under Sundry Debtors/Creditors -> Customer/SupplierGroup."""
        out = {}
        for g, row in latest_groups.items():
            cy = latest_groups_by_year_of[g]
            chain = group_chain(groups_by_cy[cy], g)
            lowered = [c.lower() for c in chain]
            if top_key in lowered and lowered[0] != top_key:
                out[g] = row
        made = {}
        for g in sorted(out, key=lambda x: (depth(x), x)):
            parent_name = latest_groups[g].get("parent")
            parent = made.get(parent_name, root_group)
            obj = model.objects.get_or_create(
                name=clean_name(g), defaults={"parent": parent, "is_group": True}
            )[0]
            made[g] = obj
        return made

    cust_groups = party_groups(CustomerGroup, all_cg, default_cg, "sundry debtors")
    supp_groups = party_groups(SupplierGroup, all_sg, default_sg, "sundry creditors")

    debtor_hist = {n: h for n, h in hist.items() if h.latest(kind="debtor")}
    creditor_hist = {n: h for n, h in hist.items() if h.latest(kind="creditor")}
    cust_names = assign_unique_names(debtor_hist)
    supp_names = assign_unique_names(creditor_hist)
    truncated = []
    states = set(State.objects.values_list("code", flat=True))
    gst_stats = {
        "parties": 0,
        "with_gstin": 0,
        "with_state": 0,
        "invalid_gstin": [],
    }
    chart.notes["party_gst"] = gst_stats

    def make_party(model, name, h, kind, groups_map, default_group, field_group):
        fy, row, cls = h.latest(kind=kind)
        grp = default_group
        if cls.parent in groups_map:
            grp = groups_map[cls.parent]
        gstin = latest_non_empty(h, "partygstin", kind)
        if gstin and len(gstin) > 20:
            truncated.append((name, "gstin", gstin))
            gstin = gstin[:20]
        pan = latest_non_empty(h, "incometaxnumber", kind)
        if pan and len(pan) > 40:
            truncated.append((name, "tax_id", pan))
            pan = pan[:40]
        gstin, category, state_code, invalid = party_gst_fields(
            gstin, row.get("gstregistrationtype"), row.get("ledstatename")
        )
        gst_stats["parties"] += 1
        if gstin:
            gst_stats["with_gstin"] += 1
        if invalid:
            gst_stats["invalid_gstin"].append((name, gstin))
        if state_code:
            gst_stats["with_state"] += 1
        if state_code and state_code not in states:
            state_code = None
        model.objects.get_or_create(
            company=company,
            name=name,
            defaults={
                field_group: grp,
                "gstin": gstin,
                "gst_category": category,
                "state_id": state_code,
                "tax_id": pan,
                "is_active": True,
            },
        )

    for raw in sorted(debtor_hist):
        make_party(
            Customer,
            cust_names[raw],
            debtor_hist[raw],
            "debtor",
            cust_groups,
            default_cg,
            "customer_group",
        )
        chart.customers[raw] = cust_names[raw]
    for raw in sorted(creditor_hist):
        make_party(
            Supplier,
            supp_names[raw],
            creditor_hist[raw],
            "creditor",
            supp_groups,
            default_sg,
            "supplier_group",
        )
        chart.suppliers[raw] = supp_names[raw]
    chart.notes["truncated"] = truncated
    chart.notes["customers_and_suppliers"] = sorted(
        set(chart.customers) & set(chart.suppliers)
    )

    # ledgers whose ERP root type differs from the one implied in an older year
    moved = []
    for raw, h in hist.items():
        roots_seen = {c.root_type for c in h.class_by_year.values()}
        if len(roots_seen) > 1:
            moved.append((raw, sorted(roots_seen)))
    chart.notes["root_changes"] = moved
    chart.notes["ledger_hist"] = len(hist)
    chart.notes["group_count"] = len(latest_groups)
    chart.notes["leaf_accounts"] = len(chart.accounts)
    # tally ledger rows per fy, keyed so voucher/opening code can look up
    chart.notes["ledgers_by_fy"] = {y.fy: ledgers_by_cy[y.cy] for y in years}
    return chart
