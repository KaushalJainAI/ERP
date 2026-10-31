import calendar
import datetime
from django.db.models import Q, Sum
from accounts.models import Account, AccountRootType, GLEntry
from accounts.reports import ZERO, money, q
from buying.models import ExpenseCategory, PurchaseInvoice
from core.models import Company, DocStatus


def expense_summary(company, from_date, to_date):
    """Aggregate submitted expenses by category and wages over the period."""
    if isinstance(company, (int, str)):
        company = Company.objects.get(pk=company)

    if isinstance(from_date, str):
        from_date = datetime.date.fromisoformat(from_date)
    if isinstance(to_date, str):
        to_date = datetime.date.fromisoformat(to_date)

    # Previous calendar month bounds
    prev_year = from_date.year if from_date.month > 1 else from_date.year - 1
    prev_month = from_date.month - 1 if from_date.month > 1 else 12
    prev_from_date = datetime.date(prev_year, prev_month, 1)
    _, last_day = calendar.monthrange(prev_year, prev_month)
    prev_to_date = datetime.date(prev_year, prev_month, last_day)

    # Current period submitted expense invoices
    invoices = PurchaseInvoice.objects.filter(
        company=company,
        is_expense=True,
        docstatus=DocStatus.SUBMITTED,
        posting_date__gte=from_date,
        posting_date__lte=to_date,
    ).select_related("expense_category")

    # Previous period submitted expense invoices
    prev_invoices = PurchaseInvoice.objects.filter(
        company=company,
        is_expense=True,
        docstatus=DocStatus.SUBMITTED,
        posting_date__gte=prev_from_date,
        posting_date__lte=prev_to_date,
    ).select_related("expense_category")

    # Categories to include
    all_categories = list(
        ExpenseCategory.objects.filter(company=company, is_active=True).order_by("name")
    )
    cat_ids = {c.id for c in all_categories}
    extra_cat_ids = set(
        list(invoices.values_list("expense_category_id", flat=True))
        + list(prev_invoices.values_list("expense_category_id", flat=True))
    ) - {None}
    extra_cats = ExpenseCategory.objects.filter(
        company=company, id__in=extra_cat_ids
    ).exclude(id__in=cat_ids)
    all_categories.extend(extra_cats)

    # Group current invoices by category
    cur_cat_data = {}
    for inv in invoices:
        cid = inv.expense_category_id
        if cid not in cur_cat_data:
            cur_cat_data[cid] = {"total": ZERO, "due": ZERO, "paid": ZERO}
        total = q(inv.grand_total)
        due = q(inv.outstanding_amount)
        paid = q(total - due)
        cur_cat_data[cid]["total"] += total
        cur_cat_data[cid]["due"] += due
        cur_cat_data[cid]["paid"] += paid

    # Group prev invoices by category
    prev_cat_data = {}
    for inv in prev_invoices:
        cid = inv.expense_category_id
        prev_cat_data[cid] = prev_cat_data.get(cid, ZERO) + q(inv.grand_total)

    category_rows = []
    tot_paid = ZERO
    tot_due = ZERO
    tot_amount = ZERO
    tot_last_month = ZERO

    for cat in all_categories:
        cdata = cur_cat_data.get(cat.id, {"total": ZERO, "due": ZERO, "paid": ZERO})
        prev_tot = prev_cat_data.get(cat.id, ZERO)

        paid_val = cdata["paid"]
        due_val = cdata["due"]
        total_val = cdata["total"]

        tot_paid += paid_val
        tot_due += due_val
        tot_amount += total_val
        tot_last_month += prev_tot

        category_rows.append(
            {
                "category_id": cat.id,
                "category_name": cat.name,
                "paid": money(paid_val),
                "due": money(due_val),
                "total": money(total_val),
                "last_month_total": money(prev_tot),
            }
        )

    # Wages row from GLEntry
    wage_accounts = Account.objects.filter(
        company=company,
        root_type=AccountRootType.EXPENSE,
        is_group=False,
    ).filter(
        Q(name__icontains="wage")
        | Q(name__icontains="salary")
        | Q(name__icontains="payroll")
        | Q(account_type__icontains="wage")
        | Q(account_type__icontains="salary")
        | Q(account_type__icontains="payroll")
    )

    wage_gl = GLEntry.objects.filter(
        company=company,
        account__in=wage_accounts,
        is_cancelled=False,
        posting_date__gte=from_date,
        posting_date__lte=to_date,
    )
    wage_agg = wage_gl.aggregate(dr=Sum("debit"), cr=Sum("credit"))
    wage_total = q((wage_agg["dr"] or ZERO) - (wage_agg["cr"] or ZERO))
    if wage_total < ZERO:
        wage_total = ZERO

    prev_wage_gl = GLEntry.objects.filter(
        company=company,
        account__in=wage_accounts,
        is_cancelled=False,
        posting_date__gte=prev_from_date,
        posting_date__lte=prev_to_date,
    )
    prev_wage_agg = prev_wage_gl.aggregate(dr=Sum("debit"), cr=Sum("credit"))
    prev_wage_total = q((prev_wage_agg["dr"] or ZERO) - (prev_wage_agg["cr"] or ZERO))
    if prev_wage_total < ZERO:
        prev_wage_total = ZERO

    payable_accounts = Account.objects.filter(
        company=company,
        root_type=AccountRootType.LIABILITY,
        is_group=False,
    ).filter(
        Q(name__icontains="wage")
        | Q(name__icontains="salary")
        | Q(name__icontains="payroll")
        | Q(account_type__icontains="payable")
    )

    wage_due = ZERO
    if payable_accounts.exists() and wage_total > ZERO:
        pay_gl = GLEntry.objects.filter(
            company=company,
            account__in=payable_accounts,
            is_cancelled=False,
            posting_date__lte=to_date,
        )
        pay_agg = pay_gl.aggregate(dr=Sum("debit"), cr=Sum("credit"))
        net_payable = q((pay_agg["cr"] or ZERO) - (pay_agg["dr"] or ZERO))
        if net_payable > ZERO:
            wage_due = min(wage_total, net_payable)

    wage_paid = q(wage_total - wage_due)

    tot_paid += wage_paid
    tot_due += wage_due
    tot_amount += wage_total
    tot_last_month += prev_wage_total

    wages_row = {
        "category_id": None,
        "category_name": "Wages & Salaries",
        "paid": money(wage_paid),
        "due": money(wage_due),
        "total": money(wage_total),
        "last_month_total": money(prev_wage_total),
        "is_wages": True,
    }

    grand_totals = {
        "paid": money(tot_paid),
        "due": money(tot_due),
        "total": money(tot_amount),
        "last_month_total": money(tot_last_month),
    }

    return {
        "company": company.id,
        "from_date": str(from_date),
        "to_date": str(to_date),
        "categories": category_rows,
        "wages": wages_row,
        "rows": category_rows + [wages_row],
        "grand_totals": grand_totals,
        "totals": grand_totals,
    }
