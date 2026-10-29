from django.contrib import admin

from ngu.models import NguCatalogEntry


@admin.register(NguCatalogEntry)
class NguCatalogEntryAdmin(admin.ModelAdmin):
    list_display = ("kind", "ngu_id", "product_name", "weight", "unit", "price", "item")
    list_filter = ("kind", "is_active", "link_method")
    search_fields = ("product_name", "category_name", "item__code")
