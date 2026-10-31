from django.contrib.auth import get_user_model
from rest_framework import serializers

from notifications.models import AlertRule, Notification

User = get_user_model()


class UserSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "email", "first_name", "last_name"]


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = [
            "id",
            "rule_key",
            "message_key",
            "params",
            "link",
            "read_at",
            "created_at",
            "dedupe_key",
        ]
        read_only_fields = fields


class AlertRuleSerializer(serializers.ModelSerializer):
    recipients = serializers.PrimaryKeyRelatedField(
        many=True, queryset=User.objects.all(), required=False
    )
    recipient_details = UserSummarySerializer(
        source="recipients", many=True, read_only=True
    )

    class Meta:
        model = AlertRule
        fields = [
            "id",
            "key",
            "is_active",
            "days",
            "recipients",
            "recipient_details",
        ]
        read_only_fields = ["id", "key"]
