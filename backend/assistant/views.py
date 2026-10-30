"""Views for the AI assistant API."""

import logging
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.company_context import resolve_active_company

from .agent import Agent
from .models import AssistantConversation, AssistantProposal
from .serializers import (
    AssistantConversationSerializer,
    AssistantMessageSerializer,
    ChatStreamRequestSerializer,
)
from .stt import TranscriptionUnavailable, transcribe_audio
from .throttles import (
    AssistantBurstThrottle,
    AssistantDailyThrottle,
    AssistantTranscribeThrottle,
)

logger = logging.getLogger(__name__)


class AssistantChatStreamView(APIView):
    """Server-Sent Events streaming endpoint for one assistant turn."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [AssistantBurstThrottle, AssistantDailyThrottle]

    def post(self, request):
        serializer = ChatStreamRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        msg_text = serializer.validated_data["message"]
        conv_id = serializer.validated_data.get("conversation_id")
        input_mode = serializer.validated_data.get("input_mode", "text")
        language = serializer.validated_data.get("language", getattr(user, "language", "en") or "en")

        conversation = None
        if conv_id:
            conversation = get_object_or_404(AssistantConversation, pk=conv_id, user=user)
        else:
            conversation = AssistantConversation.objects.create(
                user=user,
                language=language,
            )

        agent = Agent(user=user, company=resolve_active_company(request, required=False))

        stream = agent.run_stream(
            user_text=msg_text,
            conversation=conversation,
            input_mode=input_mode,
            language=language,
        )

        response = StreamingHttpResponse(stream, content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response


class ConversationListCreateView(generics.ListCreateAPIView):
    """List or create chat threads owned by the authenticated user."""

    permission_classes = [IsAuthenticated]
    serializer_class = AssistantConversationSerializer

    def get_queryset(self):
        return AssistantConversation.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        user = self.request.user
        lang = getattr(user, "language", "en") or "en"
        serializer.save(user=user, language=lang)


class ConversationDetailView(generics.RetrieveDestroyAPIView):
    """Get or delete a conversation thread owned by the authenticated user."""

    permission_classes = [IsAuthenticated]
    serializer_class = AssistantConversationSerializer

    def get_queryset(self):
        return AssistantConversation.objects.filter(user=self.request.user)


class ConversationMessagesView(generics.ListAPIView):
    """List messages in a conversation owned by the authenticated user."""

    permission_classes = [IsAuthenticated]
    serializer_class = AssistantMessageSerializer

    def get_queryset(self):
        conv_id = self.kwargs["pk"]
        conversation = get_object_or_404(AssistantConversation, pk=conv_id, user=self.request.user)
        return conversation.messages.all().prefetch_related("proposals")


class ProposalOpenedView(APIView):
    """Mark a proposal as opened in the frontend form."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        proposal = get_object_or_404(
            AssistantProposal,
            pk=pk,
            message__conversation__user=request.user,
        )
        if proposal.status in ["proposed", "abandoned"]:
            proposal.status = "opened"
            proposal.save(update_fields=["status", "updated_at"])

        return Response({"status": proposal.status})


class ProposalAbandonedView(APIView):
    """Mark a proposal as abandoned when user leaves form without saving."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        proposal = get_object_or_404(
            AssistantProposal,
            pk=pk,
            message__conversation__user=request.user,
        )
        if proposal.status in ["proposed", "opened"]:
            proposal.status = "abandoned"
            proposal.save(update_fields=["status", "updated_at"])

        return Response({"status": proposal.status})


class TranscribeAudioView(APIView):
    """Speech-to-text audio transcription endpoint."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [AssistantTranscribeThrottle]

    def post(self, request):
        audio_file = request.FILES.get("file") or request.FILES.get("audio")
        if not audio_file:
            return Response(
                {"error": "no_audio", "message": "No audio file uploaded."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        lang = request.data.get("language") or getattr(request.user, "language", "en") or "en"
        try:
            text = transcribe_audio(audio_file, language=lang)
        except TranscriptionUnavailable as exc:
            logger.warning("Transcription unavailable: %s", exc)
            return Response(
                {"error": "stt_unavailable", "message": "Voice input is not available right now."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"text": text})
