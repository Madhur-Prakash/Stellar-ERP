"""Tests for the test-suite's own safety nets.

A guard that never fires is worse than no guard: it gives false confidence and
costs maintenance. These tests prove the lazy-load detector actually triggers on
the bug it was written for, and stays quiet on the legitimate cases that made the
first two versions of it unusable.

The bug being guarded: traversing an unloaded relationship in async SQLAlchemy
raises ``MissingGreenlet``, whose message names neither the relationship nor the
attribute. It cost real debugging time in the sales module.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.organizations.models import Organization
from app.modules.rbac.models import Role
from tests.conftest import LazyLoadDetected


class TestLazyLoadGuard:
    async def test_fires_on_an_unloaded_relationship(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        """The actual bug: traverse a relationship that was never eager-loaded.

        Without the guard this is a ``MissingGreenlet`` with no indication of which
        attribute caused it.
        """
        # Fetch a role with no loader options, so `role.members` is unloaded.
        role = (
            await db.execute(select(Role).where(Role.organization_id == organization.id).limit(1))
        ).scalar_one()
        db.expunge_all()
        role = (await db.execute(select(Role).where(Role.id == role.id))).scalar_one()

        with pytest.raises(LazyLoadDetected, match="Implicit lazy load on Role"):
            _ = role.members

    async def test_quiet_for_eager_loads(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        """`selectinload` must pass through untouched, or every repository breaks."""
        from sqlalchemy.orm import selectinload

        role = (
            await db.execute(
                select(Role)
                .where(Role.organization_id == organization.id)
                .options(selectinload(Role.members))
                .limit(1)
            )
        ).scalar_one()

        assert role.members is not None  # no exception

    async def test_quiet_for_awaited_delete_cascade(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        """`await session.delete()` legitimately lazy-loads to apply cascades.

        It is a coroutine precisely so that load can happen inside greenlet
        context. Flagging it produced seven false positives in the first version of
        the guard, which is why `in_greenlet()` is part of the check.
        """
        role = Role(
            organization_id=organization.id,
            name="Disposable",
            slug=f"disposable-{uuid.uuid4().hex[:6]}",
            permissions=[],
        )
        db.add(role)
        await db.flush()

        await db.delete(role)  # must not raise
        await db.flush()

    async def test_quiet_for_non_select_statements(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        """An UPDATE has no load options at all.

        Reading `lazy_loaded_from` on a non-SELECT raises
        `InvalidRequestError: This ORM execution is not against a SELECT statement`,
        which broke six unrelated tests until the hook checked `is_select` first.
        """
        from sqlalchemy import update

        await db.execute(
            update(Organization)
            .where(Organization.id == organization.id)
            .values(updated_at=dt.datetime.now(dt.UTC))
        )  # must not raise


class TestRequestSchemasKeepEnums:
    """Guards the second bug: enum fields becoming plain strings.

    `use_enum_values=True` on the *request* base turned every validated enum into
    a `str`, so `data.method.is_cash` raised AttributeError - the enum helpers
    vanished exactly where services use them.
    """

    def test_request_enums_stay_enum_members(self) -> None:
        from decimal import Decimal

        from app.modules.sales.models import PaymentMethod
        from app.modules.sales.schemas import PaymentCreate

        payload = PaymentCreate(
            customer_id=uuid.uuid4(), amount=Decimal("100"), method=PaymentMethod.CASH
        )
        assert isinstance(payload.method, PaymentMethod)
        # The property that broke: available only on a real enum member.
        assert payload.method.is_cash is True

    def test_response_enums_serialise_to_values(self) -> None:
        """`ResponseSchema` keeps `use_enum_values`, so JSON carries stable values
        rather than Python member names."""
        from app.core.schemas import ResponseSchema
        from app.modules.sales.models import InvoiceStatus

        class Probe(ResponseSchema):
            status: InvoiceStatus

        assert Probe(status=InvoiceStatus.PARTIALLY_PAID).status == "partially_paid"


class TestNoBuiltinShadowing:
    """Guards the third bug, in addition to ruff's `A` rule.

    A method named `list` shadows the builtin inside its own class body, so a
    sibling annotation `list[Invoice]` resolves to the method. mypy reports it as
    "Function ... is not valid as a type", which points nowhere useful.
    """

    def test_service_classes_do_not_shadow_builtins(self) -> None:
        import builtins
        import inspect

        from app.modules.sales import invoicing, service

        shadowed: list[str] = []
        for module in (service, invoicing):
            for class_name, cls in inspect.getmembers(module, inspect.isclass):
                if cls.__module__ != module.__name__:
                    continue
                for member_name, _ in inspect.getmembers(cls, inspect.isfunction):
                    if hasattr(builtins, member_name) and not member_name.startswith("_"):
                        shadowed.append(f"{class_name}.{member_name}")

        assert not shadowed, f"methods shadowing builtins: {shadowed}"
