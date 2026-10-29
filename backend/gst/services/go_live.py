"""GST go-live setup: separate Input / Output GST accounts, rate templates, GSTIN checks.

Tally kept one *combined* ledger per GST head (``C Gst``, ``S Gst``, ``I Gst``,
``Cess Gst`` are both input and output), which cannot drive GSTR-1 / GSTR-3B
(``docs/tally_data_gaps.md`` G3). Before the first invoice is raised in the ERP the
company needs distinct, **tagged** Input and Output accounts and tax templates that
post to them. :func:`setup_company` is the idempotent routine behind
``manage.py setup_gst_accounts``.

Rules this module keeps:

* It is **fill-if-missing**. A re-run creates nothing that exists; operator edits to
  templates are never overwritten.
* The legacy combined ledgers (and anything else already in the chart) are never
  deleted, renamed, re-tagged or re-pointed -- they carry the Tally history. They are
  merely not used by the templates created here.
* Accounts are created under the company's ``Duties & Taxes`` group (the Tally group
  name; created if the chart has none).
* The only thing that is *changed* is which template is the default of a
  (template type, supply type) slot, and only when asked for (``default_rate``).
"""

from decimal import Decimal

from django.db import transaction
from django.utils.translation import gettext as _

from accounts.models import (
    Account,
    AccountRootType,
    AccountType,
    TaxTemplate,
    TaxTemplateRow,
)
from gst.constants import GSTComponent, GSTDirection, GSTSupplyType, TemplateType
from gst.validators import check_gstin, normalise_gstin

D = Decimal

#: Slab rates that get Sales and Purchase templates: spices 5%, packing / services 18%,
#: 12% for the pre-GST-2.0 invoices still being booked.
DEFAULT_RATES = (D("5"), D("12"), D("18"))
TAX_GROUP_NAME = "Duties & Taxes"
#: Rate whose templates become the default of each slot.
DEFAULT_RATE = D("5")


def _fmt_rate(rate) -> str:
    return f"{Decimal(rate).normalize():f}"


def template_title(template_type, supply_type, rate) -> str:
    """e.g. ``Output GST 5% In-state`` / ``Input GST 18% Out-state``."""
    prefix = "Output" if template_type == TemplateType.SALES else "Input"
    suffix = "In-state" if supply_type == GSTSupplyType.INTRA_STATE else "Out-state"
    return f"{prefix} GST {_fmt_rate(rate)}% {suffix}"


# ------------------------------------------------------------------ accounts


def _root(company, root_type):
    return (
        Account.objects.filter(
            company=company, root_type=root_type, parent__isnull=True, is_group=True
        )
        .order_by("id")
        .first()
    )


def find_tax_group(company, name=TAX_GROUP_NAME):
    """The company's existing duties-and-taxes group (``&`` / ``and`` and case are
    ignored), or ``None``."""
    groups = list(Account.objects.filter(company=company, is_group=True).order_by("id"))
    for group in groups:  # the exact name wins
        if group.name == name:
            return group
    wanted = name.lower().replace("&", "and")
    for group in groups:
        if group.name.lower().replace("&", "and") == wanted:
            return group
    return None


def ensure_accounts(company, group_name=TAX_GROUP_NAME, with_cess=False):
    """Make sure tagged Input and Output accounts exist for CGST / SGST / IGST
    (+ CESS when ``with_cess``).

    Lookup is by ``(company, direction, component)`` first (an account already tagged
    is kept whatever it is called), then by the target name (an untagged leaf of that
    name is tagged), else a new account is created under the tax group.

    Returns ``{"accounts": {(direction, component): Account}, "created": [names],
    "tagged": [names], "warnings": [str]}``.
    """
    result = {"accounts": {}, "created": [], "tagged": [], "warnings": []}
    liabilities = _root(company, AccountRootType.LIABILITY)
    if liabilities is None:
        result["warnings"].append(
            _(
                "%(company)s has no chart of accounts (no Liability root); nothing set up."
            )
            % {"company": company.name}
        )
        return result

    components = [GSTComponent.CGST, GSTComponent.SGST, GSTComponent.IGST]
    if with_cess:
        components.append(GSTComponent.CESS)

    with transaction.atomic():
        group = find_tax_group(company, group_name)
        for direction in (GSTDirection.OUTPUT, GSTDirection.INPUT):
            for component in components:
                key = (direction.value, component.value)
                name = f"{direction.value} {component.value}"
                tagged = Account.objects.filter(
                    company=company,
                    gst_direction=direction,
                    gst_component=component,
                ).first()
                if tagged is not None:
                    result["accounts"][key] = tagged
                    continue
                same_name = Account.objects.filter(company=company, name=name).first()
                if same_name is not None:
                    compatible = not same_name.is_group and same_name.account_type in (
                        None,
                        "",
                        AccountType.TAX,
                    )
                    if not compatible:
                        result["warnings"].append(
                            _(
                                "Account '%(name)s' exists but cannot be tagged as "
                                "%(direction)s %(component)s; skipped."
                            )
                            % {
                                "name": name,
                                "direction": direction.value,
                                "component": component.value,
                            }
                        )
                        continue
                    same_name.account_type = AccountType.TAX
                    same_name.gst_direction = direction
                    same_name.gst_component = component
                    same_name.save()
                    result["accounts"][key] = same_name
                    result["tagged"].append(name)
                    continue
                if group is None:
                    group = Account.objects.create(
                        company=company,
                        name=group_name,
                        parent=liabilities,
                        root_type=AccountRootType.LIABILITY,
                        is_group=True,
                    )
                account = Account.objects.create(
                    company=company,
                    name=name,
                    parent=group,
                    root_type=group.root_type,
                    account_type=AccountType.TAX,
                    is_group=False,
                    gst_direction=direction,
                    gst_component=component,
                )
                result["accounts"][key] = account
                result["created"].append(name)
    return result


def legacy_gst_accounts(company):
    """Untagged Tax accounts of the company: the Tally-imported combined ledgers
    (``C Gst`` ...). Reported, never touched."""
    return list(
        Account.objects.filter(
            company=company,
            account_type=AccountType.TAX,
            is_group=False,
            gst_direction__isnull=True,
        )
        .order_by("name")
        .values_list("name", flat=True)
    )


# ----------------------------------------------------------------- templates


def _desired_rows(template_type, supply_type, rate, accounts):
    """``[(account, rate)]`` a template of this kind must carry, or ``None`` when an
    account is missing."""
    direction = (
        GSTDirection.OUTPUT
        if template_type == TemplateType.SALES
        else GSTDirection.INPUT
    ).value
    if supply_type == GSTSupplyType.INTRA_STATE:
        parts = (("CGST", rate / 2), ("SGST", rate / 2))
    else:
        parts = (("IGST", rate),)
    rows = []
    for component, part_rate in parts:
        account = accounts.get((direction, component))
        if account is None:
            return None
        rows.append((account, part_rate))
    return rows


def _find_equivalent(company, template_type, supply_type, rows):
    """An existing normal (non-RCM) template with exactly these account/rate rows."""
    want = {(a.pk, r) for a, r in rows}
    candidates = TaxTemplate.objects.filter(
        company=company,
        template_type=template_type,
        gst_supply_type=supply_type,
        is_reverse_charge=False,
    ).prefetch_related("rows")
    for template in candidates.order_by("pk"):
        have = {(r.account_id, r.rate) for r in template.rows.all()}
        if have == want:
            return template
    return None


def ensure_rate_templates(
    company, accounts, rates=DEFAULT_RATES, default_rate=DEFAULT_RATE
):
    """Sales and Purchase templates, intra-state (CGST + SGST) and inter-state (IGST),
    for every rate, pointing at the tagged accounts.

    A template is created only when no existing one already has the same rows (so the
    seeded ``Output GST In-state`` = 18% is reused, not duplicated) and its title is
    free. ``default_rate`` (``None`` = leave defaults alone) makes that rate's template
    the default of each (type, supply) slot; with ``None`` a default is claimed only
    for an empty slot. Returns ``{"templates": {title: TaxTemplate}, "created": [titles],
    "defaults": [titles], "warnings": [str]}``.
    """
    result = {"templates": {}, "created": [], "defaults": [], "warnings": []}
    by_slot = {}
    with transaction.atomic():
        for template_type in (TemplateType.SALES, TemplateType.PURCHASE):
            for supply_type in (GSTSupplyType.INTRA_STATE, GSTSupplyType.INTER_STATE):
                for rate in rates:
                    rows = _desired_rows(template_type, supply_type, rate, accounts)
                    if rows is None:
                        result["warnings"].append(
                            _("Accounts for %(title)s are missing; template skipped.")
                            % {
                                "title": template_title(
                                    template_type, supply_type, rate
                                )
                            }
                        )
                        continue
                    template = _find_equivalent(
                        company, template_type, supply_type, rows
                    )
                    title = template_title(template_type, supply_type, rate)
                    if template is None:
                        if TaxTemplate.objects.filter(
                            company=company, title=title
                        ).exists():
                            result["warnings"].append(
                                _(
                                    "Template '%(title)s' exists with different rows; "
                                    "left unchanged and not created again."
                                )
                                % {"title": title}
                            )
                            continue
                        template = TaxTemplate.objects.create(
                            company=company,
                            title=title,
                            template_type=template_type,
                            gst_supply_type=supply_type,
                            is_reverse_charge=False,
                        )
                        for idx, (account, part_rate) in enumerate(rows, start=1):
                            TaxTemplateRow.objects.create(
                                template=template,
                                idx=idx,
                                account=account,
                                description=account.name,
                                rate=part_rate,
                            )
                        result["created"].append(title)
                    result["templates"][title] = template
                    by_slot.setdefault((template_type, supply_type), {})[
                        rate
                    ] = template

        # Defaults, per slot.
        for (template_type, supply_type), per_rate in by_slot.items():
            current = TaxTemplate.objects.filter(
                company=company,
                template_type=template_type,
                gst_supply_type=supply_type,
                is_reverse_charge=False,
                is_default=True,
            ).first()
            chosen = per_rate.get(default_rate) if default_rate is not None else None
            if chosen is None:
                if current is not None or not per_rate:
                    continue
                chosen = per_rate[sorted(per_rate)[0]]
            if current is not None and current.pk == chosen.pk:
                continue
            if current is not None:
                current.is_default = False
                current.save(update_fields=["is_default", "modified_at"])
            if chosen.disabled:
                chosen.disabled = False
            chosen.is_default = True
            chosen.save(update_fields=["is_default", "disabled", "modified_at"])
            result["defaults"].append(chosen.title)
    return result


def legacy_templates(company):
    """Titles of the company's templates that still post to an untagged Tax account
    (a Tally combined ledger). Reported so the operator can disable them."""
    titles = set(
        TaxTemplateRow.objects.filter(
            template__company=company,
            account__account_type=AccountType.TAX,
            account__gst_direction__isnull=True,
        ).values_list("template__title", flat=True)
    )
    return sorted(titles)


# -------------------------------------------------------------- GSTIN / state


def company_gst_findings(company):
    """Warnings about the company's GST identity, in operator language.

    The GST reports (GSTR-1 / 3B) refuse a company without a valid GSTIN and a
    matching state, and an invoice cannot decide intra- vs inter-state without them.
    Read only. Returns a list of strings.
    """
    out = []
    gstin = normalise_gstin(company.gstin)
    if not gstin:
        out.append(
            _(
                "Company '%(company)s' has no GSTIN: GST invoices and the GSTR-1 / "
                "GSTR-3B reports need it. Set GSTIN, State (Madhya Pradesh = 23) and "
                "GST category 'Registered Regular' on the company."
            )
            % {"company": company.name}
        )
        return out
    check = check_gstin(gstin)
    if not check.valid:
        out.append(
            _("Company '%(company)s': GSTIN %(gstin)s is invalid (%(reason)s).")
            % {"company": company.name, "gstin": gstin, "reason": check.reason}
        )
    if not company.state_id:
        out.append(
            _("Company '%(company)s' has a GSTIN but no State set.")
            % {"company": company.name}
        )
    elif gstin[:2] != company.state_id:
        out.append(
            _(
                "Company '%(company)s': GSTIN prefix %(prefix)s does not match "
                "State %(state)s."
            )
            % {
                "company": company.name,
                "prefix": gstin[:2],
                "state": company.state_id,
            }
        )
    if company.gst_category != "Registered Regular":
        out.append(
            _("Company '%(company)s' has a GSTIN but GST category is '%(category)s'.")
            % {"company": company.name, "category": company.gst_category}
        )
    return out


def company_account_findings(company):
    """Warnings when a GST-registered company lacks the tagged Input / Output
    accounts or default templates (only companies with a GSTIN are checked)."""
    if not normalise_gstin(company.gstin):
        return []
    out = []
    missing = [
        f"{d} {c}"
        for d in (GSTDirection.OUTPUT.value, GSTDirection.INPUT.value)
        for c in ("CGST", "SGST", "IGST")
        if not Account.objects.filter(
            company=company, gst_direction=d, gst_component=c
        ).exists()
    ]
    if missing:
        out.append(
            _(
                "Company '%(company)s' has no tagged GST account for: %(accounts)s. "
                'Run: manage.py setup_gst_accounts --company "%(company)s".'
            )
            % {"company": company.name, "accounts": ", ".join(missing)}
        )
    return out


# --------------------------------------------------------------------- driver


def setup_company(
    company,
    group_name=TAX_GROUP_NAME,
    rates=DEFAULT_RATES,
    default_rate=DEFAULT_RATE,
    with_cess=False,
):
    """Run the whole go-live setup for one company (idempotent). Returns a summary."""
    with transaction.atomic():
        acc = ensure_accounts(company, group_name, with_cess)
        summary = {
            "company": company.name,
            "accounts_created": acc["created"],
            "accounts_tagged": acc["tagged"],
            "templates_created": [],
            "defaults_set": [],
            "legacy_accounts": legacy_gst_accounts(company),
            "legacy_templates": [],
            "warnings": list(acc["warnings"]),
        }
        if acc["accounts"]:
            tpl = ensure_rate_templates(company, acc["accounts"], rates, default_rate)
            summary["templates_created"] = tpl["created"]
            summary["defaults_set"] = tpl["defaults"]
            summary["warnings"] += tpl["warnings"]
        summary["legacy_templates"] = legacy_templates(company)
        summary["warnings"] += company_gst_findings(company)
    return summary
