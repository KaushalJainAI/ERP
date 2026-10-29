from django.urls import include, path
from rest_framework.routers import DefaultRouter

from drive.views import DriveFileViewSet, FolderViewSet, drive_members

app_name = "drive"

router = DefaultRouter()
router.register("folders", FolderViewSet, basename="folder")
router.register("files", DriveFileViewSet, basename="file")

urlpatterns = [
    path("members/", drive_members, name="drive-members"),
    path("", include(router.urls)),
]
