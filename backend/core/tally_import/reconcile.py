"""ERP-vs-Tally reconciliation and the markdown report.

Everything here reads the ERP through the ORM/GL (not the importer's in-memory
state), so ``--reconcile-only`` reproduces the same numbers on an already
imported database.
"""

import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from core.tally_import.masters import (
    PNL_LEDGER,
    ZERO,
    fiscal_year_dates,
    je_name,
    money,
    signed_debit,
)

REPORT_PATH = r"C:\Users\91700\Desktop\ERP\backend\docs\tally_import_report.md"

MODEL_CHANGES = [
    "JournalEntry has no voucher_type / voucher_number / reference fields: Tally voucher type, number "
    "and guid are only in user_remark (a text field, not searchable by type).",
    "JournalEntryLine has no bill reference / against_voucher (Tally bill-wise, 125 'On Account' rows "
    "dropped) and no per-line remark; GLEntry.against_voucher_* is therefore never filled.",
    "No allow-negative-stock setting (per item or per company): 7 items with negative Tally stock "
    "could not be imported (see the stock section).",
    "FiscalYear is global, not per company (acceptable for one company; ACME shares the 2026-2027 row).",
    "Customer/Supplier have no address, pincode or contact-person fields (Tally ledger mailing "
    "address, pincode and phone are not imported).",
    "StockEntry has no remark / reference field: the Tally voucher number and guid of each "
    "stock entry cannot be recorded (names TLY-2627-STK-<seq> follow Tally date / voucher order).",
    "SubmittableDocument.name is max 60 chars (sufficient for TLY-yyyy-nnnnnn).",
    "Account has no 'cost centre' / sub-ledger concept (Tally cost centres not imported).",
    "No opening-balance / bill-wise document type: yearly opening corrections had to be a "
    "JournalEntry to 'Difference in Opening Balances'.",
]


def _fmt(d) -> str:
    d = Decimal(d)
    return f"{d:,.2f}"


def erp_balances(end_date: datetime.date, exclude_voucher: Optional[str] = None):
    """{(account_id, party_type, party): debit-positive balance} as of end_date."""
    from django.db.models import Sum

    from accounts.models import GLEntry

    qs = GLEntry.objects.filter(is_cancelled=False, posting_date__lte=end_date)
    if exclude_voucher:
        qs = qs.exclude(voucher_no=exclude_voucher)
    out: Dict[tuple, Decimal] = {}
    for r in qs.values("account_id", "party_type", "party").annotate(
        dr=Sum("debit"), cr=Sum("credit")
    ):
        out[(r["account_id"], r["party_type"], r["party"])] = r["dr"] - r["cr"]
    return out


def erp_year_profit(fy: str) -> Decimal:
    """Credit-positive net profit of the year: Σ(credit - debit) on Income and
    Expense accounts dated in the year, excluding the period-closing entry."""
    from django.db.models import Sum

    from accounts.models import GLEntry

    start, end = fiscal_year_dates(fy)
    agg = (
        GLEntry.objects.filter(
            is_cancelled=False,
            posting_date__gte=start,
            posting_date__lte=end,
            account__root_type__in=["Income", "Expense"],
        )
        .exclude(voucher_no=je_name(fy, "PCV"))
        .aggregate(dr=Sum("debit"), cr=Sum("credit"))
    )
    return (agg["cr"] or ZERO) - (agg["dr"] or ZERO)


def ledger_key(chart, fy, ledger: str):
    kind = chart.kind[fy].get(ledger)
    if kind == "debtor":
        return (chart.debtors.pk, "Customer", chart.customers[ledger])
    if kind == "creditor":
        return (chart.creditors.pk, "Supplier", chart.suppliers[ledger])
    acct = chart.accounts.get(ledger)
    return (acct.pk, None, None) if acct else None


def reconcile_year(src, chart, y, health: Optional[dict]) -> dict:
    from core.models import FiscalYear  # noqa: F401
    from stock.models import Bin  # noqa: F401

    fy = y.fy
    start, end = fiscal_year_dates(fy)
    pcv = je_name(fy, "PCV")
    bal = erp_balances(end, exclude_voucher=pcv)
    accounts = chart.all_account_ids()
    rows = src.ledger_balances(y.cy)

    seen = set()
    sum_abs_a = sum_abs_b = ZERO
    n_a = n_b = n_a_bad = n_b_bad = 0
    mism: List[dict] = []
    pnl_row = None
    for r in rows:
        name = r["ledger"]
        if name == PNL_LEDGER:
            pnl_row = r
            seen.add(ledger_key(chart, fy, name))
            continue
        key = ledger_key(chart, fy, name)
        seen.add(key)
        erp = bal.get(key, ZERO)
        view_dr = signed_debit(r["closing"] or 0)
        d_b = erp - view_dr
        n_b += 1
        sum_abs_b += abs(d_b)
        if d_b != 0:
            n_b_bad += 1
        entry = dict(
            ledger=name,
            group=r["ledger_group"],
            erp=erp,
            view=view_dr,
            tally=None,
            d_b=d_b,
            d_a=None,
        )
        if r["tally_closing"] is not None:
            tal = signed_debit(r["tally_closing"])
            d_a = erp - tal
            n_a += 1
            sum_abs_a += abs(d_a)
            if d_a != 0:
                n_a_bad += 1
            entry.update(tally=tal, d_a=d_a)
        mism.append(entry)

    # stock in hand
    s_open = s_close = Decimal(0)
    for it in src.stock_items(y.cy):
        s_open += Decimal(it["openingvalue"] or 0)
        s_close += Decimal(it["closingvalue"] or 0)
    s_open, s_close = money(-s_open), money(-s_close)
    sih_key = (chart.stock_in_hand.pk, None, None)
    sih_erp = bal.get(sih_key, ZERO)
    completed = bool(health and health.get("completed"))

    # ERP-only balances (non-zero keys that are not Tally ledgers of this year)
    skip = {
        sih_key,
        (chart.diff_opening.pk, None, None),
        (chart.change_in_stock.pk, None, None),
    }
    extra = []
    for key, v in bal.items():
        if v == 0 or key in seen or key in skip:
            continue
        extra.append((accounts[key[0]].name, key[2], v))
    extra.sort(key=lambda x: -abs(x[2]))

    mism.sort(key=lambda e: -abs(e["d_a"] if e["d_a"] is not None else e["d_b"]))

    rev_mov = src.revenue_movement(y.cy)
    tally_formula = money(rev_mov + (s_close - s_open))
    erp_profit = money(erp_year_profit(fy))
    tally_own = None
    if pnl_row is not None and pnl_row["tally_closing"] is not None:
        tally_own = money(
            Decimal(pnl_row["tally_closing"]) - Decimal(pnl_row["opening"] or 0)
        )
    diff_bal = bal.get((chart.diff_opening.pk, None, None), ZERO)
    return dict(
        fy=fy,
        sum_abs_a=sum_abs_a,
        n_a=n_a,
        n_a_bad=n_a_bad,
        sum_abs_b=sum_abs_b,
        n_b=n_b,
        n_b_bad=n_b_bad,
        top=[m for m in mism if (m["d_a"] or m["d_b"])][:15],
        stock_erp=sih_erp,
        stock_tally=s_close,
        stock_open=s_open,
        erp_profit=erp_profit,
        tally_formula=tally_formula,
        tally_own=tally_own,
        extra=extra[:10],
        extra_sum=sum((abs(e[2]) for e in extra), ZERO),
        extra_n=len(extra),
        diff_bal=diff_bal,
        completed=completed,
    )


def stock_section(chart, sm_stats: Optional[dict]) -> List[str]:
    out = ["## FY 2026-27 stock ledger", ""]
    if not sm_stats:
        out += ["_No stock import stats found (stock not imported)._", ""]
        return out
    out += [
        f"- stock entries posted: {sm_stats['entries']} "
        f"({sm_stats['receipts']} receipts, {sm_stats['issues']} issues, "
        f"opening receipt items: {sm_stats['opening_items']})",
        f"- stock lines posted: {sm_stats['lines_posted']}; lines of excluded items skipped: "
        f"{sm_stats['lines_excluded']}; Tally lines without qty ignored: {sm_stats['lines_without_qty']}",
        "",
        "### Items excluded (would go negative; NO movements imported, needs a physical count)",
        "",
        "| item | Tally opening qty | min running balance | movements in 2026-27 | Tally closing qty |",
        "|---|---:|---:|---:|---:|",
    ]
    for e in sm_stats["excluded_items"]:
        out.append(
            f"| {e['item']} | {e['opening_qty']} | {e['min_balance']} | {e['movements']} | {e['tally_closing_qty']} |"
        )
    out += ["", "### Bin qty (ERP) vs Tally closingbalance_num", ""]
    bad = []
    ok = 0
    for r in sm_stats["bin_compare"]:
        if r["tally_qty"] is None and Decimal(r["erp_qty"]) == 0:
            ok += 1
            continue
        t = Decimal(r["tally_qty"] or 0)
        e = Decimal(r["erp_qty"])
        if t == e:
            ok += 1
        else:
            bad.append((r["item"], e, t, r["excluded"]))
    out.append(f"{ok} of {len(sm_stats['bin_compare'])} items agree exactly.")
    if bad:
        out += [
            "",
            "| item | ERP qty | Tally closing qty | difference | excluded item |",
            "|---|---:|---:|---:|---|",
        ]
        for item, e, t, ex in sorted(bad, key=lambda x: -abs(x[1] - x[2])):
            out.append(f"| {item} | {e} | {t} | {e - t} | {'yes' if ex else ''} |")
    out.append("")
    return out


def build_report(
    src,
    chart,
    years,
    health_by_fy: Dict[str, dict],
    stock_stats,
    verify_output,
    chart_notes,
    master_counts,
    runtime,
    not_imported,
    sm_notes,
) -> str:
    L: List[str] = []
    L += [
        "# Tally -> ERP import report (NIDHI GRAH UDYOG)",
        "",
        f"Generated {datetime.datetime.now():%Y-%m-%d %H:%M}. Runtime of the import: {runtime}.",
        "Sign convention in this report: ERP and Tally balances are **debit-positive** "
        "(Dr +, Cr -) unless stated.",
        "",
        "## Entities imported",
        "",
    ]
    for k, v in master_counts.items():
        L.append(f"- {k}: {v}")
    L.append("")

    L += [
        "## Vouchers per year",
        "",
        "| FY | Tally vouchers | cancelled (skipped) | no ledger lines | JEs created | "
        "skipped unbalanced | skipped error | JE lines | opening-adj lines |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for y in years:
        h = health_by_fy.get(y.fy)
        if not h:
            L.append(f"| {y.fy} | not imported | | | | | | | |")
            continue
        L.append(
            f"| {y.fy} | {h['tally_vouchers']} | {h['cancelled']} | {h['no_lines']} | {h['journals']} "
            f"| {h['skipped_unbalanced']} | {h['skipped_error']} | {h['journal_lines']} | {h['opening_lines']} |"
        )
    ex = [
        (h["fy"], e)
        for h in health_by_fy.values()
        for e in h.get("skipped_examples", [])
    ]
    if ex:
        L += ["", "Skipped voucher examples:", ""]
        L += [f"- {fy}: {e}" for fy, e in ex[:40]]
    L.append("")

    L += [
        "## Opening-balance difference posted each year (JE TLY-yyyy-OPEN)",
        "",
        "Plug = amount posted to 'Difference in Opening Balances' so that the year opens exactly as "
        "Tally does (debit +). 'Balance after' = the account's resulting balance, i.e. Tally's own "
        "opening imbalance for that year (Tally years do not carry forward).",
        "",
        "| FY | plug posted (Dr+) | Difference account balance after OPEN | opening stock (Dr) | closing stock (Dr) | CLSTK delta (Dr) | PCV profit (Cr+) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for y in years:
        h = health_by_fy.get(y.fy)
        if not h:
            continue
        L.append(
            f"| {y.fy} | {_fmt(h['opening_plug_debit'])} | {_fmt(h['diff_balance_after_open'])} | "
            f"{_fmt(h['stock_open_debit'])} | {_fmt(h['stock_close_debit'])} | {_fmt(h['clstk_amount'])} | "
            f"{_fmt(h['pcv_profit']) if h['pcv_profit'] is not None else '-'} |"
        )
    L.append("")

    L += [
        "## Reconciliation per year",
        "",
        "Method: ERP balance of each ledger/party at 31-Mar of the year (all GL up to that date, "
        "period-closing entry of that year excluded) vs (A) `mst_ledger.closingbalance` where not NULL "
        "and (B) `v_ledger_balance.closing` (= Tally opening + Σ posted vouchers) for every ledger. "
        "'Profit & Loss A/c' is excluded (Tally's closing includes the period profit) and compared in "
        "the profit table instead.",
        "",
    ]
    L += [
        "| FY | ledgers vs (A) | mismatching (A) | Σ abs diff (A) | ledgers vs (B) | mismatching (B) | Σ abs diff (B) | "
        "ERP-only non-zero keys (n / Σ abs) |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    recs = {}
    for y in years:
        if y.fy not in health_by_fy:
            continue
        r = reconcile_year(src, chart, y, health_by_fy.get(y.fy))
        recs[y.fy] = r
        L.append(
            f"| {y.fy} | {r['n_a']} | {r['n_a_bad']} | {_fmt(r['sum_abs_a'])} | {r['n_b']} | {r['n_b_bad']} "
            f"| {_fmt(r['sum_abs_b'])} | {r['extra_n']} / {_fmt(r['extra_sum'])} |"
        )
    L.append("")

    L += [
        "## Stock in Hand and net profit per year",
        "",
        "Stock: ERP 'Stock In Hand' at 31-Mar vs Tally Σ closingvalue (Dr). Profit is credit-positive: "
        "ERP = Σ(Cr-Dr) on Income/Expense accounts of the year (incl. Change in Stock, before PCV); "
        "Tally formula = Σ revenue-ledger movement + closing stock - opening stock; "
        "Tally own = P&L A/c closing - opening.",
        "",
        "| FY | Stock ERP | Stock Tally closing | diff | profit ERP | profit Tally formula | diff | profit Tally own | diff |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for fy, r in recs.items():
        to = r["tally_own"]
        L.append(
            f"| {fy} | {_fmt(r['stock_erp'])} | {_fmt(r['stock_tally'])} | {_fmt(r['stock_erp'] - r['stock_tally'])} "
            f"| {_fmt(r['erp_profit'])} | {_fmt(r['tally_formula'])} | {_fmt(r['erp_profit'] - r['tally_formula'])} "
            f"| {_fmt(to) if to is not None else '-'} | {_fmt(r['erp_profit'] - to) if to is not None else '-'} |"
        )
    L += [
        "",
        "_For FY 2026-27 (no CLSTK / PCV, year still open) the ERP stock value is the opening "
        "value only and profit excludes the closing-stock adjustment; the Tally columns include it._",
        "",
    ]

    L += ["## Top 15 mismatches per year", ""]
    for fy, r in recs.items():
        L += [f"### {fy}", ""]
        if not r["top"]:
            L += ["No ledger mismatches.", ""]
            continue
        L += [
            "| ledger | group | ERP | Tally closing (A) | diff (A) | Tally computed (B) | diff (B) |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
        for m in r["top"]:
            L.append(
                f"| {m['ledger']} | {m['group']} | {_fmt(m['erp'])} | "
                f"{_fmt(m['tally']) if m['tally'] is not None else '-'} | "
                f"{_fmt(m['d_a']) if m['d_a'] is not None else '-'} | {_fmt(m['view'])} | {_fmt(m['d_b'])} |"
            )
        if r["extra"]:
            L += ["", "ERP-only balances (not a Tally ledger of this year):", ""]
            for n, p, v in r["extra"]:
                L.append(f"- {n}{' / ' + p if p else ''}: {_fmt(v)}")
        L.append("")

    L += stock_section(chart, stock_stats)

    L += ["## Chart / master decisions", ""]
    n = chart_notes or {}
    L.append(
        f"- ledgers (distinct names, all years): {n.get('ledger_hist')}; Tally groups: {n.get('group_count')}; "
        f"ledger leaf accounts: {n.get('leaf_accounts')}"
    )
    L.append(f"- groups renamed with ' (Group)': {n.get('group_renamed')}")
    L.append(
        f"- ledgers renamed (whitespace / collisions): {len(n.get('ledger_renamed', {}))}"
    )
    for k, v in list(n.get("ledger_renamed", {}).items())[:15]:
        L.append(f"  - '{k}' -> '{v}'")
    L.append(
        f"- ledgers that are both a Customer and a Supplier: {len(n.get('customers_and_suppliers', []))}"
    )
    L.append(
        f"- ledgers whose ERP root type differs between years (single Account, latest year wins): {len(n.get('root_changes', []))}"
    )
    for k, v in list(n.get("root_changes", []))[:15]:
        L.append(f"  - {k}: {v}")
    L.append(f"- truncated values: {n.get('truncated')}")
    pg = n.get("party_gst") or {}
    L.append(
        f"- party GST: {pg.get('parties')} parties, {pg.get('with_gstin')} with GSTIN, "
        f"{pg.get('with_state')} with a state; **{len(pg.get('invalid_gstin', []))} GSTINs fail validation** "
        f"(kept, state left null, category Registered Regular)"
    )
    for nm, g in pg.get("invalid_gstin", [])[:20]:
        L.append(f"  - {nm}: {g}")
    L.append(
        "- company GSTIN: not available in the Tally extract (left null); state set to Madhya Pradesh (23)"
    )
    L.append(f"- GST-tagged Tax accounts: {n.get('gst_tagged')}")
    L.append(
        f"- Tax-type ledgers left untagged (Tally uses one combined input/output ledger, direction is ambiguous): {n.get('gst_untagged_tax')}"
    )
    s = sm_notes or {}
    L.append(
        f"- items: HSN set on {s.get('hsn_set')}, invalid/unsupported HSN skipped: {s.get('hsn_invalid')}, "
        f"nil/exempt items: {s.get('nil_exempt')}, inactive items (absent from 2026-27): {s.get('inactive')}, "
        f"code truncations: {s.get('truncated')}, code collisions: {s.get('collisions')}"
    )
    L.append("")

    L += ["## Not imported (counts in the Tally database)", ""]
    for k, v in not_imported.items():
        L.append(f"- {k}: {v if v is not None else 'not present / not extracted'}")
    L.append("")

    L += ["## Model changes needed", ""] + [f"- {m}" for m in MODEL_CHANGES] + [""]

    L += [
        "## verify_integrity",
        "",
        "```",
        (verify_output or "(not run)").strip(),
        "```",
        "",
    ]
    return "\n".join(L)
