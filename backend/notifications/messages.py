"""Readable alert and digest emails, in the recipient's language.

The bell renders alerts in the browser from ``frontend/src/i18n`` (rule title and
body). Email is rendered here with the same wording through gettext, so a staff
member whose profile language is Hindi gets a Hindi email. Unknown rules fall
back to a plain listing of the parameters.
"""

from django.conf import settings
from django.utils import translation
from django.utils.translation import gettext_lazy as _

EMAILS = {
    "raw_material_shortage": (
        _("Raw material shortage"),
        _("%(item)s: %(qty)s left, minimum %(critical)s."),
    ),
    "ready_low": (
        _("Low ready stock"),
        _("%(item)s: %(qty)s left, minimum %(critical)s."),
    ),
    "large_order": (
        _("Large sales order"),
        _("%(order)s, %(customer)s: ₹%(grand_total)s."),
    ),
    "large_payment": (
        _("Large payment received"),
        _("%(payment)s, %(party)s: ₹%(amount)s."),
    ),
    "payment_delay": (
        _("Payment delayed"),
        _(
            "%(invoice)s, %(customer)s: ₹%(outstanding)s unpaid for %(days_unpaid)s days."
        ),
    ),
    "expense_unpaid": (
        _("Expense unpaid"),
        _(
            "%(invoice)s, %(supplier)s: ₹%(outstanding)s unpaid for %(days_unpaid)s days."
        ),
    ),
    "gst_credit_at_risk": (
        _("GST credit at risk"),
        _("%(invoice)s: ₹%(gst_amount)s GST credit, unpaid for %(days_unpaid)s days."),
    ),
    "integrity_failed": (
        _("Books check failed"),
        _("%(error_count)s problems found in the books.\n\n%(summary)s"),
    ),
    "backup_failed": (
        _("Backup failed"),
        _("Last night's database backup did not complete: %(error)s"),
    ),
    "overdue_bills_digest": (
        _("Overdue bills"),
        _("%(count)s overdue bills, ₹%(total_amount)s in total."),
    ),
    "daily_summary": (
        _("Daily summary"),
        _(
            "₹%(money_in)s received, %(bills_count)s bills worth ₹%(bills_total)s, "
            "%(pending_dispatches)s orders to dispatch."
        ),
    ),
    "weekly_digest": (
        _("Weekly summary"),
        _("To collect this week: ₹%(collections_needed)s. Bills due: ₹%(bills_due)s."),
    ),
}


def _absolute(link):
    if not link:
        return ""
    if link.startswith("http"):
        return link
    return settings.FRONTEND_URL.rstrip("/") + "/" + link.lstrip("/")


def render_email(rule_key, params, link="", language=None):
    """``(subject, body)`` for one alert, in ``language`` (a user's profile
    language). Missing parameters never raise: the generic listing is used."""
    params = {k: ("" if v is None else v) for k, v in (params or {}).items()}
    with translation.override(language or settings.LANGUAGE_CODE):
        template = EMAILS.get(rule_key)
        subject = body = None
        if template:
            title, text = template
            try:
                subject = f"ERP: {title}"
                body = str(text) % params
            except (KeyError, TypeError, ValueError):
                subject = body = None
        if body is None:
            subject = f"ERP: {rule_key}"
            body = "\n".join(f"{k}: {v}" for k, v in params.items())
        url = _absolute(link)
        if url:
            body += "\n\n" + str(_("Open in the ERP: %(url)s") % {"url": url})
        return str(subject), body
