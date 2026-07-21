from rest_framework import serializers
from users.models import User, Role, UserRole, RolePermission


class UserSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "username", "email", "first_name", "last_name", "language", "is_active", "roles")
        read_only_fields = ("id", "username")

    def get_roles(self, obj):
        return list(
            UserRole.objects.filter(user=obj).values_list("role__name", flat=True)
        )


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = "__all__"


class RolePermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = RolePermission
        fields = "__all__"
