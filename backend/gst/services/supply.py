"""GST supply rules and the tax-calculation orchestration for taxable documents.

* ``supply_context`` -- supply kind (intra / inter), place of supply, which GST
  components may be charged (docs/gst_design.md section 2.2);
* ``compute`` -- snapshots, template expansion, line resolution, structural
  validation, the pure engine (``accounts.services.taxes``) and the submit gate.
  It works on in-memory objects and **never writes**, so the dry-run endpoint and
  the real save share exactly one code path;
* ``persist`` -- writes a ``compute`` outcome for a saved draft document.

Decisions on legacy / ORM-imported parties (section 1.3 of the design says they
bypass the serializer): a party stored as Unregistered / Consumer but carrying a
GSTIN that passes the checksum is treated as **Registered Regular** (it can only
have come from an ORM writer, and a GSTIN holder must be billed as registered);
a party whose category requires no GSTIN but stores an invalid one is treated as
unregistered and the GSTIN is ignored. The party's location is ``party.state``,
else the GSTIN prefix, else (sales: place of supply / purchase: supplier location)
the company's state.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from accounts.models import AccountType, TaxTemplate
from accounts.services import taxes as engine
from core.gst_states import STATE_CODES
from gst.constants import (
    AddDeduct,
    ChargeType,
    GSTDirection,
    GSTSupplyType,
    GSTTreatment,
    HSN_LENGTHS,
    TaxCategory,
    TemplateType,
)
from gst.validators import (
    CATEGORIES_FORBIDDING_GSTIN,
    CATEGORIES_REQUIRING_GSTIN,
    CONSUMER,
    OVERSEAS,
    REGISTERED_COMPOSITION,
    REGISTERED_REGULAR,
    SEZ,
    UNREGISTERED,
    check_gstin,
    normalise_gstin,
)

INTRA = "INTRA"
INTER = "INTER"
SALES = TemplateType.SALES.value
PURCHASE = TemplateType.PURCHASE.value
OTHER_COUNTRIES = "96"

INTRA_COMPONENTS = ("CGST", "SGST", "CESS")
INTER_COMPONENTS = ("IGST", "CESS")
ALL_COMPONENTS = ("CGST", "SGST", "IGST", "CESS")

#: Longest document number GST allows on a sales invoice.
MAX_SALES_INVOICE_NUMBER_LENGTH = 16


# ------------------------------------------------------------------- parties


def effective_party(party):
    """``(gst_category, gstin)`` to use for a party (see the module docstring)."""
    category = getattr(party, "gst_category", None) or UNREGISTERED
    gstin = normalise_gstin(getattr(party, "gstin", None)) or None
    if category in (UNREGISTERED, CONSUMER) and gstin and check_gstin(gstin).valid:
        category = REGISTERED_REGULAR
    if category in CATEGORIES_FORBIDDING_GSTIN:
        gstin = None
    return category, gstin


def party_state_code(party, gstin=None) -> Optional[str]:
    """The party's location: its state, else its GSTIN prefix, else ``None``."""
    state_id = getattr(party, "state_id", None)
    if state_id:
        return state_id
    if gstin and gstin[:2] in STATE_CODES:
        return gstin[:2]
    return None


def company_is_registered(company) -> bool:
    """A company with a GSTIN that is not a composition taxpayer can charge GST."""
    return bool(normalise_gstin(company.gstin)) and (
        company.gst_category != REGISTERED_COMPOSITION
    )


# ------------------------------------------------------------ supply context


@dataclass
class SupplyContext:
    direction: str
    kind: Optional[str]
    place_of_supply: Optional[str]
    gst_category: str
    party_gstin: Optional[str]
    company_gstin: Optional[str]
    allowed_components: tuple = ()
    #: Why no GST component is allowed ("" when some are).
    reason: str = ""

    @property
    def gst_rows_allowed(self) -> bool:
        return bool(self.allowed_components)


def supply_context(
    company,
    party,
    direction,
    place_of_supply=None,
    is_export_with_gst=False,
    is_reverse_charge=False,
) -> SupplyContext:
    """Supply kind, place of supply and the GST components that may be charged.

    ``place_of_supply`` is a ``State``, a state code or ``None`` (default it).
    """
    category, party_gstin = effective_party(party)
    company_gstin = normalise_gstin(company.gstin) or None
    pos = getattr(place_of_supply, "pk", place_of_supply) or None
    party_loc = party_state_code(party, party_gstin)

    # Place of supply default.
    if not pos:
        if direction == SALES:
            pos = (
                OTHER_COUNTRIES
                if category == OVERSEAS
                else (party_loc or company.state_id)
            )
        else:
            pos = company.state_id

    # Supply kind.
    if direction == SALES:
        supplier_loc = company.state_id
    else:
        supplier_loc = party_loc or company.state_id
    if category in (SEZ, OVERSEAS):
        kind = INTER
    elif not pos or not supplier_loc:
        kind = None
    else:
        kind = INTRA if supplier_loc == pos else INTER

    by_kind = {
        INTRA: INTRA_COMPONENTS,
        INTER: INTER_COMPONENTS,
        None: ALL_COMPONENTS,
    }[kind]

    allowed: tuple = ()
    reason = ""
    if not company_is_registered(company):
        reason = _(
            "The company is not a regular GST registrant, so GST cannot be charged "
            "or claimed on this document."
        )
    elif direction == SALES:
        if category in (SEZ, OVERSEAS):
            if is_export_with_gst:
                allowed = INTER_COMPONENTS
            else:
                reason = _(
                    "A supply to an SEZ or overseas customer without payment of "
                    "tax (LUT / bond) carries no GST."
                )
        else:
            allowed = by_kind
    else:  # purchase
        if category == REGISTERED_COMPOSITION:
            reason = _("Purchases from a composition supplier carry no GST.")
        elif category in (UNREGISTERED, CONSUMER, OVERSEAS):
            if is_reverse_charge:
                allowed = INTER_COMPONENTS if category == OVERSEAS else by_kind
            else:
                reason = _(
                    "GST on a purchase from an unregistered or overseas supplier "
                    "applies only under reverse charge."
                )
        elif category == SEZ:
            allowed = INTER_COMPONENTS
        else:
            allowed = by_kind
    return SupplyContext(
        direction=direction,
        kind=kind,
        place_of_supply=pos,
        gst_category=category,
        party_gstin=party_gstin,
        company_gstin=company_gstin,
        allowed_components=allowed,
        reason=str(reason),
    )


def default_template(company, direction, kind, is_reverse_charge=False):
    """The company's default tax template for a supply kind, or ``None``."""
    if kind is None:
        return None
    supply_type = (
        GSTSupplyType.INTRA_STATE.value
        if kind == INTRA
        else GSTSupplyType.INTER_STATE.value
    )
    return (
        TaxTemplate.objects.filter(
            company=company,
            template_type=direction,
            gst_supply_type=supply_type,
            is_reverse_charge=bool(is_reverse_charge),
            is_default=True,
            disabled=False,
        )
        .order_by("pk")
        .first()
    )


# ------------------------------------------------------------------- lines


def resolve_line(item) -> dict:
    """GST snapshot of an Item for a document line.

    Priority (docs/gst_design.md section 1.5): ``is_non_gst`` -> Non-GST, rates 0;
    ``is_nil_exempt`` -> the item template's Nil-Rated / Exempted treatment, else
    Nil-Rated; otherwise the item template's treatment and rates (a template whose
    treatment is not Taxable also zero-rates the item), else Taxable at the tax
    row's own rate (empty ``item_tax_rate``).
    """
    template = item.item_tax_template
    rows: dict = {}
    if template is not None and template.gst_treatment == GSTTreatment.TAXABLE:
        rows = {r.gst_component: f"{r.tax_rate:.3f}" for r in template.rows.all()}
    if item.is_non_gst:
        treatment, rates = GSTTreatment.NON_GST, {}
    elif item.is_nil_exempt:
        treatment = (
            template.gst_treatment
            if template is not None
            and template.gst_treatment
            in (GSTTreatment.NIL_RATED, GSTTreatment.EXEMPTED)
            else GSTTreatment.NIL_RATED
        )
        rates = {}
    elif template is not None and template.gst_treatment != GSTTreatment.TAXABLE:
        treatment, rates = template.gst_treatment, {}
    else:
        treatment, rates = GSTTreatment.TAXABLE, rows
    return {
        "gst_hsn_code": item.gst_hsn_code or "",
        "gst_treatment": str(treatment),
        "item_tax_template": template,
        "item_tax_rate": rates,
    }


# ----------------------------------------------------------------- template


def template_rows(doc, template):
    """Unsaved tax rows copied from ``template`` (validated against ``doc``)."""
    problems = {}
    if template.company_id != doc.company_id:
        problems["taxes_and_charges"] = _(
            "The tax template belongs to a different company."
        )
    elif template.template_type != doc.GST_DIRECTION:
        problems["taxes_and_charges"] = _(
            "A %(type)s document needs a %(type)s tax template."
        ) % {"type": doc.GST_DIRECTION}
    elif template.disabled:
        problems["taxes_and_charges"] = _("The tax template is disabled.")
    if problems:
        raise ValidationError(problems)
    model = doc.tax_model()
    out = []
    for position, row in enumerate(
        template.rows.select_related("account").order_by("idx"), start=1
    ):
        out.append(
            model(
                idx=position,
                charge_type=row.charge_type,
                account=row.account,
                description=row.description or row.account.name,
                rate=row.rate,
                add_deduct_tax=row.add_deduct_tax,
                category=row.category,
            )
        )
    return out


# ---------------------------------------------------------------- validation


def _row_label(position):
    return _("Row %(n)s") % {"n": position}


def validate_structure(doc, ctx, rows):
    """Draft-time checks: tax rows must be consistent with the document.

    Raises a field-keyed ``ValidationError``. Run on every calculation so an edit
    that leaves the rows inconsistent (party or place of supply changed) is refused
    with a hint to re-apply the template, never silently rewritten.
    """
    errors = {}
    if doc.GST_DIRECTION == SALES and doc.is_reverse_charge:
        errors["is_reverse_charge"] = _(
            "Reverse charge is not supported on sales documents."
        )
    if doc.GST_DIRECTION == PURCHASE and doc.is_export_with_gst:
        errors["is_export_with_gst"] = _(
            "'Export with GST' applies to sales documents only."
        )
    if (
        doc.GST_DIRECTION == SALES
        and doc.is_export_with_gst
        and ctx.gst_category not in (SEZ, OVERSEAS)
    ):
        errors["is_export_with_gst"] = _(
            "'Export with GST' applies only to SEZ and overseas customers."
        )

    want_direction = (
        GSTDirection.OUTPUT.value
        if doc.GST_DIRECTION == SALES
        else GSTDirection.INPUT.value
    )
    row_errors = []
    seen_accounts = set()
    components = set()
    for position, row in enumerate(rows, start=1):
        account = row.account
        problems = []
        if account.company_id != doc.company_id:
            problems.append(_("Account must belong to the document's company."))
        if account.is_group:
            problems.append(_("Account must not be a group account."))
        if account.account_type != AccountType.TAX:
            problems.append(_("Account must be of type 'Tax'."))
        if row.charge_type != ChargeType.ON_NET_TOTAL:
            problems.append(_("Only the 'On Net Total' charge type is supported."))
        if row.category != TaxCategory.TOTAL:
            problems.append(_("Only the 'Total' tax category is supported."))
        if row.add_deduct_tax not in (AddDeduct.ADD, AddDeduct.DEDUCT):
            problems.append(_("Add / Deduct must be 'Add' or 'Deduct'."))
        rate = Decimal(row.rate)
        if rate < 0 or rate > 100:
            problems.append(_("Rate must be between 0 and 100."))
        if account.pk in seen_accounts:
            problems.append(_("Account is used twice in the tax table."))
        seen_accounts.add(account.pk)
        if account.gst_direction:
            if account.gst_direction != want_direction:
                problems.append(
                    _("A %(type)s document needs %(direction)s GST accounts.")
                    % {"type": doc.GST_DIRECTION, "direction": want_direction}
                )
            comp = account.gst_component
            components.add(comp)
            if row.add_deduct_tax == AddDeduct.DEDUCT:
                problems.append(_("A GST row cannot be a deduction."))
            if not ctx.gst_rows_allowed:
                problems.append(ctx.reason)
            elif ctx.kind and comp not in ctx.allowed_components:
                problems.append(
                    _(
                        "%(comp)s is not applicable to this %(kind)s supply "
                        "(place of supply %(pos)s). Re-apply the tax template."
                    )
                    % {
                        "comp": comp,
                        "kind": (
                            _("intra-state") if ctx.kind == INTRA else _("inter-state")
                        ),
                        "pos": ctx.place_of_supply,
                    }
                )
        row_errors.append(
            {_row_label(position): [str(p) for p in problems]} if problems else None
        )
    if "IGST" in components and components & {"CGST", "SGST"}:
        row_errors.append(
            {_("Tax table"): [str(_("IGST cannot be combined with CGST / SGST."))]}
        )
    flat = [e for e in row_errors if e]
    if flat:
        errors["taxes"] = [
            f"{label}: {' '.join(msgs)}" for e in flat for label, msgs in e.items()
        ]
    if errors:
        raise ValidationError(errors)


def validate_for_submit(doc, lines, ctx):
    """Submit gate (docs/gst_design.md section 2.3); only for GST-registered companies."""
    if not ctx.company_gstin:
        return
    errors = []
    company = doc.company
    if not check_gstin(ctx.company_gstin).valid:
        errors.append(_("The company's GSTIN is invalid."))
    if not ctx.place_of_supply:
        errors.append(_("Place of supply is required."))
    min_digits = company.hsn_min_digits
    for position, line in enumerate(lines, start=1):
        if line.gst_treatment == GSTTreatment.NON_GST:
            continue
        code = line.gst_hsn_code or ""
        label = _("Item %(code)s (line %(n)s)") % {
            "code": line.item.code,
            "n": position,
        }
        if not code:
            errors.append(_("%(label)s needs an HSN/SAC code.") % {"label": label})
        elif not (code.isascii() and code.isdigit()) or len(code) not in HSN_LENGTHS:
            errors.append(
                _("%(label)s: HSN/SAC code must be 4, 6 or 8 digits.")
                % {"label": label}
            )
        elif len(code) < min_digits:
            errors.append(
                _("%(label)s: HSN/SAC code needs at least %(n)s digits.")
                % {"label": label, "n": min_digits}
            )
    if ctx.gst_category in CATEGORIES_REQUIRING_GSTIN:
        party = doc.party
        gstin_check = check_gstin(ctx.party_gstin)
        if not gstin_check.valid:
            errors.append(
                _("%(party)s: %(reason)s")
                % {"party": party.name, "reason": gstin_check.reason}
            )
        elif party.state_id and gstin_check.state_code != party.state_id:
            errors.append(
                _("%(party)s: GSTIN prefix %(code)s does not match the party's state.")
                % {"party": party.name, "code": gstin_check.state_code}
            )
    if (
        doc.GST_DIRECTION == SALES
        and doc._meta.object_name == "SalesInvoice"
        and len(doc.name or "") > MAX_SALES_INVOICE_NUMBER_LENGTH
    ):
        errors.append(
            _(
                "Invoice number '%(name)s' is longer than %(max)s characters, "
                "which GST does not allow."
            )
            % {"name": doc.name, "max": MAX_SALES_INVOICE_NUMBER_LENGTH}
        )
    if errors:
        raise ValidationError([str(e) for e in errors])


# -------------------------------------------------------------------- compute


@dataclass
class Outcome:
    """Result of :func:`compute` (everything is also written to the objects)."""

    ctx: SupplyContext
    lines: list
    rows: list
    result: engine.Result
    #: True when ``rows`` are new unsaved objects (template expanded / auto-selected).
    rows_replaced: bool = False


def compute(
    doc,
    lines: List,
    rows: List,
    *,
    resolve_lines: bool = False,
    expand_template: bool = False,
    auto_select: bool = False,
    for_submit: bool = False,
) -> Outcome:
    """Calculate taxes and totals for ``doc`` in memory (see module docstring).

    ``doc``, ``lines`` (document lines with ``amount``/``item``) and ``rows`` (tax
    rows with ``account``) may be saved or unsaved. All derived fields are written
    onto those objects; nothing is saved.
    """
    company = doc.company
    ctx = supply_context(
        company,
        doc.party,
        doc.GST_DIRECTION,
        doc.place_of_supply_id,
        doc.is_export_with_gst,
        doc.is_reverse_charge,
    )
    doc.gst_category = ctx.gst_category
    doc.party_gstin = ctx.party_gstin
    doc.company_gstin = ctx.company_gstin
    doc.place_of_supply_id = ctx.place_of_supply

    # Template: expand the chosen one, or auto-select the company default.
    rows_replaced = False
    template = None
    if expand_template and doc.taxes_and_charges_id:
        template = doc.taxes_and_charges
    elif (
        auto_select
        and not doc.taxes_and_charges_id
        and ctx.company_gstin
        and ctx.gst_rows_allowed
    ):
        template = default_template(
            company, doc.GST_DIRECTION, ctx.kind, doc.is_reverse_charge
        )
        if template is not None:
            doc.taxes_and_charges = template
    if template is not None:
        rows = template_rows(doc, template)
        rows_replaced = True
    for position, row in enumerate(rows, start=1):
        row.idx = position
        if not row.description:
            row.description = row.account.name

    # Line snapshots.
    for line in lines:
        if resolve_lines:
            for name, value in resolve_line(line.item).items():
                setattr(line, name, value)
        elif for_submit and not line.gst_hsn_code:
            line.gst_hsn_code = line.item.gst_hsn_code or ""

    validate_structure(doc, ctx, rows)

    # A return (credit / debit note) has negative lines. It is calculated as its
    # positive twin and negated, so the tax is the exact mirror of the original's.
    is_return = bool(getattr(doc, "is_return", False))
    if is_return and any(Decimal(line.amount) > 0 for line in lines):
        raise ValidationError(
            {"items": _("A return can only have negative quantities.")}
        )
    sign_amount = (lambda a: abs(a)) if is_return else (lambda a: a)

    eng_lines = [
        engine.LineInput(
            amount=sign_amount(Decimal(line.amount)),
            taxable=line.gst_treatment == GSTTreatment.TAXABLE,
            item_tax_rate={
                k: Decimal(str(v)) for k, v in (line.item_tax_rate or {}).items()
            },
        )
        for line in lines
    ]
    eng_rows = []
    for row in rows:
        component = row.account.gst_component if row.account.gst_direction else None
        eng_rows.append(
            engine.RowInput(
                rate=Decimal(str(row.rate)),
                add_deduct=row.add_deduct_tax,
                component=component,
                memo=bool(doc.is_reverse_charge and component),
            )
        )
    result = engine.calculate(
        eng_lines,
        eng_rows,
        engine.Policy(
            enable_rounded_total=company.enable_rounded_total,
            round_off_gst_values=company.round_off_gst_values,
        ),
    )
    if is_return:
        result = engine.negate(result)

    for line, res in zip(lines, result.lines):
        line.gst_rate = res.gst_rate
        line.cgst_amount = res.cgst_amount
        line.sgst_amount = res.sgst_amount
        line.igst_amount = res.igst_amount
    for row, row_res in zip(rows, result.rows):
        row.net_amount = row_res.net_amount
        row.tax_amount = row_res.tax_amount
        row.total = row_res.total
    doc.net_total = result.net_total
    doc.total_taxes_and_charges = result.total_taxes_and_charges
    doc.grand_total = result.grand_total
    doc.rounding_adjustment = result.rounding_adjustment
    doc.rounded_total = result.rounded_total

    if for_submit:
        validate_for_submit(doc, lines, ctx)
    return Outcome(ctx, lines, rows, result, rows_replaced)


def persist(doc, outcome: Outcome):
    """Write a :func:`compute` outcome for a saved draft ``doc``."""
    doc.save(update_fields=list(doc.HEADER_UPDATE_FIELDS))
    line_model = doc.item_model()
    for line in outcome.lines:
        line_model.objects.filter(pk=line.pk).update(
            **{
                "gst_hsn_code": line.gst_hsn_code,
                "gst_treatment": line.gst_treatment,
                "item_tax_template": line.item_tax_template,
                "item_tax_rate": line.item_tax_rate,
                "gst_rate": line.gst_rate,
                "cgst_amount": line.cgst_amount,
                "sgst_amount": line.sgst_amount,
                "igst_amount": line.igst_amount,
            }
        )
    tax_model = doc.tax_model()
    fk = doc.tax_fk_name()
    if outcome.rows_replaced:
        tax_model.objects.filter(**{fk: doc}).delete()
        for row in outcome.rows:
            setattr(row, fk, doc)
        tax_model.objects.bulk_create(outcome.rows)
    else:
        for row in outcome.rows:
            tax_model.objects.filter(pk=row.pk).update(
                idx=row.idx,
                description=row.description,
                net_amount=row.net_amount,
                tax_amount=row.tax_amount,
                total=row.total,
            )
    # The viewset prefetches items / taxes; drop the stale cache.
    doc._prefetched_objects_cache = {}
