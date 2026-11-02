"""Session revocation: sign-out, sign-out everywhere, password change, deactivation."""

import datetime

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from users.models import User

PASSWORD = "Str0ng-Pass-For-Tests!"
NEW_PASSWORD = "An0ther-Strong-Pass!"


class SessionRevocationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="clerk", email="clerk@example.com", password=PASSWORD
        )
        self.admin = User.objects.create_superuser(
            username="boss", email="boss@example.com", password=PASSWORD
        )

    # helpers -------------------------------------------------------------
    def login(self, username="clerk", password=PASSWORD):
        cache.clear()  # the auth throttle is 10/min; tests sign in often
        res = APIClient().post(
            "/api/auth/token/", {"username": username, "password": password}
        )
        self.assertEqual(res.status_code, 200, res.content)
        return res.data

    def me(self, access):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        return client.get("/api/auth/me/")

    def refresh(self, refresh):
        cache.clear()
        return APIClient().post("/api/auth/token/refresh/", {"refresh": refresh})

    def backdate_cutoff(self, user):
        """Make tokens issued so far strictly older than the cutoff second."""
        user.refresh_from_db()
        user.tokens_valid_after = timezone.now() + datetime.timedelta(seconds=2)
        user.save(update_fields=["tokens_valid_after"])

    # tests ---------------------------------------------------------------
    def test_fresh_login_works(self):
        tokens = self.login()
        self.assertEqual(self.me(tokens["access"]).status_code, 200)
        self.assertEqual(self.refresh(tokens["refresh"]).status_code, 200)

    def test_refresh_token_is_single_use(self):
        tokens = self.login()
        self.assertEqual(self.refresh(tokens["refresh"]).status_code, 200)
        # Rotation blacklisted the old one: a stolen copy cannot be replayed.
        self.assertEqual(self.refresh(tokens["refresh"]).status_code, 401)

    def test_logout_blacklists_this_devices_refresh_token(self):
        tokens = self.login()
        res = APIClient().post("/api/auth/logout/", {"refresh": tokens["refresh"]})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.refresh(tokens["refresh"]).status_code, 401)

    def test_logout_with_garbage_token_is_still_200(self):
        res = APIClient().post("/api/auth/logout/", {"refresh": "not-a-token"})
        self.assertEqual(res.status_code, 200)

    def test_cutoff_refuses_older_access_and_refresh_tokens(self):
        tokens = self.login()
        self.backdate_cutoff(self.user)
        self.assertEqual(self.me(tokens["access"]).status_code, 401)
        self.assertEqual(self.refresh(tokens["refresh"]).status_code, 401)

    def test_logout_all_sets_the_cutoff(self):
        tokens = self.login()
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        self.assertEqual(client.post("/api/auth/me/logout-all/").status_code, 200)
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.tokens_valid_after)

    def test_password_change_returns_a_working_pair_and_revokes_others(self):
        other_device = self.login()
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {other_device['access']}")
        res = client.post(
            "/api/auth/me/change-password/",
            {"old_password": PASSWORD, "new_password": NEW_PASSWORD},
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIn("access", res.data)
        self.assertIn("refresh", res.data)
        # The returned pair keeps this device signed in.
        self.assertEqual(self.me(res.data["access"]).status_code, 200)
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.tokens_valid_after)

    def test_deactivation_ends_sessions(self):
        tokens = self.login()
        admin = APIClient()
        admin.credentials(HTTP_AUTHORIZATION=f"Bearer {self.login('boss')['access']}")
        res = admin.post(f"/api/auth/users/{self.user.pk}/deactivate/")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.me(tokens["access"]).status_code, 401)
        self.assertEqual(self.refresh(tokens["refresh"]).status_code, 401)

    def test_admin_password_reset_sets_the_cutoff(self):
        admin = APIClient()
        admin.credentials(HTTP_AUTHORIZATION=f"Bearer {self.login('boss')['access']}")
        res = admin.post(
            f"/api/auth/users/{self.user.pk}/set-password/",
            {"new_password": NEW_PASSWORD},
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.tokens_valid_after)


class SchemaIsPrivateTests(TestCase):
    def test_schema_needs_sign_in_outside_debug(self):
        # Tests run with DEBUG=False, as production does.
        self.assertIn(APIClient().get("/api/schema/").status_code, (401, 403))
