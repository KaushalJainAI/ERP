import datetime
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, transaction
from django.db.models import F, Min, Q
from django.db.models.functions import Length
from django.db.models.lookups import Exact
from django.utils.translation import gettext as _


class TimeStampedModel(models.Model):
    """Abstract base model tracking creation and modification timestamps and actors."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    modified_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        abstract = True


class DocStatus(models.IntegerChoices):
    DRAFT = 0, "Draft"
    SUBMITTED = 1, "Submitted"
    CANCELLED = 2, "Cancelled"


class SubmittableDocument(TimeStampedModel):
    """
    Abstract base model for submittable transactional documents.
    Docstatus transitions: Draft (0) -> Submitted (1) -> Cancelled (2).
    Submitted and Cancelled documents are immutable.
    """

    name = models.CharField(max_length=60, unique=True, db_index=True)
    docstatus = models.IntegerField(
        choices=DocStatus.choices,
        default=DocStatus.DRAFT,
        db_index=True,
    )
    amended_from = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="amendments",
    )

    class Meta:
        abstract = True

    def validate_before_submit(self):
        """Hook called before document submit. Should raise ValidationError on unpostable state."""
        pass

    def on_submit(self):
        """Hook called after docstatus is set to SUBMITTED. Posts ledger entries via posting service."""
        pass

    def on_cancel(self):
        """Hook called after docstatus is set to CANCELLED. Posts reversing ledger entries."""
        pass

    def submit(self, user=None):
        """Atomically validate and submit the document."""
        with transaction.atomic():
            # Lock the row and re-read docstatus so two concurrent submits
            # cannot both pass the guard and post ledger entries twice.
            locked_status = (
                self.__class__.objects.select_for_update()
                .filter(pk=self.pk)
                .values_list("docstatus", flat=True)
                .first()
            )
            if locked_status is None:
                raise ValidationError(_("Document no longer exists."))
            if locked_status != DocStatus.DRAFT:
                raise ValidationError(_("Only draft documents can be submitted."))
            self.docstatus = locked_status
            self.validate_before_submit()
            self.docstatus = DocStatus.SUBMITTED
            if user and hasattr(user, "id"):
                self.modified_by = user
            self.save(update_fields=["docstatus", "modified_at", "modified_by"])
            self.on_submit()

    def cancel(self, user=None):
        """Atomically cancel the document and reverse ledger entries."""
        if getattr(self, "is_historical", False):
            # Imported from Tally: its GL is the TLY-* journal, and Tally stays
            # the place to correct the old books.
            raise ValidationError(
                _("Historical documents imported from Tally cannot be cancelled.")
            )
        with transaction.atomic():
            # Lock the row and re-read docstatus so two concurrent cancels
            # cannot both pass the guard and reverse ledger entries twice.
            locked_status = (
                self.__class__.objects.select_for_update()
                .filter(pk=self.pk)
                .values_list("docstatus", flat=True)
                .first()
            )
            if locked_status is None:
                raise ValidationError(_("Document no longer exists."))
            if locked_status != DocStatus.SUBMITTED:
                raise ValidationError(_("Only submitted documents can be cancelled."))
            self.docstatus = DocStatus.CANCELLED
            if user and hasattr(user, "id"):
                self.modified_by = user
            self.save(update_fields=["docstatus", "modified_at", "modified_by"])
            self.on_cancel()

    def amend(self, new_name=None):
        """Create a new draft amendment from a cancelled document.

        The amendment is saved (as a draft) together with a copy of every child
        row set (line items, payment references, ...) in one transaction, so the
        returned draft is complete and can be edited and submitted. Derived
        totals are recomputed from the copied rows.
        """
        if self.docstatus != DocStatus.CANCELLED:
            raise ValidationError(_("Only cancelled documents can be amended."))
        with transaction.atomic():
            amendment = self.__class__()
            # Copy fields from self
            for field in self._meta.fields:
                if field.name in [
                    "id",
                    "pk",
                    "name",
                    "docstatus",
                    "created_at",
                    "modified_at",
                    "amended_from",
                ]:
                    continue
                setattr(amendment, field.name, getattr(self, field.name))
            amendment.docstatus = DocStatus.DRAFT
            amendment.amended_from = self
            # A submittable's `name` is unique and NOT NULL; an amendment must get its
            # own name. Follow ERPNext's convention of suffixing the source name with
            # an incrementing amendment index (e.g. SINV-2026-0001 -> SINV-2026-0001-1).
            amendment.name = new_name or self._next_amendment_name()
            amendment.save()
            self._copy_child_rows_to(amendment)
            amendment._recompute_derived_totals()
            return amendment

    def _copy_child_rows_to(self, amendment):
        """Copy every child row (non-submittable model with a CASCADE FK to this
        document, e.g. ``lines``/``items``/``references``) onto ``amendment``."""
        for rel in self._meta.related_objects:
            if not rel.one_to_many or rel.on_delete is not models.CASCADE:
                continue
            child_model = rel.related_model
            if issubclass(child_model, SubmittableDocument):
                continue
            fk = rel.field
            for child in child_model._default_manager.filter(
                **{fk.name: self.pk}
            ).order_by("pk"):
                clone = child_model()
                for field in child_model._meta.concrete_fields:
                    if field.primary_key or field.name == fk.name:
                        continue
                    setattr(clone, field.attname, getattr(child, field.attname))
                setattr(clone, fk.name, amendment)
                clone.save()

    def _recompute_derived_totals(self):
        """Refresh header totals of a freshly copied draft from its child rows."""
        if hasattr(self, "recompute_totals"):
            self.recompute_totals()
            self.save(update_fields=["total_debit", "total_credit"])
        elif hasattr(self, "calculate_taxes_and_totals"):
            # Taxable documents: the tax engine recomputes from the copied rows and
            # line snapshots (a never-calculated legacy draft is resolved once).
            self.calculate_taxes_and_totals()
        elif hasattr(self, "grand_total"):
            total = Decimal("0.00")
            for rel in self._meta.related_objects:
                if not rel.one_to_many or rel.on_delete is not models.CASCADE:
                    continue
                if issubclass(rel.related_model, SubmittableDocument):
                    continue
                if not any(
                    f.name == "amount" for f in rel.related_model._meta.concrete_fields
                ):
                    continue
                total += sum(
                    (
                        row.amount
                        for row in getattr(self, rel.get_accessor_name()).all()
                    ),
                    Decimal("0.00"),
                )
            self.grand_total = total
            self.save(update_fields=["grand_total"])

    def _next_amendment_name(self):
        """Return a unique name for a new amendment of this document."""
        base = self.name
        n = self.amendments.count() + 1
        candidate = f"{base}-{n}"
        # Guard against gaps/races: bump until the name is free.
        while self.__class__.objects.filter(name=candidate).exists():
            n += 1
            candidate = f"{base}-{n}"
        return candidate

    def save(self, *args, **kwargs):
        # Prevent edit of non-draft documents unless updating docstatus/timestamps
        if self.pk:
            orig = self.__class__.objects.filter(pk=self.pk).values("docstatus").first()
            if orig and orig["docstatus"] != DocStatus.DRAFT:
                update_fields = kwargs.get("update_fields")
                allowed = {"docstatus", "modified_at", "modified_by"}
                if update_fields and not set(update_fields).issubset(allowed):
                    raise ValidationError(
                        _("Submitted or cancelled documents cannot be modified.")
                    )
                elif not update_fields:
                    raise ValidationError(
                        _("Submitted or cancelled documents cannot be modified.")
                    )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.docstatus != DocStatus.DRAFT:
            raise ValidationError(_("Only draft documents can be deleted."))
        super().delete(*args, **kwargs)


class GSTCategory(models.TextChoices):
    """GST registration category of a party or a company (India Compliance names)."""

    REGISTERED_REGULAR = "Registered Regular", "Registered Regular"
    REGISTERED_COMPOSITION = "Registered Composition", "Registered Composition"
    UNREGISTERED = "Unregistered", "Unregistered"
    # UI-only alias of Unregistered (walk-in B2C parties); behaves identically in reports.
    CONSUMER = "Consumer", "Consumer"
    SEZ = "SEZ", "SEZ"
    OVERSEAS = "Overseas", "Overseas"


class State(models.Model):
    """GST state / union-territory master (code = the 2-digit GST state code).

    Seeded by ``core.migrations.0008_seed_states`` from ``core.gst_states``; read-only
    over the API. ``is_active=False`` marks retired codes (25, 28) that are still
    valid inside older GSTINs.
    """

    code = models.CharField(max_length=2, primary_key=True)
    name = models.CharField(max_length=60, unique=True)
    abbr = models.CharField(max_length=3, blank=True, default="")
    is_union_territory = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(
                check=Exact(Length("code"), 2), name="check_state_code_length"
            )
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Company(TimeStampedModel):
    name = models.CharField(max_length=200, unique=True)
    abbr = models.CharField(max_length=10, unique=True)
    default_currency = models.CharField(max_length=3, default="INR")
    country = models.CharField(max_length=100, default="India")
    gstin = models.CharField(max_length=20, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    # Company profile (the details an organisation needs on file for statutory
    # documents, correspondence and its own records). Accounting/GST settings
    # remain the source of truth for transactions; these fields are descriptive.
    legal_name = models.CharField(max_length=250, blank=True, default="")
    phone_no = models.CharField(max_length=30, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    website = models.URLField(blank=True, default="")
    address_line1 = models.CharField(max_length=250, blank=True, default="")
    address_line2 = models.CharField(max_length=250, blank=True, default="")
    city = models.CharField(max_length=100, blank=True, default="")
    pincode = models.CharField(
        max_length=6,
        blank=True,
        default="",
        validators=[
            RegexValidator(
                regex=r"^[1-9][0-9]{5}$",
                message=_("Enter a valid 6-digit PIN code."),
                code="invalid_pincode",
            )
        ],
    )
    pan = models.CharField(
        max_length=10,
        blank=True,
        default="",
        validators=[
            RegexValidator(
                regex=r"^[A-Z]{5}[0-9]{4}[A-Z]$",
                message=_("Enter a valid 10-character PAN."),
                code="invalid_pan",
            )
        ],
    )
    cin = models.CharField(
        max_length=21,
        blank=True,
        default="",
        validators=[
            RegexValidator(
                regex=r"^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$",
                message=_("Enter a valid 21-character CIN."),
                code="invalid_cin",
            )
        ],
    )
    date_of_incorporation = models.DateField(null=True, blank=True)

    # Perpetual inventory (ERPNext: Company.enable_perpetual_inventory and the
    # default_inventory_account / stock_adjustment_account / default_expense_account
    # [= Cost of Goods Sold] accounts). Defaults False so companies without a chart
    # of accounts keep working; seed_defaults turns it on for the seeded company.
    enable_perpetual_inventory = models.BooleanField(default=False)
    default_inventory_account = models.ForeignKey(
        "accounts.Account",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    stock_adjustment_account = models.ForeignKey(
        "accounts.Account",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    default_expense_account = models.ForeignKey(
        "accounts.Account",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    # Default party / income accounts (ERPNext: Company.default_receivable_account,
    # default_payable_account, default_income_account). An invoice saved without
    # them gets these; blank falls back to accounts.mapping's name heuristic.
    default_receivable_account = models.ForeignKey(
        "accounts.Account",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    default_payable_account = models.ForeignKey(
        "accounts.Account",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    default_income_account = models.ForeignKey(
        "accounts.Account",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    # Price-list defaults (design section 5.3): used when the party and its group
    # do not name a list.
    default_selling_price_list = models.ForeignKey(
        "stock.PriceList",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    default_buying_price_list = models.ForeignKey(
        "stock.PriceList",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    unprocessed_warehouse = models.ForeignKey(
        "stock.Warehouse",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    in_process_warehouse = models.ForeignKey(
        "stock.Warehouse",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    ready_warehouse = models.ForeignKey(
        "stock.Warehouse",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    # GST (see docs/gst_design.md). Percent/threshold style settings live on the
    # company (India Compliance keeps them in a "GST Settings" doctype).
    state = models.ForeignKey(
        State, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # v1 supports Registered Regular and Unregistered companies on GST documents.
    gst_category = models.CharField(
        max_length=24,
        choices=GSTCategory.choices,
        default=GSTCategory.UNREGISTERED,
    )
    hsn_min_digits = models.PositiveSmallIntegerField(
        default=6, choices=((4, "4"), (6, "6"), (8, "8"))
    )
    # Defaults False (like enable_perpetual_inventory); seed_defaults turns it on
    # for the seeded company.
    enable_rounded_total = models.BooleanField(default=False)
    round_off_account = models.ForeignKey(
        "accounts.Account",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    round_off_gst_values = models.BooleanField(default=False)

    # Payment terms (collections & payables): default credit days for customer bills
    # (standard 90 days) and supplier bills (blank until confirmed).
    default_customer_credit_days = models.PositiveIntegerField(default=90)
    default_supplier_credit_days = models.PositiveIntegerField(null=True, blank=True)
    require_proof = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "Companies"

    def clean(self):
        """Company master checks (admin form and the profile PATCH endpoint)."""
        from gst.validators import check_gstin, normalise_gstin

        self.legal_name = (self.legal_name or "").strip()
        self.phone_no = (self.phone_no or "").strip()
        self.email = (self.email or "").strip()
        self.website = (self.website or "").strip()
        self.address_line1 = (self.address_line1 or "").strip()
        self.address_line2 = (self.address_line2 or "").strip()
        self.city = (self.city or "").strip()
        self.pincode = (self.pincode or "").strip()
        self.pan = (self.pan or "").strip().upper()
        self.cin = (self.cin or "").strip().upper()

        errors = {}
        gstin = normalise_gstin(self.gstin)
        if gstin:
            result = check_gstin(gstin)
            if not result.valid:
                errors["gstin"] = result.reason
            else:
                if self.state_id is None:
                    errors["state"] = _("State is required when a GSTIN is set.")
                elif gstin[:2] != self.state_id:
                    errors["state"] = _(
                        "GSTIN prefix %(code)s does not match the selected state."
                    ) % {"code": gstin[:2]}
            if self.gst_category != GSTCategory.REGISTERED_REGULAR:
                errors["gst_category"] = _(
                    "A company with a GSTIN must be 'Registered Regular'."
                )
        acc = self.round_off_account if self.round_off_account_id else None
        if acc is not None:
            if self.pk is None or acc.company_id != self.pk:
                errors["round_off_account"] = _(
                    "Round-off account must belong to this company."
                )
            elif acc.is_group:
                errors["round_off_account"] = _(
                    "Round-off account must not be a group account."
                )
        for field, account_types, root_type in (
            ("default_receivable_account", ("Receivable",), None),
            ("default_payable_account", ("Payable",), None),
            ("default_income_account", None, "Income"),
        ):
            acc = getattr(self, field) if getattr(self, f"{field}_id") else None
            if acc is None:
                continue
            if self.pk is None or acc.company_id != self.pk or acc.is_group:
                errors[field] = _("Pick a non-group account of this company.")
            elif account_types and acc.account_type not in account_types:
                errors[field] = _("Pick an account of type %(type)s.") % {
                    "type": account_types[0]
                }
            elif root_type and acc.root_type != root_type:
                errors[field] = _("Pick an Income account.")
        for field in (
            "unprocessed_warehouse",
            "in_process_warehouse",
            "ready_warehouse",
        ):
            wh = getattr(self, field) if getattr(self, f"{field}_id") else None
            if wh is None:
                continue
            if self.pk is None or wh.company_id != self.pk or wh.is_group:
                errors[field] = _("Pick a non-group warehouse of this company.")
        if errors:
            raise ValidationError(errors)

    def get_perpetual_accounts(self, *, need_cogs=False, need_adjustment=False):
        """Return the validated perpetual-inventory accounts as a dict, or None
        when perpetual inventory is off. Raises ValidationError naming the missing
        or invalid Company field (leaf account of this company)."""
        from django.core.exceptions import ValidationError

        if not self.enable_perpetual_inventory:
            return None
        required = ["default_inventory_account"]
        if need_cogs:
            required.append("default_expense_account")
        if need_adjustment:
            required.append("stock_adjustment_account")
        accounts = {}
        for field in required:
            acc = getattr(self, field)
            if acc is None:
                raise ValidationError(
                    _(
                        "Perpetual inventory is enabled for company '%(company)s' but "
                        "Company.%(field)s is not set."
                    )
                    % {"company": self.name, "field": field}
                )
            if acc.is_group:
                raise ValidationError(
                    _("Company.%(field)s ('%(account)s') must not be a group account.")
                    % {"field": field, "account": acc.name}
                )
            if acc.company_id != self.pk:
                raise ValidationError(
                    _(
                        "Company.%(field)s ('%(account)s') must belong to company '%(company)s'."
                    )
                    % {"field": field, "account": acc.name, "company": self.name}
                )
            accounts[field] = acc
        return accounts

    def __str__(self):
        return self.name


class UserCompany(TimeStampedModel):
    """Which companies a user may enter, and which one is their default.

    Company is the tenant boundary: every company-owned queryset is filtered by
    the user's active company (``core.company_context``). Membership is the
    permission — a user with no row here sees no company data at all. Roles stay
    global; this model only answers *which* companies, not *what* they may do.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="company_memberships",
    )
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="user_memberships"
    )
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "company"], name="unique_user_company"
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(is_default=True, is_active=True),
                name="unique_default_company_per_user",
            ),
        ]
        ordering = ("user_id", "-is_default", "company_id")

    def __str__(self):
        return f"{self.user} @ {self.company}"


class FiscalYear(TimeStampedModel):
    name = models.CharField(max_length=40, unique=True)
    start_date = models.DateField()
    end_date = models.DateField()
    is_closed = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(end_date__gt=F("start_date")),
                name="check_fiscal_year_dates",
            )
        ]

    @classmethod
    def for_date(cls, date):
        """Return the open FiscalYear containing ``date`` or raise ValidationError.

        Mirrors ERPNext's ``get_fiscal_year`` + closed-year check: posting into a
        missing or closed fiscal year is refused. If year ranges overlap, the one
        starting latest (most specific) wins.
        """
        fy = (
            cls.objects.filter(start_date__lte=date, end_date__gte=date)
            .order_by("-start_date", "-pk")
            .first()
        )
        if fy is None:
            raise ValidationError(
                _("No fiscal year found for posting date %(date)s.") % {"date": date}
            )
        if fy.is_closed:
            raise ValidationError(
                _("Fiscal year %(name)s is closed; posting on %(date)s is not allowed.")
                % {"name": fy.name, "date": date}
            )
        return fy

    @classmethod
    def ensure_for_date(cls, date):
        """Create the Indian April-to-March year containing ``date`` if none covers it.

        Returns the covering FiscalYear (existing or new), or ``None`` when nothing
        is created: the year would start before the earliest existing year, its
        name is taken by a year that does not cover ``date``, or it would overlap
        an existing year. Idempotent. Not called from posting: a typo'd date must
        not create a year.
        """
        covering = (
            cls.objects.filter(start_date__lte=date, end_date__gte=date)
            .order_by("-start_date", "-pk")
            .first()
        )
        if covering is not None:
            return covering

        start_year = date.year if date.month >= 4 else date.year - 1
        start = datetime.date(start_year, 4, 1)
        end = datetime.date(start_year + 1, 3, 31)
        earliest = cls.objects.aggregate(first=Min("start_date"))["first"]
        if earliest is not None and start < earliest:
            return None
        name = f"{start_year}-{str(start_year + 1)[2:]}"
        if cls.objects.filter(name=name).exists():
            return None
        if cls.objects.filter(start_date__lte=end, end_date__gte=start).exists():
            return None
        with transaction.atomic():
            return cls.objects.create(
                name=name, start_date=start, end_date=end, is_closed=False
            )

    def clean(self):
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValidationError({"end_date": _("End date must be after start date.")})
        if self.start_date and self.end_date:
            overlapping = FiscalYear.objects.filter(
                start_date__lte=self.end_date, end_date__gte=self.start_date
            ).exclude(pk=self.pk)
            other = overlapping.first()
            if other is not None:
                raise ValidationError(
                    _("Fiscal year overlaps %(name)s.") % {"name": other.name}
                )

    def __str__(self):
        return self.name


class NamingSeries(TimeStampedModel):
    prefix = models.CharField(max_length=40, unique=True)
    current = models.IntegerField(default=0)
    padding = models.SmallIntegerField(default=4)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(current__gte=0),
                name="check_naming_series_current_nonneg",
            )
        ]

    @classmethod
    def next(cls, prefix: str, padding: int = 4) -> str:
        """Atomically generate the next document name for a prefix."""
        with transaction.atomic():
            obj, _ = cls.objects.select_for_update().get_or_create(
                prefix=prefix,
                defaults={"current": 0, "padding": padding},
            )
            obj.current = F("current") + 1
            obj.save(update_fields=["current"])
            obj.refresh_from_db()
            seq_str = str(obj.current).zfill(obj.padding)
            return f"{prefix}{seq_str}"

    def __str__(self):
        return f"{self.prefix} ({self.current})"


class AuditLog(models.Model):
    """Immutable log of model mutations."""

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    action = models.CharField(max_length=40)  # create, update, submit, cancel, delete
    model_label = models.CharField(max_length=120, db_index=True)
    object_id = models.CharField(max_length=60, db_index=True)
    changes = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError(
                _("AuditLog rows are immutable and cannot be updated.")
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(_("AuditLog rows are immutable and cannot be deleted."))

    def __str__(self):
        return f"{self.timestamp} - {self.user} - {self.action} {self.model_label}:{self.object_id}"


class SystemHealthCheck(models.Model):
    """Recorded results from verify_integrity checks."""

    checked_at = models.DateTimeField(auto_now_add=True)
    check_name = models.CharField(max_length=80)
    status = models.CharField(max_length=10)  # ok, drift, healed, alert
    details = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.checked_at} - {self.check_name}: {self.status}"


class Outbox(models.Model):
    """Deduplicated transactional outbox for reliable external side effects."""

    dedupe_key = models.CharField(max_length=120, unique=True)
    channel = models.CharField(max_length=20)  # email, sms, whatsapp, webhook
    payload = models.JSONField(default=dict)
    status = models.CharField(
        max_length=12, default="pending"
    )  # pending, sent, failed, dead
    attempts = models.SmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __init__(self, *args, **kwargs):
        recipient = kwargs.pop("recipient", None)
        super().__init__(*args, **kwargs)
        if recipient is not None:
            if not isinstance(self.payload, dict):
                self.payload = {}
            self.payload["recipient"] = recipient
            if "to" not in self.payload:
                self.payload["to"] = recipient

    @property
    def recipient(self):
        if isinstance(self.payload, dict):
            return self.payload.get("recipient") or self.payload.get("to")
        return None

    def __str__(self):
        return f"{self.dedupe_key} [{self.channel}] - {self.status}"


class DummySubmittableDoc(SubmittableDocument):
    """Concrete SubmittableDocument model used for testing submittable document workflows."""

    title = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class Language(models.Model):
    code = models.CharField(max_length=5, unique=True)
    name = models.CharField(max_length=60)
    is_rtl = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.code})"


class FieldTranslation(models.Model):
    model_label = models.CharField(max_length=120)
    object_id = models.BigIntegerField()
    field_name = models.CharField(max_length=60)
    language = models.CharField(max_length=5)
    value = models.TextField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["model_label", "object_id", "field_name", "language"],
                name="unique_field_translation",
            )
        ]

    def __str__(self):
        return (
            f"{self.model_label}:{self.object_id}.{self.field_name} [{self.language}]"
        )


def attachment_upload_to(instance, filename):
    """``attachments/YYYY/MM/DD/<uuid>.<ext>``: partitioned by date, and the stored
    name never contains anything the client sent except the (validated) extension."""
    import os
    import uuid

    from django.utils import timezone

    ext = os.path.splitext(filename)[1].lower()
    today = timezone.localdate()
    return f"attachments/{today:%Y/%m/%d}/{uuid.uuid4().hex}{ext}"


class AttachmentQuerySet(models.QuerySet):
    """Proof is never rewritten, and bulk deletes still go through the guards."""

    def update(self, **kwargs):
        raise ValidationError(_("Attachments are immutable and cannot be updated."))

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError(_("Attachments are immutable and cannot be updated."))

    def delete(self):
        count = 0
        for obj in self:
            obj.delete()
            count += 1
        return count, {"core.Attachment": count}


class Attachment(models.Model):
    """A proof document (scan, photo, PDF...) attached to a document or master.

    Mirrors ERPNext's ``File`` (``attached_to_doctype`` / ``attached_to_name``) but
    is append-only: a row is never edited, and when the parent is submitted or
    cancelled the row can never be deleted either (that is the proof). The upload
    rules (type, size, RBAC) live in ``core.attachments``; the guarantees below
    hold for any code path, API or not.
    """

    model_label = models.CharField(max_length=120)  # e.g. "selling.SalesInvoice"
    object_id = models.CharField(max_length=60)  # str(parent.pk)
    file = models.FileField(upload_to=attachment_upload_to, max_length=255)
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)  # sniffed, never client-sent
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    description = models.CharField(max_length=240, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = AttachmentQuerySet.as_manager()

    class Meta:
        ordering = ["-uploaded_at", "-id"]
        indexes = [
            models.Index(
                fields=["model_label", "object_id"], name="attachment_parent_idx"
            )
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(size__gt=0), name="attachment_size_positive"
            ),
            models.CheckConstraint(
                check=Exact(Length("sha256"), 64),
                name="attachment_sha256_length",
            ),
            models.CheckConstraint(
                check=~Q(object_id="") & ~Q(model_label="") & ~Q(original_filename=""),
                name="attachment_parent_and_name_present",
            ),
            # The same bytes attached twice to one record is a slip, not proof.
            models.UniqueConstraint(
                fields=["model_label", "object_id", "sha256"],
                name="attachment_unique_file_per_parent",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            raise ValidationError(_("Attachments are immutable and cannot be updated."))
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from core import attachments

        parent = attachments.get_parent(self.model_label, self.object_id)
        if parent is not None and attachments.parent_docstatus(parent) in (
            DocStatus.SUBMITTED,
            DocStatus.CANCELLED,
        ):
            raise ValidationError(
                _(
                    "Proof attached to a submitted or cancelled document cannot be deleted."
                )
            )
        storage, name = self.file.storage, self.file.name
        result = super().delete(*args, **kwargs)
        if name:
            transaction.on_commit(lambda: storage.delete(name))
        return result

    def __str__(self):
        return f"{self.original_filename} -> {self.model_label}:{self.object_id}"
