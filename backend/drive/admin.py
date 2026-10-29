from django.contrib import admin

from drive.models import DriveFile, DriveShare, Folder


@admin.register(Folder)
class FolderAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "parent", "is_system", "created_by", "created_at")
    list_filter = ("company", "is_system")
    search_fields = ("name", "company__name")
    readonly_fields = ("created_at", "modified_at", "created_by", "modified_by")


@admin.register(DriveFile)
class DriveFileAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "company",
        "folder",
        "visibility",
        "size",
        "uploaded_by",
        "created_at",
    )
    list_filter = ("company", "visibility", "content_type")
    search_fields = ("name", "original_filename", "description", "company__name")
    readonly_fields = (
        "size",
        "sha256",
        "content_type",
        "original_filename",
        "created_at",
        "modified_at",
        "created_by",
        "modified_by",
    )


@admin.register(DriveShare)
class DriveShareAdmin(admin.ModelAdmin):
    list_display = ("file", "user", "can_write", "shared_by", "created_at")
    list_filter = ("can_write",)
    search_fields = ("file__name", "user__username", "user__email")
    readonly_fields = ("created_at",)
