"""Views for the AI assistant API."""

import logging
import mimetypes
import os

from django.db.models import Case, IntegerField, Value, When
from django.http import FileResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core import attachments as att_rules
from core.company_context import resolve_active_company
from core.models import Attachment

from .agent import Agent
from .bill_photo import (
    BILL_KINDS,
    BILL_MAX_BYTES,
    BILL_MAX_FILES,
    BILL_MAX_TOTAL_BYTES,
    KINDS,
    STAGING_LABEL,
    StagedFile,
    run_bill_turn,
)
from .models import (
    AssistantConversation,
    AssistantMessage,
    AssistantProposal,
    QuickNote,
)
from .serializers import (
    AssistantConversationSerializer,
    AssistantMessageSerializer,
    ChatStreamRequestSerializer,
    QuickNoteSerializer,
)
from .stt import TranscriptionUnavailable, transcribe_audio
from .throttles import (
    AssistantBillThrottle,
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
        language = serializer.validated_data.get(
            "language", getattr(user, "language", "en") or "en"
        )

        conversation = None
        if conv_id:
            conversation = get_object_or_404(
                AssistantConversation, pk=conv_id, user=user
            )
        else:
            conversation = AssistantConversation.objects.create(
                user=user,
                language=language,
            )

        agent = Agent(
            user=user, company=resolve_active_company(request, required=False)
        )

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
        conversation = get_object_or_404(
            AssistantConversation, pk=conv_id, user=self.request.user
        )
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
                {"error": "no_audio", "message": _("No audio file uploaded.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        lang = (
            request.data.get("language")
            or getattr(request.user, "language", "en")
            or "en"
        )
        try:
            text = transcribe_audio(audio_file, language=lang)
        except TranscriptionUnavailable as exc:
            logger.warning("Transcription unavailable: %s", exc)
            return Response(
                {
                    "error": "stt_unavailable",
                    "message": _("Voice input is not available right now."),
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"text": text})


class BillPhotoView(APIView):
    """A paper or PDF read into the proposal it makes: a supplier bill, a customer's
    order, a payment slip or cheque, or an expense receipt.

    A paper the reader cannot place returns a `question` whose `choices` are the four
    kinds. The person's choice is sent back as `reply_message_id` (the reply that asked)
    with `kind`; the kept file is then read again as that kind, with no new upload.

    Read failures (model down, unreadable photo, permission) come back as 200 with a
    friendly `reply` and an `error` code, and write nothing. Only a rejected file is
    a 400 or 413.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [AssistantBillThrottle]

    def post(self, request):
        if request.data.get("reply_message_id") not in (None, ""):
            return self._read_kept_file(request)
        # `file` (one page) or `files` (several pages of one bill), up to BILL_MAX_FILES.
        uploads = request.FILES.getlist("files")
        single = request.FILES.get("file")
        if single is not None:
            uploads.insert(0, single)
        if not uploads:
            return Response(
                {"error": "no_file", "message": _("No bill photo uploaded.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(uploads) > BILL_MAX_FILES:
            return Response(
                {
                    "error": "bill_too_many_files",
                    "message": _("Send at most %(count)s files for one bill.")
                    % {"count": BILL_MAX_FILES},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        checked_uploads = []
        for upload in uploads:
            try:
                checked = att_rules.validate_upload(upload)
            except att_rules.AttachmentError as exc:
                return Response(
                    {"error": exc.code, "message": exc.message}, status=exc.status
                )
            if checked.kind not in BILL_KINDS:
                return Response(
                    {
                        "error": "bill_type_not_allowed",
                        "message": _("Send the bill as a JPG, PNG, WebP or PDF file."),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if checked.size > BILL_MAX_BYTES:
                return Response(
                    {
                        "error": "bill_too_large",
                        "message": _(
                            "The bill photo is too large. The maximum size is %(mb)s MB."
                        )
                        % {"mb": f"{BILL_MAX_BYTES / (1024 * 1024):g}"},
                    },
                    status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )
            checked_uploads.append((checked, upload))
        if (
            sum(checked.size for checked, _upload in checked_uploads)
            > BILL_MAX_TOTAL_BYTES
        ):
            return Response(
                {
                    "error": "bill_too_large",
                    "message": _(
                        "The bill is too large. The files together may be at most %(mb)s MB."
                    )
                    % {"mb": f"{BILL_MAX_TOTAL_BYTES / (1024 * 1024):g}"},
                },
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        pages = [(upload.read(), checked) for checked, upload in checked_uploads]

        conversation = None
        raw_conv = request.data.get("conversation_id")
        if raw_conv not in (None, ""):
            try:
                conv_id = int(raw_conv)
            except (TypeError, ValueError):
                return Response(
                    {
                        "error": "bad_conversation",
                        "message": _("That conversation was not found."),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            conversation = get_object_or_404(
                AssistantConversation, pk=conv_id, user=request.user
            )

        language = (
            request.data.get("language")
            or getattr(request.user, "language", "en")
            or "en"
        )
        company = resolve_active_company(request, required=False)
        body = run_bill_turn(
            request.user,
            uploads=pages,
            company_pk=company.pk if company else None,
            conversation=conversation,
            language=language,
        )
        return Response(body)

    def _read_kept_file(self, request):
        """Read the file a reply kept, again, as the kind the person chose."""
        kind = request.data.get("kind")
        if kind not in KINDS:
            return Response(
                {
                    "error": "bill_kind_invalid",
                    "message": _(
                        "Choose what the paper is: a supplier bill, a customer's order, a payment slip or cheque, or an expense receipt."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            reply = AssistantMessage.objects.select_related("conversation").get(
                pk=int(request.data.get("reply_message_id")),
                role="assistant",
                conversation__user=request.user,
            )
        except (TypeError, ValueError, AssistantMessage.DoesNotExist):
            reply = None
        staged = (
            list(
                Attachment.objects.filter(
                    model_label=STAGING_LABEL, object_id=str(reply.pk)
                ).order_by("pk")
            )
            if reply is not None
            else []
        )
        if not staged:
            return Response(
                {"error": "bill_not_found", "message": _("That paper was not found.")},
                status=status.HTTP_404_NOT_FOUND,
            )
        pages = []
        for att in staged:
            with att.file.open("rb") as handle:
                data = handle.read()
            checked = StagedFile(
                filename=att.original_filename,
                content_type=att.content_type,
                size=att.size,
                sha256=att.sha256,
            )
            pages.append((data, checked))

        company = resolve_active_company(request, required=False)
        body = run_bill_turn(
            request.user,
            uploads=pages,
            company_pk=company.pk if company else None,
            conversation=reply.conversation,
            language=getattr(request.user, "language", "en") or "en",
            kind=kind,
        )
        return Response(body)


class QuickNoteViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    """Quick notes of the active company: list (Open first, newest first), create, done.

    A note writes nothing to the books. There is no update or delete; a note is
    marked Done once its document is approved or dismissed.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = QuickNoteSerializer

    def get_queryset(self):
        company = resolve_active_company(self.request)
        return (
            QuickNote.objects.filter(company=company)
            .annotate(
                open_first=Case(
                    When(status="open", then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                )
            )
            .order_by("open_first", "-created_at", "-id")
        )

    def perform_create(self, serializer):
        serializer.save(
            company=resolve_active_company(self.request),
            created_by=self.request.user,
        )

    @action(detail=True, methods=["post"])
    def done(self, request, pk=None):
        note = self.get_object()
        if note.status == "open":
            QuickNote.objects.filter(pk=note.pk, status="open").update(
                status="done", done_at=timezone.now()
            )
            note.refresh_from_db()
        return Response(self.get_serializer(note).data)

    @action(detail=True, methods=["get"])
    def file(self, request, pk=None):
        """Stream the note's photo or voice clip inline. Company scoped; 404 without a file."""
        note = self.get_object()
        if not note.attachment:
            raise NotFound(_("This note has no file."))
        name = note.attachment.name
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        try:
            handle = note.attachment.open("rb")
        except (OSError, ValueError):
            raise NotFound(_("The stored file is missing."))
        response = FileResponse(
            handle,
            content_type=content_type,
            as_attachment=False,
            filename=os.path.basename(name),
        )
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
        response["Cache-Control"] = "private, no-store"
        return response
