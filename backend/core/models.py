from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Q


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
            if self.docstatus != DocStatus.DRAFT:
                raise ValidationError("Only draft documents can be submitted.")
            self.validate_before_submit()
            self.docstatus = DocStatus.SUBMITTED
            if user and hasattr(user, "id"):
                self.modified_by = user
            self.save(update_fields=["docstatus", "modified_at", "modified_by"])
            self.on_submit()

    def cancel(self, user=None):
        """Atomically cancel the document and reverse ledger entries."""
        with transaction.atomic():
            if self.docstatus != DocStatus.SUBMITTED:
                raise ValidationError("Only submitted documents can be cancelled.")
            self.docstatus = DocStatus.CANCELLED
            if user and hasattr(user, "id"):
                self.modified_by = user
            self.save(update_fields=["docstatus", "modified_at", "modified_by"])
            self.on_cancel()

    def amend(self, new_name=None):
        """Create a new draft amendment from a cancelled document."""
        if self.docstatus != DocStatus.CANCELLED:
            raise ValidationError("Only cancelled documents can be amended.")
        amendment = self.__class__()
        # Copy fields from self
        for field in self._meta.fields:
            if field.name in ["id", "pk", "name", "docstatus", "created_at", "modified_at", "amended_from"]:
                continue
            setattr(amendment, field.name, getattr(self, field.name))
        amendment.docstatus = DocStatus.DRAFT
        amendment.amended_from = self
        if new_name:
            amendment.name = new_name
        return amendment

    def save(self, *args, **kwargs):
        # Prevent edit of non-draft documents unless updating docstatus/timestamps
        if self.pk:
            orig = self.__class__.objects.filter(pk=self.pk).values("docstatus").first()
            if orig and orig["docstatus"] != DocStatus.DRAFT:
                update_fields = kwargs.get("update_fields")
                allowed = {"docstatus", "modified_at", "modified_by"}
                if update_fields and not set(update_fields).issubset(allowed):
                    raise ValidationError("Submitted or cancelled documents cannot be modified.")
                elif not update_fields:
                    raise ValidationError("Submitted or cancelled documents cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.docstatus != DocStatus.DRAFT:
            raise ValidationError("Only draft documents can be deleted.")
        super().delete(*args, **kwargs)


class Company(TimeStampedModel):
    name = models.CharField(max_length=200, unique=True)
    abbr = models.CharField(max_length=10, unique=True)
    default_currency = models.CharField(max_length=3, default="INR")
    country = models.CharField(max_length=100, default="India")
    gstin = models.CharField(max_length=20, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "Companies"

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
    status = models.CharField(max_length=12, default="pending")  # pending, sent, failed, dead
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
        return f"{self.model_label}:{self.object_id}.{self.field_name} [{self.language}]"



