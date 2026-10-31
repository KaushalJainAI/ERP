from rest_framework.routers import DefaultRouter

from notifications.views import AlertRuleViewSet, NotificationViewSet

router = DefaultRouter()
router.register(r"rules", AlertRuleViewSet, basename="alert-rule")
router.register(r"", NotificationViewSet, basename="notification")

urlpatterns = router.urls
