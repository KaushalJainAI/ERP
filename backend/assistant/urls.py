from django.urls import path

from .views import (
    AssistantChatStreamView,
    ConversationListCreateView,
    ConversationDetailView,
    ConversationMessagesView,
    ProposalOpenedView,
    ProposalAbandonedView,
    TranscribeAudioView,
)

app_name = "assistant"

urlpatterns = [
    path("chat/stream/", AssistantChatStreamView.as_view(), name="chat_stream"),
    path("conversations/", ConversationListCreateView.as_view(), name="conversations_list_create"),
    path("conversations/<int:pk>/", ConversationDetailView.as_view(), name="conversation_detail"),
    path("conversations/<int:pk>/messages/", ConversationMessagesView.as_view(), name="conversation_messages"),
    path("proposals/<uuid:pk>/opened/", ProposalOpenedView.as_view(), name="proposal_opened"),
    path("proposals/<uuid:pk>/abandoned/", ProposalAbandonedView.as_view(), name="proposal_abandoned"),
    path("transcribe/", TranscribeAudioView.as_view(), name="transcribe"),
]
