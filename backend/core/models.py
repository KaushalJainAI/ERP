from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Q
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
                raise ValidationError("Document no longer exists.")
            if locked_status != DocStatus.DRAFT:
                raise ValidationError("Only draft documents can be submitted.")
            self.docstatus = locked_status
            self.validate_before_submit()
            self.docstatus = DocStatus.SUBMITTED
            if user and hasattr(user, "id"):
                self.modified_by = user
            self.save(update_fields=["docstatus", "modified_at", "modified_by"])
            self.on_submit()

    def cancel(self, user=None):
        """Atomically cancel the document and reverse ledger entries."""
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
                raise ValidationError("Document no longer exists.")
            if locked_status != DocStatus.SUBMITTED:
                raise ValidationError("Only submitted documents can be cancelled.")
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
            raise ValidationError("Only cancelled documents can be amended.")
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
                        "Submitted or cancelled documents cannot be modified."
                    )
                elif not update_fields:
                    raise ValidationError(
                        "Submitted or cancelled documents cannot be modified."
                    )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.docstatus != DocStatus.DRAFT:
            raise ValidationError("Only draft documents can be deleted.")
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

    class Meta:
        verbose_name_plural = "Companies"

    def clean(self):
        """GST master checks (run by the admin form; the API for companies is read-only)."""
        from gst.validators import check_gstin, normalise_gstin

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
                    f"Perpetual inventory is enabled for company '{self.name}' but "
                    f"Company.{field} is not set."
                )
            if acc.is_group:
                raise ValidationError(
                    f"Company.{field} ('{acc.name}') must not be a group account."
                )
            if acc.company_id != self.pk:
                raise ValidationError(
                    f"Company.{field} ('{acc.name}') must belong to company '{self.name}'."
                )
            accounts[field] = acc
        return accounts

    def __str__(self):
        return self.name


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
            raise ValidationError(f"No fiscal year found for posting date {date}.")
        if fy.is_closed:
            raise ValidationError(
                f"Fiscal year {fy.name} is closed; posting on {date} is not allowed."
            )
        return fy

    def clean(self):
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValidationError({"end_date": "End date must be after start date."})

    def __str__(self):
        return self.name


class NamingSeries(TimeStampedModel):
    prefix = models.CharField(max_length=40, unique=True)
    current = models.IntegerField(default=0)
    padding = models.SmallIntegerField(default=4)

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
    action = models.CharField(max_length=20)  # create, update, submit, cancel, delete
    model_label = models.CharField(max_length=120, db_index=True)
    object_id = models.CharField(max_length=60, db_index=True)
    changes = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("AuditLog rows are immutable and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("AuditLog rows are immutable and cannot be deleted.")

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
