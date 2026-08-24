"""Unit tests for the permission catalogue and wildcard expansion.

These guard the authorization model's core invariants. A bug in
:func:`expand_grants` is a silent privilege escalation or a silent lockout, so
the edge cases are worth pinning down explicitly.
"""

from __future__ import annotations

import pytest

from app.modules.rbac.permissions import (
    ALL_PERMISSION_VALUES,
    PERMISSION_GROUPS,
    SYSTEM_ROLE_DESCRIPTIONS,
    SYSTEM_ROLE_PERMISSIONS,
    Permission,
    SystemRole,
    expand_grants,
    has_permission,
    is_valid_grant,
)


class TestCatalogue:
    def test_every_slug_is_resource_colon_action(self) -> None:
        for permission in Permission:
            assert permission.value.count(":") == 1
            assert permission.resource and permission.action

    def test_slugs_are_unique(self) -> None:
        values = [p.value for p in Permission]
        assert len(values) == len(set(values))

    def test_no_permission_uses_a_wildcard_in_its_slug(self) -> None:
        """Wildcards belong in role grants, never in the catalogue itself."""
        assert all("*" not in p.value for p in Permission)


class TestGrantValidation:
    @pytest.mark.parametrize(
        "grant", ["*:*", "invoice:*", "invoice:read", "member:invite", "audit:export"]
    )
    def test_accepts_valid_grants(self, grant: str) -> None:
        assert is_valid_grant(grant)

    @pytest.mark.parametrize(
        "grant",
        [
            "invoces:*",  # typo'd resource
            "bogus:read",
            "invoice:destroy",  # real resource, unimplemented action
            "invoice",  # missing action
            "*",  # not the full wildcard
            "",
        ],
    )
    def test_rejects_invalid_grants(self, grant: str) -> None:
        assert not is_valid_grant(grant)


class TestExpansion:
    def test_full_wildcard_expands_to_everything(self) -> None:
        assert expand_grants(["*:*"]) == ALL_PERMISSION_VALUES

    def test_resource_wildcard_expands_to_that_resource_only(self) -> None:
        expanded = expand_grants(["invoice:*"])
        assert expanded == {
            Permission.INVOICE_READ.value,
            Permission.INVOICE_WRITE.value,
            Permission.INVOICE_APPROVE.value,
        }

    def test_concrete_grants_pass_through(self) -> None:
        assert expand_grants(["invoice:read", "member:read"]) == {
            "invoice:read",
            "member:read",
        }

    def test_unknown_grants_are_dropped_not_kept(self) -> None:
        """A permission removed from the catalogue must stop granting access.

        Silently retaining it would leave a stale grant working forever.
        """
        assert expand_grants(["bogus:read", "invoice:read"]) == {"invoice:read"}

    def test_empty_grants_expand_to_nothing(self) -> None:
        assert expand_grants([]) == frozenset()

    def test_mixed_wildcard_and_concrete_are_unioned(self) -> None:
        expanded = expand_grants(["invoice:*", "member:read"])
        assert "invoice:approve" in expanded
        assert "member:read" in expanded
        assert "member:invite" not in expanded

    def test_full_wildcard_short_circuits_other_grants(self) -> None:
        assert expand_grants(["*:*", "bogus:whatever"]) == ALL_PERMISSION_VALUES

    def test_duplicate_grants_are_idempotent(self) -> None:
        assert expand_grants(["invoice:read", "invoice:read"]) == {"invoice:read"}


class TestHasPermission:
    def test_accepts_enum_and_string(self) -> None:
        granted = expand_grants(["invoice:*"])
        assert has_permission(granted, Permission.INVOICE_READ)
        assert has_permission(granted, "invoice:read")

    def test_denies_ungranted(self) -> None:
        granted = expand_grants(["invoice:*"])
        assert not has_permission(granted, Permission.MEMBER_REMOVE)


class TestSystemRoles:
    def test_every_role_has_grants_and_a_description(self) -> None:
        for role in SystemRole:
            assert SYSTEM_ROLE_PERMISSIONS[role]
            assert SYSTEM_ROLE_DESCRIPTIONS[role]

    def test_every_system_grant_is_valid(self) -> None:
        """A typo here would create a permanently dead grant on a seeded role."""
        for role, grants in SYSTEM_ROLE_PERMISSIONS.items():
            invalid = [g for g in grants if not is_valid_grant(str(g))]
            assert not invalid, f"{role.value} has invalid grants: {invalid}"

    def test_owner_has_full_access(self) -> None:
        assert (
            expand_grants([str(g) for g in SYSTEM_ROLE_PERMISSIONS[SystemRole.OWNER]])
            == ALL_PERMISSION_VALUES
        )

    def test_privilege_ordering_is_sane(self) -> None:
        """Roles must form a broadly decreasing privilege ladder."""
        sizes = {
            role: len(expand_grants([str(g) for g in grants]))
            for role, grants in SYSTEM_ROLE_PERMISSIONS.items()
        }
        assert sizes[SystemRole.OWNER] >= sizes[SystemRole.ADMIN]
        assert sizes[SystemRole.ADMIN] > sizes[SystemRole.ACCOUNTANT]
        assert sizes[SystemRole.ACCOUNTANT] > sizes[SystemRole.SALES]

    def test_viewer_is_read_only(self) -> None:
        """The clearest possible statement of least privilege."""
        granted = expand_grants([str(g) for g in SYSTEM_ROLE_PERMISSIONS[SystemRole.VIEWER]])
        writes = {p for p in granted if not p.endswith(("read", "export"))}
        assert not writes, f"viewer holds non-read permissions: {sorted(writes)}"

    def test_only_owner_can_delete_the_organization(self) -> None:
        for role in SystemRole:
            if role is SystemRole.OWNER:
                continue
            granted = expand_grants([str(g) for g in SYSTEM_ROLE_PERMISSIONS[role]])
            assert Permission.ORG_DELETE.value not in granted, role.value

    def test_only_owner_can_touch_billing(self) -> None:
        for role in SystemRole:
            if role is SystemRole.OWNER:
                continue
            granted = expand_grants([str(g) for g in SYSTEM_ROLE_PERMISSIONS[role]])
            assert Permission.ORG_BILLING.value not in granted, role.value


class TestPermissionGroups:
    def test_every_permission_appears_in_exactly_one_group(self) -> None:
        """The UI's picker is built from these groups.

        A permission in no group is invisible to administrators; one in two groups
        renders twice.
        """
        seen: list[str] = []
        for group in PERMISSION_GROUPS:
            seen.extend(p.value for p in group.permissions)

        assert len(seen) == len(set(seen)), "a permission is in more than one group"
        assert set(seen) == ALL_PERMISSION_VALUES, (
            f"ungrouped: {sorted(ALL_PERMISSION_VALUES - set(seen))}"
        )

    def test_group_keys_are_unique(self) -> None:
        keys = [group.key for group in PERMISSION_GROUPS]
        assert len(keys) == len(set(keys))
