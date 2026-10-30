"""Check the assistant's LLM key and model end to end.

    python manage.py assistant_ping
    python manage.py assistant_ping --message "Haldi ka stock kitna hai?"

Runs one real turn (a few paise of LLM spend) as the first active superuser, in a
throwaway conversation, and prints the steps and the reply. Prints no secrets.
"""

import json
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from assistant.agent import DEFAULT_MODEL, Agent
from assistant.models import AssistantConversation
from core.company_context import companies_for


class Command(BaseCommand):
    help = "Check that the AI assistant's LLM is configured and reachable."

    def add_arguments(self, parser):
        parser.add_argument("--message", default="Reply with the single word: ok")

    def handle(self, *args, **options):
        if not os.getenv("LLM_API_KEY"):
            raise CommandError("LLM_API_KEY is not set in backend/.env.")

        user = get_user_model().objects.filter(is_superuser=True, is_active=True).first()
        if user is None:
            raise CommandError("No active superuser to run the turn as.")

        self.stdout.write(f"model: {os.getenv('ASSISTANT_LLM_MODEL') or DEFAULT_MODEL}")
        conversation = AssistantConversation.objects.create(user=user, title="assistant_ping")
        try:
            agent = Agent(user=user, company=companies_for(user).filter(name__icontains="nidhi").first())
            for chunk in agent.run_stream(options["message"], conversation, language=user.language or "en"):
                event, data = chunk.strip().split("\n", 1)
                payload = json.loads(data[len("data: "):])
                self.stdout.write(f"{event[len('event: '):]}: {json.dumps(payload, ensure_ascii=False)[:400]}")
            self.stdout.write(f"usage: {agent.usage}")
        finally:
            conversation.delete()
