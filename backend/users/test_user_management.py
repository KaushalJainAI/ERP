"""User management: /api/auth/users/, set-password, me/change-password.

The contract: only a superuser or a System Manager (RBAC on users.User /
users.UserRole) may manage users; users are deactivated, never deleted;
is_superuser is never writable over the API; nobody can deactivate themselves
or edit their own roles; every mutation lands in the AuditLog.
"""

import pytest
from django.core.cache import cache
from django.core.management import call_command
from rest_framework.test import APIClient

from core.models import AuditLog
from users.models import Role, User, UserRole

USERS = "/api/auth/users/"
STRONG = "Zx9!kLm2#vQw"


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def seeded(db):
    call_command("seed_defaults", verbosity=0)


def _user(username, *, roles=(), superuser=False, password=STRONG):
    u = User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=password,
        is_superuser=superuser,
    )
    for r in roles:
        UserRole.objects.create(user=u, role=Role.objects.get(name=r))
    return u


def _client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.fixture
def sysmgr(seeded):
    return _user("sysmgr", roles=["System Manager"])


@pytest.fixture
def clerk(seeded):
    return _user("clerk", roles=["Default Role"])


def _payload(**over):
    body = {
        "username": "newbie",
        "email": "Newbie@Example.com",
        "first_name": "New",
        "last_name": "Bie",
        "language": "hi",
        "password": STRONG,
    }
    body.update(over)
    return body


# --------------------------------------------------------------------------- RBAC
@pytest.mark.django_db
class TestAccess:
    def test_anonymous_is_401(self, seeded):
        assert APIClient().get(USERS).status_code == 401

    def test_normal_user_gets_403_everywhere(self, clerk):
        c = _client(clerk)
        other = _user("other")
        assert c.get(USERS).status_code == 403
        assert c.get(f"{USERS}{other.pk}/").status_code == 403
        assert c.post(USERS, _payload(), format="json").status_code == 403
        assert (
            c.patch(
                f"{USERS}{other.pk}/", {"first_name": "x"}, format="json"
            ).status_code
            == 403
        )
        assert (
            c.post(
                f"{USERS}{other.pk}/set-password/",
                {"new_password": STRONG + "1"},
                format="json",
            ).status_code
            == 403
        )
        assert c.post(f"{USERS}{other.pk}/deactivate/").status_code == 403

    def test_staff_flag_does_not_grant_access(self, seeded):
        u = _user("staffer")
        u.is_staff = True
        u.save()
        assert _client(u).get(USERS).status_code == 403

    def test_system_manager_and_superuser_can_list(self, sysmgr, seeded):
        assert _client(sysmgr).get(USERS).status_code == 200
        root = _user("root", superuser=True)
        assert _client(root).get(USERS).status_code == 200

    def test_create_only_role_cannot_reset_passwords_or_deactivate(self, seeded):
        """POST actions on an existing user need can_write, not can_create."""
        from users.models import RolePermission

        role = Role.objects.create(name="User Creator")
        RolePermission.objects.create(
            role=role, model_label="users.User", can_read=True, can_create=True
        )
        u = _user("creator")
        UserRole.objects.create(user=u, role=role)
        victim = _user("victim")
        c = _client(u)
        assert c.get(USERS).status_code == 200
        assert (
            c.post(
                f"{USERS}{victim.pk}/set-password/",
                {"new_password": STRONG + "1"},
                format="json",
            ).status_code
            == 403
        )
        assert c.post(f"{USERS}{victim.pk}/deactivate/").status_code == 403

    def test_no_hard_delete(self, sysmgr):
        victim = _user("victim")
        assert _client(sysmgr).delete(f"{USERS}{victim.pk}/").status_code == 405
        assert User.objects.filter(pk=victim.pk).exists()


# --------------------------------------------------------------------------- CRUD
@pytest.mark.django_db
class TestCrud:
    def test_create_user_with_roles_and_audit(self, sysmgr):
        role = Role.objects.get(name="Stock Manager")
        r = _client(sysmgr).post(USERS, _payload(role_ids=[role.pk]), format="json")
        assert r.status_code == 201, r.data
        assert "password" not in r.data
        assert r.data["email"] == "newbie@example.com"
        assert r.data["roles"] == ["Stock Manager"]
        assert r.data["role_ids"] == [role.pk]
        assert r.data["is_superuser"] is False
        user = User.objects.get(username="newbie")
        assert user.check_password(STRONG)
        assert user.password != STRONG
        log = AuditLog.objects.get(
            model_label="users.User", object_id=str(user.pk), action="create"
        )
        assert log.user_id == sysmgr.pk
        assert "password" not in str(log.changes) and STRONG not in str(log.changes)

    def test_create_validates_password(self, sysmgr):
        c = _client(sysmgr)
        for bad in ("short1!", "password123", "12345678901234"):
            r = c.post(USERS, _payload(password=bad), format="json")
            assert r.status_code == 400 and "password" in r.data, bad
        r = c.post(USERS, _payload(password=""), format="json")
        assert r.status_code == 400 and "password" in r.data
        r = c.post(
            USERS,
            {k: v for k, v in _payload().items() if k != "password"},
            format="json",
        )
        assert r.status_code == 400 and "password" in r.data
        assert not User.objects.filter(username="newbie").exists()

    def test_create_rejects_password_similar_to_username(self, sysmgr):
        r = _client(sysmgr).post(
            USERS, _payload(username="newbie", password="newbie123"), format="json"
        )
        assert r.status_code == 400 and "password" in r.data

    def test_duplicate_username_and_email_case_insensitive(self, sysmgr):
        c = _client(sysmgr)
        assert c.post(USERS, _payload(), format="json").status_code == 201
        r = c.post(
            USERS, _payload(username="other", email="NEWBIE@example.com"), format="json"
        )
        assert r.status_code == 400 and "email" in r.data
        r = c.post(USERS, _payload(email="x@example.com"), format="json")
        assert r.status_code == 400 and "username" in r.data

    def test_unsupported_language_rejected(self, sysmgr):
        r = _client(sysmgr).post(USERS, _payload(language="xx"), format="json")
        assert r.status_code == 400 and "language" in r.data

    def test_is_superuser_and_staff_never_writable(self, sysmgr):
        c = _client(sysmgr)
        r = c.post(USERS, _payload(is_superuser=True, is_staff=True), format="json")
        assert r.status_code == 201
        u = User.objects.get(username="newbie")
        assert not u.is_superuser and not u.is_staff
        r = c.patch(
            f"{USERS}{u.pk}/", {"is_superuser": True, "is_staff": True}, format="json"
        )
        assert r.status_code == 200
        u.refresh_from_db()
        assert not u.is_superuser and not u.is_staff

    def test_patch_and_put_update_with_audit_diff(self, sysmgr):
        c = _client(sysmgr)
        u = _user("target")
        r = c.patch(
            f"{USERS}{u.pk}/", {"first_name": "Zed", "language": "ta"}, format="json"
        )
        assert r.status_code == 200
        u.refresh_from_db()
        assert (u.first_name, u.language) == ("Zed", "ta")
        log = AuditLog.objects.filter(
            model_label="users.User", object_id=str(u.pk), action="update"
        ).latest("id")
        assert log.changes["first_name"] == {"from": "", "to": "Zed"}
        # PUT behaves as partial (the generic form omits read-only keys).
        r = c.put(
            f"{USERS}{u.pk}/",
            {"email": "t2@example.com", "first_name": "Zee"},
            format="json",
        )
        assert r.status_code == 200, r.data
        u.refresh_from_db()
        assert u.email == "t2@example.com" and u.first_name == "Zee"

    def test_username_immutable(self, sysmgr):
        u = _user("target")
        r = _client(sysmgr).patch(
            f"{USERS}{u.pk}/", {"username": "renamed"}, format="json"
        )
        assert r.status_code == 400 and "username" in r.data
        # Re-sending the same username is harmless.
        r = _client(sysmgr).patch(
            f"{USERS}{u.pk}/", {"username": "target"}, format="json"
        )
        assert r.status_code == 200

    def test_password_not_accepted_on_update(self, sysmgr):
        u = _user("target")
        r = _client(sysmgr).patch(
            f"{USERS}{u.pk}/", {"password": STRONG + "9"}, format="json"
        )
        assert r.status_code == 400 and "password" in r.data
        u.refresh_from_db()
        assert u.check_password(STRONG)

    def test_list_search_filter_and_order(self, sysmgr):
        _user("alice")
        b = _user("bob")
        b.is_active = False
        b.save()
        c = _client(sysmgr)
        r = c.get(USERS, {"search": "alic"})
        assert [x["username"] for x in r.data["results"]] == ["alice"]
        r = c.get(USERS, {"is_active": "false"})
        assert [x["username"] for x in r.data["results"]] == ["bob"]
        names = [x["username"] for x in c.get(USERS).data["results"]]
        assert names == sorted(names)


# --------------------------------------------------------------------------- roles
@pytest.mark.django_db
class TestRoles:
    def test_assign_replace_and_clear_roles(self, sysmgr):
        c = _client(sysmgr)
        u = _user("target")
        sm = Role.objects.get(name="Stock Manager").pk
        am = Role.objects.get(name="Accounts Manager").pk
        r = c.patch(f"{USERS}{u.pk}/", {"role_ids": [sm, am]}, format="json")
        assert r.status_code == 200
        assert sorted(r.data["role_ids"]) == sorted([sm, am])
        r = c.patch(f"{USERS}{u.pk}/", {"role_ids": [am]}, format="json")
        assert r.data["roles"] == ["Accounts Manager"]
        r = c.patch(f"{USERS}{u.pk}/", {"role_ids": []}, format="json")
        assert r.data["roles"] == []
        assert not UserRole.objects.filter(user=u).exists()
        # role changes are in the audit trail
        assert any(
            "role_ids" in log.changes
            for log in AuditLog.objects.filter(
                model_label="users.User", object_id=str(u.pk)
            )
        )

    def test_unknown_role_id_is_400(self, sysmgr):
        u = _user("target")
        r = _client(sysmgr).patch(
            f"{USERS}{u.pk}/", {"role_ids": [999999]}, format="json"
        )
        assert r.status_code == 400

    def test_cannot_edit_own_roles(self, sysmgr):
        r = _client(sysmgr).patch(
            f"{USERS}{sysmgr.pk}/",
            {"role_ids": [Role.objects.get(name="Stock Manager").pk]},
            format="json",
        )
        assert r.status_code == 400 and "role_ids" in r.data
        assert list(sysmgr.user_roles.values_list("role__name", flat=True)) == [
            "System Manager"
        ]

    def test_resending_own_roles_unchanged_is_ok(self, sysmgr):
        ids = list(sysmgr.user_roles.values_list("role_id", flat=True))
        r = _client(sysmgr).patch(
            f"{USERS}{sysmgr.pk}/", {"role_ids": ids, "first_name": "Me"}, format="json"
        )
        assert r.status_code == 200

    def test_superuser_cannot_edit_own_roles_either(self, seeded):
        root = _user("root", superuser=True)
        r = _client(root).patch(
            f"{USERS}{root.pk}/",
            {"role_ids": [Role.objects.get(name="Stock Manager").pk]},
            format="json",
        )
        assert r.status_code == 400

    def test_user_write_without_userrole_right_cannot_assign_roles(self, seeded):
        from users.models import RolePermission

        role = Role.objects.create(name="User Editor")
        RolePermission.objects.create(
            role=role,
            model_label="users.User",
            can_read=True,
            can_write=True,
            can_create=True,
        )
        editor = _user("editor")
        UserRole.objects.create(user=editor, role=role)
        target = _user("target")
        c = _client(editor)
        sm = Role.objects.get(name="System Manager").pk
        assert (
            c.patch(
                f"{USERS}{target.pk}/", {"role_ids": [sm]}, format="json"
            ).status_code
            == 403
        )
        assert c.post(USERS, _payload(role_ids=[sm]), format="json").status_code == 403
        assert not UserRole.objects.filter(role_id=sm, user=target).exists()
        # Editing the profile (no role change) is still allowed.
        assert (
            c.patch(
                f"{USERS}{target.pk}/", {"first_name": "Ok"}, format="json"
            ).status_code
            == 200
        )

    def test_system_manager_cannot_touch_a_superuser(self, sysmgr):
        root = _user("root", superuser=True)
        c = _client(sysmgr)
        assert c.get(f"{USERS}{root.pk}/").status_code == 200
        assert (
            c.patch(
                f"{USERS}{root.pk}/", {"email": "evil@example.com"}, format="json"
            ).status_code
            == 403
        )
        assert (
            c.post(
                f"{USERS}{root.pk}/set-password/",
                {"new_password": STRONG + "1"},
                format="json",
            ).status_code
            == 403
        )
        assert c.post(f"{USERS}{root.pk}/deactivate/").status_code == 403
        root.refresh_from_db()
        assert (
            root.email == "root@example.com"
            and root.is_active
            and root.check_password(STRONG)
        )

    def test_superuser_can_manage_another_superuser(self, seeded):
        root, other = _user("root", superuser=True), _user("root2", superuser=True)
        r = _client(root).patch(
            f"{USERS}{other.pk}/", {"first_name": "Hi"}, format="json"
        )
        assert r.status_code == 200


# --------------------------------------------------------------------------- deactivate
@pytest.mark.django_db
class TestDeactivate:
    def test_deactivate_and_activate_with_audit(self, sysmgr):
        c = _client(sysmgr)
        u = _user("target")
        r = c.post(f"{USERS}{u.pk}/deactivate/")
        assert r.status_code == 200 and r.data["is_active"] is False
        u.refresh_from_db()
        assert not u.is_active
        r = c.post(f"{USERS}{u.pk}/activate/")
        assert r.status_code == 200 and r.data["is_active"] is True
        logs = AuditLog.objects.filter(model_label="users.User", object_id=str(u.pk))
        assert any(log.changes.get("is_active", {}).get("to") is False for log in logs)

    def test_deactivated_user_cannot_log_in_or_use_token(self, sysmgr):
        u = _user("target")
        anon = APIClient()
        token = anon.post(
            "/api/auth/token/",
            {"username": "target", "password": STRONG},
            format="json",
        )
        assert token.status_code == 200
        _client(sysmgr).post(f"{USERS}{u.pk}/deactivate/")
        cache.clear()
        assert (
            anon.post(
                "/api/auth/token/",
                {"username": "target", "password": STRONG},
                format="json",
            ).status_code
            == 401
        )
        bearer = APIClient()
        bearer.credentials(HTTP_AUTHORIZATION=f"Bearer {token.data['access']}")
        assert bearer.get("/api/auth/me/").status_code == 401

    def test_cannot_deactivate_self(self, sysmgr):
        c = _client(sysmgr)
        assert c.post(f"{USERS}{sysmgr.pk}/deactivate/").status_code == 400
        assert (
            c.patch(
                f"{USERS}{sysmgr.pk}/", {"is_active": False}, format="json"
            ).status_code
            == 400
        )
        sysmgr.refresh_from_db()
        assert sysmgr.is_active

    def test_superuser_cannot_deactivate_self(self, seeded):
        root = _user("root", superuser=True)
        assert _client(root).post(f"{USERS}{root.pk}/deactivate/").status_code == 400

    def test_deactivate_via_patch(self, sysmgr):
        u = _user("target")
        r = _client(sysmgr).patch(
            f"{USERS}{u.pk}/", {"is_active": False}, format="json"
        )
        assert r.status_code == 200 and r.data["is_active"] is False


# --------------------------------------------------------------------------- passwords
@pytest.mark.django_db
class TestPasswords:
    def test_set_password_admin(self, sysmgr):
        u = _user("target")
        new = "Brand-new#Pass77"
        r = _client(sysmgr).post(
            f"{USERS}{u.pk}/set-password/", {"new_password": new}, format="json"
        )
        assert r.status_code == 200
        u.refresh_from_db()
        assert u.check_password(new) and not u.check_password(STRONG)
        log = AuditLog.objects.filter(
            model_label="users.User", object_id=str(u.pk)
        ).latest("id")
        assert new not in str(log.changes)

    def test_set_password_validates(self, sysmgr):
        u = _user("target")
        r = _client(sysmgr).post(
            f"{USERS}{u.pk}/set-password/", {"new_password": "123"}, format="json"
        )
        assert r.status_code == 400 and "new_password" in r.data
        r = _client(sysmgr).post(f"{USERS}{u.pk}/set-password/", {}, format="json")
        assert r.status_code == 400
        u.refresh_from_db()
        assert u.check_password(STRONG)

    def test_set_password_on_self_refused(self, sysmgr):
        r = _client(sysmgr).post(
            f"{USERS}{sysmgr.pk}/set-password/",
            {"new_password": STRONG + "1"},
            format="json",
        )
        assert r.status_code == 400

    def test_change_own_password(self, clerk):
        c = _client(clerk)
        new = "Another#Strong88"
        r = c.post(
            "/api/auth/me/change-password/",
            {"old_password": STRONG, "new_password": new},
            format="json",
        )
        assert r.status_code == 200
        clerk.refresh_from_db()
        assert clerk.check_password(new)
        assert AuditLog.objects.filter(
            model_label="users.User", object_id=str(clerk.pk)
        ).exists()
        tok = APIClient().post(
            "/api/auth/token/", {"username": "clerk", "password": new}, format="json"
        )
        assert tok.status_code == 200

    def test_change_own_password_wrong_old(self, clerk):
        r = _client(clerk).post(
            "/api/auth/me/change-password/",
            {"old_password": "nope", "new_password": "Another#Strong88"},
            format="json",
        )
        assert r.status_code == 400 and "old_password" in r.data
        clerk.refresh_from_db()
        assert clerk.check_password(STRONG)

    def test_change_own_password_weak_or_same(self, clerk):
        c = _client(clerk)
        r = c.post(
            "/api/auth/me/change-password/",
            {"old_password": STRONG, "new_password": "abc"},
            format="json",
        )
        assert r.status_code == 400 and "new_password" in r.data
        r = c.post(
            "/api/auth/me/change-password/",
            {"old_password": STRONG, "new_password": STRONG},
            format="json",
        )
        assert r.status_code == 400 and "new_password" in r.data

    def test_change_password_requires_auth(self, seeded):
        r = APIClient().post(
            "/api/auth/me/change-password/",
            {"old_password": "a", "new_password": "b"},
            format="json",
        )
        assert r.status_code == 401

    def test_change_password_is_throttled(self, clerk):
        c = _client(clerk)
        codes = [
            c.post(
                "/api/auth/me/change-password/",
                {"old_password": "wrong", "new_password": "Whatever#123x"},
                format="json",
            ).status_code
            for _ in range(12)
        ]
        assert 429 in codes


# --------------------------------------------------------------------------- permissions API
@pytest.mark.django_db
class TestRolePermissionEditing:
    def test_system_manager_edits_permissions_and_it_is_audited(self, sysmgr):
        from users.models import RolePermission

        c = _client(sysmgr)
        role = Role.objects.create(name="Auditor")
        r = c.post(
            "/api/auth/permissions/",
            {"role": role.pk, "model_label": "stock.Item", "can_read": True},
            format="json",
        )
        assert r.status_code == 201, r.data
        pk = r.data["id"]
        assert (
            c.patch(
                f"/api/auth/permissions/{pk}/", {"can_write": True}, format="json"
            ).status_code
            == 200
        )
        assert RolePermission.objects.get(pk=pk).can_write
        actions = set(
            AuditLog.objects.filter(
                model_label="users.RolePermission", object_id=str(pk)
            ).values_list("action", flat=True)
        )
        assert {"create", "update"} <= actions

    def test_unknown_model_label_rejected(self, sysmgr):
        role = Role.objects.create(name="Auditor")
        r = _client(sysmgr).post(
            "/api/auth/permissions/",
            {"role": role.pk, "model_label": "stock.Nope", "can_read": True},
            format="json",
        )
        assert r.status_code == 400 and "model_label" in r.data

    def test_normal_user_cannot_edit_permissions(self, clerk):
        role = Role.objects.create(name="Auditor")
        r = _client(clerk).post(
            "/api/auth/permissions/",
            {"role": role.pk, "model_label": "stock.Item", "can_read": True},
            format="json",
        )
        assert r.status_code == 403
