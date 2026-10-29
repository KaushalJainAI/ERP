"""Party addresses (ERPNext ``Address``).

ERPNext links an Address to any number of parties through a Dynamic Link table.
Here an address belongs to exactly one Customer or one Supplier (see
``docs/erpnext_divergences.md``); a party that is both gets one row per side.
"""

from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Length
from django.db.models.lookups import Exact
from django.utils.translation import gettext_lazy as _

from buying.models import Supplier
from core.models import State, TimeStampedModel
from gst.validators import validate_gstin_format
from selling.models import Customer

validate_pincode = RegexValidator(r"^[1-9][0-9]{5}$", _("Enter a 6-digit PIN code."))


class AddressType(models.TextChoices):
    BILLING = "Billing", _("Billing")
    SHIPPING = "Shipping", _("Shipping")
    OFFICE = "Office", _("Office")
    PERSONAL = "Personal", _("Personal")
    PLANT = "Plant", _("Plant")
    POSTAL = "Postal", _("Postal")
    SHOP = "Shop", _("Shop")
    SUBSIDIARY = "Subsidiary", _("Subsidiary")
    WAREHOUSE = "Warehouse", _("Warehouse")
    CURRENT = "Current", _("Current")
    PERMANENT = "Permanent", _("Permanent")
    OTHER = "Other", _("Other")


class Address(TimeStampedModel):
    address_title = models.CharField(max_length=200)
    address_type = models.CharField(
        max_length=20, choices=AddressType.choices, default=AddressType.BILLING
    )
    address_line1 = models.CharField(max_length=240)
    address_line2 = models.CharField(max_length=240, blank=True, default="")
    city = models.CharField(max_length=100, blank=True, default="")
    state = models.ForeignKey(
        State, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    pincode = models.CharField(
        max_length=6, blank=True, default="", validators=[validate_pincode]
    )
    country = models.CharField(max_length=60, default="India")
    gstin = models.CharField(
        max_length=20, null=True, blank=True, validators=[validate_gstin_format]
    )
    email = models.EmailField(null=True, blank=True)
    phone = models.CharField(max_length=30, null=True, blank=True)
    is_primary_address = models.BooleanField(default=False)
    is_shipping_address = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    customer = models.ForeignKey(
        Customer,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="addresses",
    )
    supplier = models.ForeignKey(
        Supplier,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="addresses",
    )

    class Meta:
        verbose_name_plural = "addresses"
        ordering = ("address_title", "pk")
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(customer__isnull=False) & models.Q(supplier__isnull=True)
                )
                | (models.Q(customer__isnull=True) & models.Q(supplier__isnull=False)),
                name="check_address_exactly_one_party",
            ),
            models.CheckConstraint(
                check=models.Q(pincode="") | Exact(Length("pincode"), 6),
                name="check_address_pincode_length",
            ),
            models.UniqueConstraint(
                fields=["customer"],
                condition=models.Q(is_primary_address=True, customer__isnull=False),
                name="unique_primary_address_per_customer",
            ),
            models.UniqueConstraint(
                fields=["supplier"],
                condition=models.Q(is_primary_address=True, supplier__isnull=False),
                name="unique_primary_address_per_supplier",
            ),
        ]

    def __str__(self):
        return f"{self.address_title} ({self.address_type})"
