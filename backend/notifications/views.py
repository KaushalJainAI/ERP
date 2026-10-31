from django.utils import timezone
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from notifications.models import AlertRule, Notification
from notifications.serializers import AlertRuleSerializer, NotificationSerializer


class IsAdminOrStaff(permissions.BasePermission):
    """
    Only admin or staff users can manage alert rule configurations.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_staff or request.user.is_superuser:
            return True
        if hasattr(request.user, "user_roles"):
            return request.user.user_roles.filter(
                role__name__in=["System Manager", "Admin"]
            ).exists()
        return False


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Strict isolation: users only see their own notifications
        return Notification.objects.filter(user=self.request.user).order_by("-created_at")

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        count = self.get_queryset().filter(read_at__isnull=True).count()
        return Response({"count": count})

    @action(detail=True, methods=["post"], url_path="read")
    def read(self, request, pk=None):
        instance = self.get_object()
        if not instance.read_at:
            instance.read_at = timezone.now()
            instance.save(update_fields=["read_at"])
        return Response(self.get_serializer(instance).data)

    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        marked = self.get_queryset().filter(read_at__isnull=True).update(read_at=timezone.now())
        return Response({"status": "all_read", "marked_read": marked})


class AlertRuleViewSet(viewsets.ModelViewSet):
    queryset = AlertRule.objects.all().order_by("key")
    serializer_class = AlertRuleSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]
    http_method_names = ["get", "put", "patch", "head", "options"]
