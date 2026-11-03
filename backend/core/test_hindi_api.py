"""L-4: server messages reach Hindi users in Hindi (Accept-Language: hi)."""

import shutil
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase
from django.utils import translation
from rest_framework.test import APIClient

HI_MO = Path(settings.BASE_DIR) / "locale" / "hi" / "LC_MESSAGES" / "django.mo"


class HindiApiMessagesTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        po = HI_MO.with_suffix(".po")
        stale = not HI_MO.exists() or HI_MO.stat().st_mtime < po.stat().st_mtime
        if stale:
            if not shutil.which("msgfmt"):
                raise cls.failureException(
                    "Hindi catalog is not compiled and gettext is missing: install "
                    "gettext and run `python manage.py compilemessages -l hi`."
                )
            call_command("compilemessages", locale=["hi"], verbosity=0)
            # Django caches catalogs per process; drop them so the new .mo loads.
            translation.trans_real._translations = {}

    def test_api_message_is_in_hindi(self):
        res = APIClient().post(
            "/api/auth/logout/", {}, format="json", HTTP_ACCEPT_LANGUAGE="hi"
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["detail"], "साइन आउट हो गए।")

    def test_english_is_unchanged(self):
        res = APIClient().post(
            "/api/auth/logout/", {}, format="json", HTTP_ACCEPT_LANGUAGE="en"
        )
        self.assertEqual(res.data["detail"], "Signed out.")

    def test_placeholders_survive_translation(self):
        with translation.override("hi"):
            text = translation.gettext("Company '%(company)s' not found.") % {
                "company": "NGU"
            }
        self.assertEqual(text, "कंपनी 'NGU' नहीं मिली।")
