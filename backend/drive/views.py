"""HTTP API for the organisation drive.

Access model:
* the caller must belong to the request's active company (``CompanyScopedMixin``);
* files are visible company-wide by default;
* a private file is visible only to its uploader and explicitly shared members;
* the uploader (or a superuser) may rename/move/delete; a ``can_write`` share may
  edit; deletion is always uploader/superuser only.
"""

from __future__ import annotations

from django.db import IntegrityError
from django.db.models import Count
from django.http import FileResponse
from django.utils.translation import gettext as _
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from core.company_context import CompanyScopedMixin, resolve_active_company
from core.mixins import record_audit_log
from core.models import UserCompany
from drive.models import DriveFile, Folder
from drive.serializers import DriveFileSerializer, FolderSerializer
from drive.services import (
    can_delete_file,
    can_edit_file,
    can_manage_folder,
    company_documents_folder,
    share_file,
    validate_share_target,
    visible_file_queryset,
)
from drive.uploads import (
    DriveUploadError,
    INLINE_CONTENT_TYPES,
    allowed_extensions,
    max_bytes,
    validate_upload,
)
from users.models import User

PERMS = [permissions.IsAuthenticated]


def _error(exc: DriveUploadError):
    return Response({"error": exc.message, "code": exc.code}, status=exc.status)


def _folder_from_data(request, value, *, allow_none=True):
    if value in (None, "", "null"):
        if allow_none:
            return None
        raise ValidationError({"folder": _("A folder is required.")})
    try:
        folder_id = int(value)
    except (TypeError, ValueError):
        raise ValidationError({"folder": _("Must be a folder id.")})
    folder = Folder.objects.filter(
        pk=folder_id, company=request.active_company
    ).first()
    if folder is None:
        raise ValidationError({"folder": _("That folder was not found.")})
    return folder


class FolderViewSet(CompanyScopedMixin, viewsets.ModelViewSet):
    queryset = Folder.objects.select_related("parent", "created_by")
    serializer_class = FolderSerializer
    permission_classes = PERMS
    pagination_class = None
    filterset_fields = ["is_system"]
    search_fields = ["name"]
    ordering_fields = ["name", "created_at"]

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .annotate(
                file_count=Count("files", distinct=True),
                folder_count=Count("children", distinct=True),
            )
        )
        parent = self.request.query_params.get("parent")
        if parent == "root":
            queryset = queryset.filter(parent__isnull=True)
        elif parent not in (None, ""):
            try:
                queryset = queryset.filter(parent_id=int(parent))
            except (TypeError, ValueError):
                raise ValidationError({"parent": _("Must be a folder id or 'root'.")})
        return queryset

    def perform_create(self, serializer):
        try:
            folder = serializer.save(
                company=self.active_company,
                created_by=self.request.user,
                modified_by=self.request.user,
            )
        except IntegrityError:
            raise ValidationError(
                {"name": _("A folder with this name already exists here.")}
            )
        record_audit_log(
            self.request.user,
            "create",
            folder,
            changes={"source": "drive"},
            ip_address=self.request.META.get("REMOTE_ADDR"),
        )

    def perform_update(self, serializer):
        folder = self.get_object()
        if not can_manage_folder(self.request.user, folder):
            raise PermissionDenied(_("Only the folder creator can change it."))
        if folder.is_system:
            raise PermissionDenied(_("System folders cannot be renamed or moved."))
        serializer.save(modified_by=self.request.user)
        record_audit_log(
            self.request.user,
            "update",
            serializer.instance,
            changes={"source": "drive"},
            ip_address=self.request.META.get("REMOTE_ADDR"),
        )

    def perform_destroy(self, instance):
        if instance.is_system:
            raise PermissionDenied(_("System folders cannot be deleted."))
        if not can_manage_folder(self.request.user, instance):
            raise PermissionDenied(_("Only the folder creator can delete it."))
        if instance.files.exists() or instance.children.exists():
            raise ValidationError(
                _("Move or delete the folder's contents before deleting it.")
            )
        record_audit_log(
            self.request.user,
            "delete",
            instance,
            changes={"source": "drive"},
            ip_address=self.request.META.get("REMOTE_ADDR"),
        )
        instance.delete()

    @action(detail=False, methods=["get"], url_path="company-documents")
    def company_documents(self, request):
        """The system folder used by the Company details page."""
        folder = company_documents_folder(self.active_company)
        files = visible_file_queryset(request.user, self.active_company).filter(
            folder=folder
        )
        return Response(
            {
                "folder": FolderSerializer(
                    folder, context=self.get_serializer_context()
                ).data,
                "files": DriveFileSerializer(
                    files, many=True, context=self.get_serializer_context()
                ).data,
                "allowed_extensions": allowed_extensions(),
                "max_bytes": max_bytes(),
            }
        )


class DriveFileViewSet(CompanyScopedMixin, viewsets.ModelViewSet):
    queryset = DriveFile.objects.select_related("folder", "uploaded_by").prefetch_related(
        "shares__user"
    )
    serializer_class = DriveFileSerializer
    permission_classes = PERMS
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    filterset_fields = ["visibility"]
    search_fields = ["name", "original_filename", "description"]
    ordering_fields = ["name", "created_at", "size"]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        queryset = visible_file_queryset(
            self.request.user, self.active_company
        ).select_related("folder", "uploaded_by").prefetch_related("shares__user")

        folder = self.request.query_params.get("folder")
        if folder == "root":
            queryset = queryset.filter(folder__isnull=True)
        elif folder not in (None, ""):
            try:
                queryset = queryset.filter(folder_id=int(folder))
            except (TypeError, ValueError):
                raise ValidationError({"folder": _("Must be a folder id or 'root'.")})

        if self.request.query_params.get("mine") in ("1", "true", "yes"):
            queryset = queryset.filter(uploaded_by=self.request.user)
        if self.request.query_params.get("shared") in ("1", "true", "yes"):
            queryset = queryset.filter(shares__user=self.request.user).distinct()
        return queryset

    def create(self, request, *args, **kwargs):
        try:
            validated = validate_upload(request.FILES.get("file"))
        except DriveUploadError as exc:
            return _error(exc)

        try:
            folder = _folder_from_data(request, request.data.get("folder"))
        except ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)

        visibility = request.data.get("visibility") or DriveFile.Visibility.COMPANY
        if visibility not in DriveFile.Visibility.values:
            return Response(
                {"visibility": _("Invalid visibility.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from core.attachments import safe_filename

        name = safe_filename(
            str(request.data.get("name") or validated.filename)
        ) or validated.filename
        name = name[:255]
        description = str(request.data.get("description") or "")[:500]

        drive_file = DriveFile.objects.create(
            company=self.active_company,
            folder=folder,
            file=request.FILES["file"],
            name=name,
            original_filename=validated.filename,
            content_type=validated.content_type,
            size=validated.size,
            sha256=validated.sha256,
            description=description,
            uploaded_by=request.user,
            created_by=request.user,
            modified_by=request.user,
            visibility=visibility,
        )
        record_audit_log(
            request.user,
            "create",
            drive_file,
            changes={"source": "drive", "visibility": visibility},
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        return Response(
            self.get_serializer(drive_file).data, status=status.HTTP_201_CREATED
        )

    def partial_update(self, request, *args, **kwargs):
        drive_file = self.get_object()
        if not can_edit_file(request.user, drive_file):
            raise PermissionDenied(_("You do not have permission to edit this file."))

        data = request.data
        if "name" in data:
            from core.attachments import safe_filename

            name = safe_filename(str(data.get("name") or ""))
            if not name:
                raise ValidationError({"name": _("A file name is required.")})
            drive_file.name = name[:255]
        if "description" in data:
            drive_file.description = str(data.get("description") or "")[:500]
        if "visibility" in data:
            if not (
                request.user.is_superuser
                or drive_file.uploaded_by_id == request.user.id
            ):
                raise PermissionDenied(
                    _("Only the uploader can change sharing.")
                )
            visibility = data.get("visibility")
            if visibility not in DriveFile.Visibility.values:
                raise ValidationError({"visibility": _("Invalid visibility.")})
            drive_file.visibility = visibility
        if "folder" in data:
            drive_file.folder = _folder_from_data(request, data.get("folder"))
        drive_file.modified_by = request.user
        drive_file.save()
        record_audit_log(
            request.user,
            "update",
            drive_file,
            changes={"source": "drive"},
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        return Response(self.get_serializer(drive_file).data)

    def destroy(self, request, *args, **kwargs):
        drive_file = self.get_object()
        if not can_delete_file(request.user, drive_file):
            raise PermissionDenied(_("Only the uploader can delete this file."))
        record_audit_log(
            request.user,
            "delete",
            drive_file,
            changes={"source": "drive"},
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        drive_file.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        drive_file = self.get_object()
        inline = request.query_params.get("inline") in ("1", "true", "yes")
        inline = inline and drive_file.content_type in INLINE_CONTENT_TYPES
        try:
            handle = drive_file.file.open("rb")
        except (FileNotFoundError, OSError):
            raise NotFound(_("The stored file is missing."))
        return FileResponse(
            handle,
            as_attachment=not inline,
            filename=drive_file.original_filename,
            content_type=drive_file.content_type,
        )

    @action(detail=True, methods=["post"])
    def share(self, request, pk=None):
        drive_file = self.get_object()
        if not (request.user.is_superuser or drive_file.uploaded_by_id == request.user.id):
            raise PermissionDenied(_("Only the uploader can share this file."))

        raw_ids = request.data.get("user_ids")
        if raw_ids is None and request.data.get("user_id") is not None:
            raw_ids = [request.data.get("user_id")]
        if not isinstance(raw_ids, list) or not raw_ids:
            raise ValidationError({"user_ids": _("Select at least one person.")})

        can_write = str(request.data.get("can_write", "")).lower() in (
            "1",
            "true",
            "yes",
        )
        shared = []
        for raw in raw_ids:
            try:
                user_id = int(raw)
            except (TypeError, ValueError):
                raise ValidationError({"user_ids": _("Every recipient must be a user id.")})
            target = User.objects.filter(pk=user_id, is_active=True).first()
            try:
                validate_share_target(request.user, self.active_company, target)
            except ValueError as exc:
                raise ValidationError({"user_ids": str(exc)})
            shared.append(
                share_file(
                    drive_file=drive_file,
                    target_user=target,
                    shared_by=request.user,
                    can_write=can_write,
                )
            )
        record_audit_log(
            request.user,
            "share",
            drive_file,
            changes={"user_ids": [s.user_id for s in shared]},
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        return Response(self.get_serializer(drive_file).data)

    @action(detail=True, methods=["post"])
    def unshare(self, request, pk=None):
        drive_file = self.get_object()
        if not (request.user.is_superuser or drive_file.uploaded_by_id == request.user.id):
            raise PermissionDenied(_("Only the uploader can change sharing."))
        try:
            user_id = int(request.data.get("user_id"))
        except (TypeError, ValueError):
            raise ValidationError({"user_id": _("A user id is required.")})
        drive_file.shares.filter(user_id=user_id).delete()
        record_audit_log(
            request.user,
            "unshare",
            drive_file,
            changes={"user_id": user_id},
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        return Response(self.get_serializer(drive_file).data)


@api_view(["GET"])
@permission_classes(PERMS)
def drive_members(request):
    """Active members of the active company, for the share dialog."""
    company = resolve_active_company(request)
    memberships = (
        UserCompany.objects.filter(
            company=company,
            is_active=True,
            user__is_active=True,
        )
        .select_related("user")
        .order_by("user__first_name", "user__last_name", "user__email", "user__id")
    )
    rows = []
    seen = set()
    for membership in memberships:
        user = membership.user
        if user.pk in seen:
            continue
        seen.add(user.pk)
        rows.append(
            {
                "id": user.pk,
                "name": user.get_full_name() or user.username,
                "email": user.email,
                "is_self": user.pk == request.user.pk,
            }
        )
    return Response(
        {
            "results": rows,
            "allowed_extensions": allowed_extensions(),
            "max_bytes": max_bytes(),
        }
    )
