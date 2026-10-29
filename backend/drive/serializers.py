from django.utils.translation import gettext as _
from rest_framework import serializers

from drive.models import DriveFile, DriveShare, Folder
from drive.services import can_delete_file, can_edit_file, can_manage_folder


def _display_name(user) -> str:
    return user.get_full_name() or user.email or user.username


def _active_company(serializer):
    """The tenant boundary from the view (request.active_company is not set)."""
    view = serializer.context.get("view")
    company = getattr(view, "active_company", None)
    if company is None:
        company = getattr(serializer.context.get("request"), "active_company", None)
    if company is None and serializer.instance is not None:
        company = serializer.instance.company
    return company


class FolderSerializer(serializers.ModelSerializer):
    file_count = serializers.SerializerMethodField()
    folder_count = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    path = serializers.SerializerMethodField()

    class Meta:
        model = Folder
        fields = (
            "id",
            "name",
            "parent",
            "is_system",
            "system_key",
            "file_count",
            "folder_count",
            "can_edit",
            "created_by",
            "created_by_name",
            "created_at",
            "modified_at",
            "path",
        )
        read_only_fields = (
            "id",
            "is_system",
            "system_key",
            "file_count",
            "folder_count",
            "can_edit",
            "created_by",
            "created_by_name",
            "created_at",
            "modified_at",
            "path",
        )

    def validate_name(self, value):
        name = (value or "").strip()
        if not name:
            raise serializers.ValidationError(_("A folder name is required."))
        return name

    def validate(self, attrs):
        company = _active_company(self)
        name = attrs.get("name", self.instance.name if self.instance else "")
        parent = attrs.get("parent", self.instance.parent if self.instance else None)
        if company is not None and name:
            duplicates = Folder.objects.filter(
                company=company, parent=parent, name=name
            )
            if self.instance is not None:
                duplicates = duplicates.exclude(pk=self.instance.pk)
            if duplicates.exists():
                raise serializers.ValidationError(
                    {"name": _("A folder with this name already exists here.")}
                )
        return attrs

    def validate_parent(self, value):
        if value is None:
            return value
        company = _active_company(self)
        if company is not None and value.company_id != company.pk:
            raise serializers.ValidationError(_("That folder is not in this company."))
        if self.instance is not None:
            cursor = value
            seen = set()
            while cursor is not None and cursor.pk not in seen:
                if cursor.pk == self.instance.pk:
                    raise serializers.ValidationError(
                        _("A folder cannot be moved into itself.")
                    )
                seen.add(cursor.pk)
                cursor = cursor.parent
        return value

    def get_file_count(self, obj):
        annotated = getattr(obj, "file_count", None)
        return annotated if annotated is not None else obj.files.count()

    def get_folder_count(self, obj):
        annotated = getattr(obj, "folder_count", None)
        return annotated if annotated is not None else obj.children.count()

    def get_can_edit(self, obj):
        request = self.context.get("request")
        return bool(request and can_manage_folder(request.user, obj))

    def get_created_by_name(self, obj):
        return _display_name(obj.created_by) if obj.created_by else ""

    def get_path(self, obj):
        """Root-to-folder breadcrumb, so the UI can navigate without extra calls."""
        chain = []
        cursor = obj
        seen = set()
        while cursor is not None and cursor.pk not in seen:
            seen.add(cursor.pk)
            chain.append({"id": cursor.pk, "name": cursor.name})
            cursor = cursor.parent
        return list(reversed(chain))


class DriveShareSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = DriveShare
        fields = ("id", "user", "name", "email", "can_write", "created_at")
        read_only_fields = fields

    def get_name(self, obj):
        return _display_name(obj.user)


class DriveFileSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.SerializerMethodField()
    folder_name = serializers.CharField(source="folder.name", read_only=True)
    can_edit = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()
    is_owner = serializers.SerializerMethodField()
    previewable = serializers.SerializerMethodField()
    shared_with = serializers.SerializerMethodField()
    shared_with_me = serializers.SerializerMethodField()

    class Meta:
        model = DriveFile
        fields = (
            "id",
            "folder",
            "folder_name",
            "name",
            "original_filename",
            "content_type",
            "size",
            "sha256",
            "description",
            "uploaded_by",
            "uploaded_by_name",
            "created_at",
            "modified_at",
            "visibility",
            "is_owner",
            "can_edit",
            "can_delete",
            "previewable",
            "shared_with",
            "shared_with_me",
        )
        read_only_fields = fields

    def get_uploaded_by_name(self, obj):
        return _display_name(obj.uploaded_by)

    def get_is_owner(self, obj):
        request = self.context.get("request")
        return bool(request and obj.uploaded_by_id == request.user.id)

    def get_can_edit(self, obj):
        request = self.context.get("request")
        return bool(request and can_edit_file(request.user, obj))

    def get_can_delete(self, obj):
        request = self.context.get("request")
        return bool(request and can_delete_file(request.user, obj))

    def get_previewable(self, obj):
        from drive.uploads import INLINE_CONTENT_TYPES

        return obj.content_type in INLINE_CONTENT_TYPES

    def get_shared_with(self, obj):
        request = self.context.get("request")
        if request is None:
            return []
        if not (request.user.is_superuser or obj.uploaded_by_id == request.user.id):
            return []
        return DriveShareSerializer(
            obj.shares.select_related("user").all(), many=True
        ).data

    def get_shared_with_me(self, obj):
        request = self.context.get("request")
        if request is None or obj.uploaded_by_id == request.user.id:
            return False
        return obj.shares.filter(user=request.user).exists()
