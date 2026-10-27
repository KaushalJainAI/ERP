"""Idempotent GST seeding, called from ``seed_defaults``.

Everything here is *fill-if-missing*: a re-run never overwrites a value an operator
changed, and a fresh database and a legacy one converge on the same tagged accounts.
Companies and parties are never given an invented GSTIN or state.
"""

from decimal import Decimal

from django.db import transaction

from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    ItemTaxTemplate,
    ItemTaxTemplateRow,
    TaxTemplate,
    TaxTemplateRow,
)
from core.gst_states import STATES
from core.models import State
from gst.constants import (
    GSTComponent,
    GSTDirection,
    GSTSupplyType,
    GSTTreatment,
    TemplateType,
)

D = Decimal

#: UOM name -> GST Unique Quantity Code for the seeded UOMs.
UOM_UQC = {
    "Nos": "NOS",
    "Kg": "KGS",
    "Meter": "MTR",
    "Litre": "LTR",
    "Box": "BOX",
    "Set": "SET",
}

#: (template name, total rate %, legacy). CGST = SGST = rate / 2, IGST = rate.
#: GST 2.0 (effective 22 Sep 2025) slabs are 0 / 0.25 / 3 / 5 / 18 / 40; 12 and 28
#: are kept as legacy for invoices raised under the old structure.
ITEM_TAX_RATE_SLABS = (
    (D("0"), False),
    (D("0.25"), False),
    (D("3"), False),
    (D("5"), False),
    (D("18"), False),
    (D("40"), False),
    (D("12"), True),
    (D("28"), True),
)

#: Zero-rate treatment templates.
ITEM_TAX_TREATMENT_TEMPLATES = (
    ("Nil Rated", GSTTreatment.NIL_RATED),
    ("Exempted", GSTTreatment.EXEMPTED),
    ("Non-GST", GSTTreatment.NON_GST),
)


def slab_name(rate):
    return f"GST {rate.normalize():f}%"


def ensure_states():
    """Make sure every GST state / UT row exists. Returns the number created."""
    created = 0
    for code, name, abbr, is_ut, is_active in STATES:
        _, was_created = State.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "abbr": abbr,
                "is_union_territory": is_ut,
                "is_active": is_active,
            },
        )
        created += was_created
    return created


def ensure_uom_uqc():
    """Fill ``UOM.gst_uqc`` for the seeded UOMs where it is still blank."""
    from stock.models import UOM

    updated = 0
    for name, uqc in UOM_UQC.items():
        updated += UOM.objects.filter(name=name, gst_uqc="").update(gst_uqc=uqc)
    return updated


def _ensure_item_tax_template(name, treatment, rate, legacy):
    template, created = ItemTaxTemplate.objects.get_or_create(
        name=name,
        defaults={"gst_treatment": treatment, "is_legacy": legacy},
    )
    if created:
        half = rate / 2
        for component, value in (
            (GSTComponent.CGST, half),
            (GSTComponent.SGST, half),
            (GSTComponent.IGST, rate),
        ):
            ItemTaxTemplateRow.objects.create(
                template=template, gst_component=component, tax_rate=value
            )
    return created


def ensure_item_tax_templates():
    """Seed the global item tax templates. Returns the number of templates created."""
    created = 0
    for rate, legacy in ITEM_TAX_RATE_SLABS:
        created += _ensure_item_tax_template(
            slab_name(rate), GSTTreatment.TAXABLE, rate, legacy
        )
    for name, treatment in ITEM_TAX_TREATMENT_TEMPLATES:
        created += _ensure_item_tax_template(name, treatment, D("0"), False)
    return created


# --------------------------------------------------------------- per-company seeds

#: (account name, root type, parent group name, direction, component)
_GST_ACCOUNTS = (
    tuple(
        (
            f"Output {c}",
            AccountRootType.LIABILITY,
            "Duties and Taxes",
            GSTDirection.OUTPUT,
            c,
        )
        for c in ("CGST", "SGST", "IGST")
    )
    + tuple(
        (f"Input {c}", AccountRootType.ASSET, "Input Tax Credit", GSTDirection.INPUT, c)
        for c in ("CGST", "SGST", "IGST")
    )
    + tuple(
        (
            f"RCM Payable {c}",
            AccountRootType.LIABILITY,
            "Duties and Taxes",
            GSTDirection.REVERSE_CHARGE,
            c,
        )
        for c in ("CGST", "SGST", "IGST")
    )
)

ROUND_OFF_ACCOUNT_NAME = "Round Off"


def _root(company, root_type):
    return (
        Account.objects.filter(
            company=company, root_type=root_type, parent__isnull=True, is_group=True
        )
        .order_by("id")
        .first()
    )


def has_chart_of_accounts(company):
    return all(
        _root(company, rt) is not None
        for rt in (
            AccountRootType.ASSET,
            AccountRootType.LIABILITY,
            AccountRootType.EXPENSE,
        )
    )


def _ensure_group(company, name, root):
    group, _ = Account.objects.get_or_create(
        company=company,
        name=name,
        defaults={"parent": root, "root_type": root.root_type, "is_group": True},
    )
    return group


def ensure_gst_accounts(company):
    """Tag-or-create the nine GST accounts and the Round Off account of ``company``.

    Lookup is by ``(company, direction, component)`` *first*, so a legacy database
    whose bare ``CGST`` / ``SGST`` / ``IGST`` accounts were tagged Output by the
    migration keeps its names and never trips the unique tag constraint. An account
    that already has the target *name* but no tag is tagged (when it is a compatible
    leaf), otherwise it is skipped and reported in the returned ``warnings``.

    Returns ``{"accounts": {(direction, component): Account}, "round_off": Account|None,
    "created": int, "warnings": [str]}``; ``accounts`` is empty when the company has
    no chart of accounts.
    """
    result = {"accounts": {}, "round_off": None, "created": 0, "warnings": []}
    if not has_chart_of_accounts(company):
        return result

    groups = {}
    with transaction.atomic():
        for name, root_type, parent_name, direction, component in _GST_ACCOUNTS:
            tagged = Account.objects.filter(
                company=company, gst_direction=direction, gst_component=component
            ).first()
            if tagged is not None:
                result["accounts"][(direction, component)] = tagged
                continue

            same_name = Account.objects.filter(company=company, name=name).first()
            if same_name is not None:
                compatible = (
                    not same_name.is_group
                    and same_name.root_type == root_type
                    and same_name.account_type in (None, "", AccountType.TAX)
                )
                if not compatible:
                    result["warnings"].append(
                        f"{company.abbr}: account '{name}' exists but cannot be tagged "
                        f"as {direction} {component}; skipped."
                    )
                    continue
                same_name.account_type = AccountType.TAX
                same_name.gst_direction = direction
                same_name.gst_component = component
                same_name.save()
                result["accounts"][(direction, component)] = same_name
                continue

            if parent_name not in groups:
                groups[parent_name] = _ensure_group(
                    company, parent_name, _root(company, root_type)
                )
            parent = groups[parent_name]
            if parent.root_type != root_type or not parent.is_group:
                result["warnings"].append(
                    f"{company.abbr}: group '{parent_name}' is not a {root_type} group; "
                    f"'{name}' skipped."
                )
                continue
            acc = Account.objects.create(
                company=company,
                name=name,
                parent=parent,
                root_type=root_type,
                account_type=AccountType.TAX,
                is_group=False,
                gst_direction=direction,
                gst_component=component,
            )
            result["accounts"][(direction, component)] = acc
            result["created"] += 1

        # Round Off (an expense account, linked on the company).
        round_off = company.round_off_account
        if round_off is None:
            round_off = Account.objects.filter(
                company=company, account_type=AccountType.ROUND_OFF, is_group=False
            ).first()
        if round_off is None:
            existing = Account.objects.filter(
                company=company, name=ROUND_OFF_ACCOUNT_NAME
            ).first()
            if existing is not None:
                if existing.is_group or existing.root_type != AccountRootType.EXPENSE:
                    result["warnings"].append(
                        f"{company.abbr}: account '{ROUND_OFF_ACCOUNT_NAME}' exists but "
                        "is not an Expense leaf; round-off account not linked."
                    )
                else:
                    existing.account_type = AccountType.ROUND_OFF
                    existing.save()
                    round_off = existing
            else:
                round_off = Account.objects.create(
                    company=company,
                    name=ROUND_OFF_ACCOUNT_NAME,
                    parent=_root(company, AccountRootType.EXPENSE),
                    root_type=AccountRootType.EXPENSE,
                    account_type=AccountType.ROUND_OFF,
                    is_group=False,
                )
                result["created"] += 1
        result["round_off"] = round_off
    return result


#: (title, template type, supply type, reverse charge, ((direction, component, rate), ...))
_TAX_TEMPLATES = (
    (
        "Output GST In-state",
        TemplateType.SALES,
        GSTSupplyType.INTRA_STATE,
        False,
        (
            (GSTDirection.OUTPUT, GSTComponent.CGST, D("9")),
            (GSTDirection.OUTPUT, GSTComponent.SGST, D("9")),
        ),
    ),
    (
        "Output GST Out-state",
        TemplateType.SALES,
        GSTSupplyType.INTER_STATE,
        False,
        ((GSTDirection.OUTPUT, GSTComponent.IGST, D("18")),),
    ),
    (
        "Input GST In-state",
        TemplateType.PURCHASE,
        GSTSupplyType.INTRA_STATE,
        False,
        (
            (GSTDirection.INPUT, GSTComponent.CGST, D("9")),
            (GSTDirection.INPUT, GSTComponent.SGST, D("9")),
        ),
    ),
    (
        "Input GST Out-state",
        TemplateType.PURCHASE,
        GSTSupplyType.INTER_STATE,
        False,
        ((GSTDirection.INPUT, GSTComponent.IGST, D("18")),),
    ),
    (
        "Input GST In-state RCM",
        TemplateType.PURCHASE,
        GSTSupplyType.INTRA_STATE,
        True,
        (
            (GSTDirection.INPUT, GSTComponent.CGST, D("9")),
            (GSTDirection.INPUT, GSTComponent.SGST, D("9")),
        ),
    ),
    (
        "Input GST Out-state RCM",
        TemplateType.PURCHASE,
        GSTSupplyType.INTER_STATE,
        True,
        ((GSTDirection.INPUT, GSTComponent.IGST, D("18")),),
    ),
)


def ensure_tax_templates(company, accounts=None):
    """Seed the six standard GST tax templates of ``company`` (when missing).

    ``accounts`` is the ``(direction, component) -> Account`` map from
    :func:`ensure_gst_accounts` (looked up when omitted). A template is created only
    when its ``(company, title)`` is absent -- operator edits to existing templates
    and their rows are never touched. A default is only claimed when no other
    template already holds that default slot. Returns the number created.
    """
    if accounts is None:
        accounts = {
            (a.gst_direction, a.gst_component): a
            for a in Account.objects.filter(
                company=company, gst_direction__isnull=False
            )
        }
    created = 0
    with transaction.atomic():
        for title, ttype, supply, rcm, rows in _TAX_TEMPLATES:
            if TaxTemplate.objects.filter(company=company, title=title).exists():
                continue
            if any((d, c) not in accounts for d, c, _ in rows):
                continue  # accounts not available (skipped with a warning earlier)
            slot_taken = TaxTemplate.objects.filter(
                company=company,
                template_type=ttype,
                gst_supply_type=supply,
                is_reverse_charge=rcm,
                is_default=True,
            ).exists()
            template = TaxTemplate.objects.create(
                company=company,
                title=title,
                template_type=ttype,
                gst_supply_type=supply,
                is_reverse_charge=rcm,
                is_default=not slot_taken,
            )
            for idx, (direction, component, rate) in enumerate(rows, start=1):
                TaxTemplateRow.objects.create(
                    template=template,
                    idx=idx,
                    account=accounts[(direction, component)],
                    description=accounts[(direction, component)].name,
                    rate=rate,
                )
            created += 1
    return created


def seed_company_gst(company):
    """Accounts + round-off link + tax templates for one company.

    On the first run for the seeded ``ACME`` company (the round-off account is not
    linked yet) rounding is switched on, as the design asks of the seeds; later runs
    leave an operator's choice alone. Returns a summary dict (``skipped`` is True for
    a company without a chart of accounts).
    """
    summary = {
        "company": company.abbr,
        "skipped": False,
        "accounts_created": 0,
        "templates_created": 0,
        "warnings": [],
    }
    first_time = company.round_off_account_id is None
    acc = ensure_gst_accounts(company)
    if not acc["accounts"] and acc["round_off"] is None:
        summary["skipped"] = True
        return summary
    summary["accounts_created"] = acc["created"]
    summary["warnings"] = acc["warnings"]

    update_fields = []
    if company.round_off_account_id is None and acc["round_off"] is not None:
        company.round_off_account = acc["round_off"]
        update_fields.append("round_off_account")
    if first_time and company.abbr == "ACME" and not company.enable_rounded_total:
        company.enable_rounded_total = True
        update_fields.append("enable_rounded_total")
    if update_fields:
        company.save(update_fields=update_fields)

    summary["templates_created"] = ensure_tax_templates(company, acc["accounts"])
    return summary


def seed_gst():
    """Run every GST seed (states, UQCs, item tax templates, per-company masters)."""
    from core.models import Company

    summary = {
        "states_created": ensure_states(),
        "uqc_filled": ensure_uom_uqc(),
        "item_tax_templates_created": ensure_item_tax_templates(),
        "companies": [],
    }
    for company in Company.objects.order_by("id"):
        summary["companies"].append(seed_company_gst(company))
    return summary
