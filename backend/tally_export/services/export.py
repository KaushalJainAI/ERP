"""Pre-flight check and export creation (the only writer of the export log)."""

import datetime
import io
import zipfile
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from django.core.files.base import ContentFile
from django.db import transaction

from core.mixins import record_audit_log
from tally_export.models import (
    TallyExportBatch,
    TallyExportedVoucher,
    TallyLedgerMap,
    TallyStockItemMap,
)
from tally_export.services import xmlgen
from tally_export.services.build import VoucherDraft, build_voucher
from tally_export.services.collect import VoucherRef, list_refs
from tally_export.services.maps import (
    MapIndex,
    Unmapped,
    get_settings,
    legacy_group_names,
    party_group,
    suggest_group,
    suggest_ledger,
)


class ExportError(Exception):
    """The export cannot be created (nothing to send, bad input)."""


class ExportBlocked(ExportError):
    """Unmapped ledgers / items or unbalanced vouchers: carries the plan."""

    def __init__(self, plan: "Plan"):
        super().__init__("export_blocked")
        self.plan = plan


@dataclass
class Plan:
    company: object
    settings: object
    refs: List[VoucherRef]
    drafts: List[VoucherDraft]
    skipped: List[dict]
    unmapped: List[Unmapped] = field(default_factory=list)
    unmapped_vouchers: Dict[tuple, int] = field(default_factory=dict)

    @property
    def unbalanced(self) -> List[VoucherDraft]:
        return [d for d in self.drafts if not d.balanced]

    @property
    def can_export(self) -> bool:
        return bool(self.drafts) and not self.unmapped and not self.unbalanced


def make_plan(
    company,
    from_date=None,
    to_date=None,
    include_exported=False,
    keys: Optional[Iterable[tuple]] = None,
) -> Plan:
    settings = get_settings(company)
    refs, skipped = list_refs(
        company, settings, from_date, to_date, only_pending=not include_exported
    )
    if keys is not None:
        wanted = set(keys)
        refs = [r for r in refs if r.key in wanted]
    index = MapIndex(company)
    drafts = [build_voucher(r, company, settings, index) for r in refs]
    plan = Plan(company, settings, refs, drafts, skipped)
    seen: Dict[tuple, Unmapped] = {}
    for d in drafts:
        for u in d.unmapped:
            seen.setdefault((u.kind, u.key), u)
            plan.unmapped_vouchers[(u.kind, u.key)] = (
                plan.unmapped_vouchers.get((u.kind, u.key), 0) + 1
            )
    plan.unmapped = list(seen.values())
    return plan


# --------------------------------------------------------------------------
# Pre-flight (JSON)
# --------------------------------------------------------------------------


def _unmapped_details(plan: Plan) -> dict:
    from accounts.models import Account
    from stock.models import Item

    legacy = legacy_group_names(plan.company)
    accounts, parties, items = [], [], []
    for u in plan.unmapped:
        used = plan.unmapped_vouchers.get((u.kind, u.key), 0)
        if u.kind == "account":
            acct = Account.objects.select_related("parent").get(pk=u.key[0])
            accounts.append(
                {
                    "account": acct.pk,
                    "account_name": acct.name,
                    "root_type": acct.root_type,
                    "account_type": acct.account_type,
                    "suggested_ledger": suggest_ledger(acct),
                    "suggested_group": suggest_group(acct, legacy),
                    "vouchers": used,
                }
            )
        elif u.kind == "party":
            ptype, pname, acc_id = u.key
            parties.append(
                {
                    "account": acc_id,
                    "party_type": ptype,
                    "party": pname,
                    "suggested_ledger": pname,
                    "suggested_group": party_group(ptype),
                    "vouchers": used,
                }
            )
        else:
            item = Item.objects.select_related("stock_uom").get(pk=u.key[0])
            items.append(
                {
                    "item": item.pk,
                    "item_code": item.code,
                    "item_name": item.name,
                    "suggested_item": item.name,
                    "suggested_unit": item.stock_uom.name,
                    "vouchers": used,
                }
            )
    return {"accounts": accounts, "parties": parties, "items": items}


def preflight(company, from_date=None, to_date=None, include_exported=False) -> dict:
    plan = make_plan(company, from_date, to_date, include_exported)
    vouchers = []
    for ref, d in zip(plan.refs, plan.drafts):
        dr, cr = d.debit_credit()
        vouchers.append(
            {
                "doctype": d.doctype,
                "name": d.name,
                "posting_date": d.posting_date.isoformat(),
                "tally_voucher_type": d.tally_type,
                "action": d.action,
                "debit": str(dr),
                "credit": str(cr),
                "balanced": d.balanced,
                "unmapped": bool(d.unmapped),
                "problems": d.problems,
                "previously_exported": ref.prev is not None,
            }
        )
    return {
        "company": company.pk,
        "tally_company_name": plan.settings.tally_company_name,
        "from_date": from_date.isoformat() if from_date else None,
        "to_date": to_date.isoformat() if to_date else None,
        "vouchers": vouchers,
        "voucher_count": sum(1 for d in plan.drafts if d.action == "Create"),
        "cancel_count": sum(1 for d in plan.drafts if d.action == "Cancel"),
        "unmapped": _unmapped_details(plan),
        "unmapped_count": len(plan.unmapped),
        "unbalanced_count": len(plan.unbalanced),
        "skipped": plan.skipped,
        "can_export": plan.can_export,
    }


# --------------------------------------------------------------------------
# New masters
# --------------------------------------------------------------------------

_REG_TYPE = {
    "Registered Regular": "Regular",
    "Registered Composition": "Composition",
    "Consumer": "Consumer",
    "Unregistered": "Unregistered",
}


def _auto_map(plan: Plan) -> List:
    """Create maps for everything unmapped, named like the ERP object."""
    from accounts.models import Account
    from stock.models import Item

    legacy = legacy_group_names(plan.company)
    created = []
    for u in plan.unmapped:
        if u.kind == "account":
            acct = Account.objects.select_related("parent").get(pk=u.key[0])
            created.append(
                TallyLedgerMap.objects.create(
                    company=plan.company,
                    account=acct,
                    tally_ledger=suggest_ledger(acct),
                    tally_group=suggest_group(acct, legacy),
                    source="auto",
                )
            )
        elif u.kind == "party":
            ptype, pname, acc_id = u.key
            created.append(
                TallyLedgerMap.objects.create(
                    company=plan.company,
                    account_id=acc_id,
                    party_type=ptype,
                    party=pname,
                    tally_ledger=pname,
                    tally_group=party_group(ptype),
                    source="auto",
                )
            )
        else:
            item = Item.objects.select_related("stock_uom").get(pk=u.key[0])
            created.append(
                TallyStockItemMap.objects.create(
                    company=plan.company,
                    item=item,
                    tally_item=item.name,
                    tally_unit=item.stock_uom.name,
                    source="auto",
                )
            )
    return created


def _party_master(m: TallyLedgerMap):
    from buying.models import Supplier
    from selling.models import Customer

    model = Customer if m.party_type == "Customer" else Supplier
    p = (
        model.objects.filter(company=m.company, name=m.party)
        .select_related("state")
        .first()
    )
    group = m.tally_group or party_group(m.party_type)
    if p is None:
        return xmlgen.ledger_master(m.tally_ledger, group, billwise=True)
    addr = (
        p.addresses.filter(is_active=True).order_by("-is_primary_address", "pk").first()
    )
    lines, pin, state = [], "", p.state.name if p.state else ""
    if addr is not None:
        lines = [x for x in (addr.address_line1, addr.address_line2, addr.city) if x]
        pin = addr.pincode
        if addr.state:
            state = addr.state.name
    return xmlgen.ledger_master(
        m.tally_ledger,
        group,
        billwise=True,
        gstin=p.gstin or "",
        state=state,
        address=lines,
        pincode=pin,
        reg_type=_REG_TYPE.get(p.gst_category, "Unregistered"),
    )


def _item_gst_rate(item):
    tpl = item.item_tax_template
    if tpl is None:
        return None
    for row in tpl.rows.all():
        if row.gst_component == "IGST":
            return row.tax_rate
    return None


def master_elements(created: list):
    legacy = None
    masters, units, ledgers, items = [], {}, 0, 0
    for m in created:
        if isinstance(m, TallyLedgerMap):
            if m.party:
                masters.append(_party_master(m))
            else:
                if legacy is None:
                    legacy = legacy_group_names(m.company)
                group = m.tally_group or suggest_group(m.account, legacy)
                masters.append(
                    xmlgen.ledger_master(
                        m.tally_ledger,
                        group,
                        billwise=group in ("Sundry Debtors", "Sundry Creditors"),
                    )
                )
            ledgers += 1
        else:
            item = m.item
            unit = m.tally_unit or item.stock_uom.name
            units[unit] = xmlgen.unit_master(unit)
            masters.append(
                xmlgen.stock_item_master(
                    m.tally_item,
                    unit,
                    hsn=item.gst_hsn_code,
                    gst_rate=_item_gst_rate(item),
                    taxable=not (item.is_nil_exempt or item.is_non_gst),
                )
            )
            items += 1
    # Units first (a stock item needs its unit), then ledgers, then items.
    ordered = (
        list(units.values())
        + [e for e in masters if e.tag == "LEDGER"]
        + [e for e in masters if e.tag == "STOCKITEM"]
    )
    return ordered, ledgers, items


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------


def create_export(
    company,
    user=None,
    from_date=None,
    to_date=None,
    create_masters=False,
    include_exported=False,
    keys: Optional[Iterable[tuple]] = None,
    kind: str = "range",
    source_batch: Optional[TallyExportBatch] = None,
) -> TallyExportBatch:
    """Build the XML of the vouchers in range and log the hand-over.

    Raises ``ExportBlocked`` while anything is unmapped (unless
    ``create_masters``) or a voucher does not balance, ``ExportError`` when
    there is nothing to send.
    """
    if from_date and to_date and from_date > to_date:
        raise ExportError("invalid_range")
    with transaction.atomic():
        plan = make_plan(company, from_date, to_date, include_exported, keys)
        if not plan.drafts:
            raise ExportError("nothing_to_export")
        created: list = []
        if plan.unmapped:
            if not create_masters:
                raise ExportBlocked(plan)
            created = _auto_map(plan)
            plan = make_plan(company, from_date, to_date, include_exported, keys)
        if plan.unmapped or plan.unbalanced:
            raise ExportBlocked(plan)

        name = plan.settings.tally_company_name or company.name
        data = xmlgen.vouchers_file(name, plan.drafts)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        batch = TallyExportBatch(
            company=company,
            kind=kind,
            from_date=from_date,
            to_date=to_date,
            source_batch=source_batch,
            created_by=user if getattr(user, "is_authenticated", False) else None,
            file_sha256=xmlgen.sha256(data),
            voucher_count=sum(1 for d in plan.drafts if d.action == "Create"),
            cancel_count=sum(1 for d in plan.drafts if d.action == "Cancel"),
        )
        batch.file.save(
            f"vouchers-{stamp}-{batch.file_sha256[:8]}.xml",
            ContentFile(data),
            save=False,
        )
        if created:
            elements, n_ledgers, n_items = master_elements(created)
            mdata = xmlgen.masters_file(name, elements)
            batch.masters_sha256 = xmlgen.sha256(mdata)
            batch.ledger_master_count = n_ledgers
            batch.item_master_count = n_items
            batch.masters_file.save(
                f"masters-{stamp}-{batch.masters_sha256[:8]}.xml",
                ContentFile(mdata),
                save=False,
            )
        batch.save()
        TallyExportedVoucher.objects.bulk_create(
            [
                TallyExportedVoucher(
                    batch=batch,
                    doctype=d.doctype,
                    name=d.name,
                    docstatus=d.docstatus,
                    action=d.action,
                    tally_voucher_type=d.tally_type,
                    posting_date=d.posting_date,
                    remote_id=d.remote_id,
                    voucher_hash=xmlgen.voucher_hash(d),
                )
                for d in plan.drafts
            ]
        )
        for m in created:
            type(m).objects.filter(pk=m.pk).update(master_batch=batch)
        record_audit_log(
            user,
            "create",
            batch,
            changes={
                "vouchers": batch.voucher_count,
                "cancels": batch.cancel_count,
                "from": str(from_date or ""),
                "to": str(to_date or ""),
                "sha256": batch.file_sha256,
                "masters": len(created),
            },
        )
    return batch


def reexport_batch(batch: TallyExportBatch, user=None) -> TallyExportBatch:
    """A new batch repeating the vouchers of ``batch`` as they stand now."""
    keys = [(v.doctype, v.name) for v in batch.vouchers.all()]
    return create_export(
        batch.company,
        user,
        batch.from_date,
        batch.to_date,
        include_exported=True,
        keys=keys,
        kind="reexport",
        source_batch=batch,
    )


def reexport_vouchers(company, keys, user=None) -> TallyExportBatch:
    return create_export(
        company, user, include_exported=True, keys=list(keys), kind="reexport"
    )


def batch_zip(batch: TallyExportBatch) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if batch.masters_file:
            with batch.masters_file.open("rb") as f:
                z.writestr("1-masters.xml", f.read())
        with batch.file.open("rb") as f:
            z.writestr("2-vouchers.xml", f.read())
    return buf.getvalue()
