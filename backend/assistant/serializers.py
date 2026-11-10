"""Serializers for assistant conversation, message, and proposal models."""

from django.utils.translation import gettext as _
from rest_framework import serializers

from core.attachments import AttachmentError

from .models import (
    AssistantConversation,
    AssistantMessage,
    AssistantProposal,
    QuickNote,
)
from .quick_notes import validate_note_upload


class AssistantProposalSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssistantProposal
        fields = [
            "id",
            "target",
            "route",
            "values",
            "summary",
            "ai_filled_keys",
            "warnings",
            "status",
            "saved_model_label",
            "saved_object_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class AssistantMessageSerializer(serializers.ModelSerializer):
    proposals = AssistantProposalSerializer(many=True, read_only=True)

    class Meta:
        model = AssistantMessage
        fields = [
            "id",
            "conversation",
            "role",
            "content",
            "input_mode",
            "steps",
            "proposals",
            "created_at",
        ]
        read_only_fields = fields


class AssistantConversationSerializer(serializers.ModelSerializer):
    last_message = serializers.SerializerMethodField()

    class Meta:
        model = AssistantConversation
        fields = [
            "id",
            "title",
            "language",
            "created_at",
            "updated_at",
            "last_message",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "last_message"]

    def get_last_message(self, obj) -> str:
        last = obj.messages.order_by("-created_at").first()
        return last.content[:100] if last else ""


class ChatStreamRequestSerializer(serializers.Serializer):
    message = serializers.CharField(max_length=2000)
    conversation_id = serializers.IntegerField(required=False, allow_null=True)
    input_mode = serializers.ChoiceField(choices=["text", "voice"], default="text")
    language = serializers.CharField(max_length=10, default="en")


class QuickNoteSerializer(serializers.ModelSerializer):
    """A quick note. The file is write-only: the note shows only that one exists."""

    has_attachment = serializers.SerializerMethodField()

    class Meta:
        model = QuickNote
        fields = [
            "id",
            "text",
            "attachment",
            "has_attachment",
            "status",
            "proposal",
            "created_at",
            "done_at",
        ]
        read_only_fields = ["id", "status", "proposal", "created_at", "done_at"]
        extra_kwargs = {
            "text": {"required": False},
            "attachment": {"required": False, "write_only": True},
        }

    def get_has_attachment(self, obj) -> bool:
        return bool(obj.attachment)

    def validate_attachment(self, value):
        if not value:
            return value
        try:
            validate_note_upload(value)
        except AttachmentError as exc:
            raise serializers.ValidationError(exc.message)
        return value

    def validate(self, attrs):
        if not (attrs.get("text") or attrs.get("attachment")):
            raise serializers.ValidationError(
                {"text": _("Write a note, or add a photo or voice clip.")}
            )
        return attrs
