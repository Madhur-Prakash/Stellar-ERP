"""Stock service - movements, valuation, and their ledger effect.

Every change to stock goes through :meth:`StockService.record` and nowhere else.
That single entry point is what keeps four things in step that would otherwise
drift:

1. the append-only ``StockMovement`` history,
2. the derived ``StockLevel`` position,
3. the weighted-average cost, and
4. the general ledger.

The locking matters. Two concurrent receipts for the same product read the same
average cost, compute from it, and the second write silently discards the first's
contribution - a lost update that shows up weeks later as an inventory value nobody
can explain. Every ``record`` call therefore takes a row lock on the stock level
before reading it (``SELECT … FOR UPDATE``), so concurrent movements on the same
product serialise and movements on different products do not.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.exceptions import BusinessRuleError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.db.types import ZERO
from app.modules.accounting.coa_template import SystemAccount
from app.modules.accounting.models import JournalType
from app.modules.accounting.repository import JournalRepository
from app.modules.accounting.schemas import JournalEntryCreate, JournalEntryLineInput
from app.modules.accounting.service import ChartOfAccountsService, PostingService
from app.modules.audit.models import AuditAction, AuditSeverity
from app.modules.audit.service import AuditService
from app.modules.purchasing.models import (
    MovementKind,
    Product,
    StockLevel,
    StockMovement,
    Warehouse,
)
from app.modules.purchasing.valuation import (
    NegativeStockError,
    ValuationState,
    apply_issue,
    apply_receipt,
    reverse_receipt,
    round_value,
)
from app.modules.users.models import User

log = get_logger(__name__)


def _audit_ctx(ctx: RequestContext | None) -> dict[str, Any]:
    if ctx is None:
        return {}
    return {"ip_address": ctx.ip_address, "user_agent": ctx.user_agent}


class StockService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.chart = ChartOfAccountsService(session)
        self.posting = PostingService(session)
        self.journals = JournalRepository(session)
        self.audit = AuditService(session)

    # -------------------------------------------------------------------------
    # Warehouses
    # -------------------------------------------------------------------------
    async def default_warehouse(self, organization_id: uuid.UUID) -> Warehouse:
        """The warehouse receipts and issues use when none is named.

        Created on demand so a single-location business never has to think about
        warehouses at all, while the schema still supports many.
        """
        existing = (
            await self.session.execute(
                select(Warehouse).where(
                    Warehouse.organization_id == organization_id,
                    Warehouse.is_default.is_(True),
                    Warehouse.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        warehouse = Warehouse(
            organization_id=organization_id,
            code="MAIN",
            name="Main Warehouse",
            is_default=True,
        )
        self.session.add(warehouse)
        await self.session.flush()
        log.info("created default warehouse", extra={"organization_id": str(organization_id)})
        return warehouse

    async def get_warehouse(
        self, organization_id: uuid.UUID, warehouse_id: uuid.UUID | None
    ) -> Warehouse:
        if warehouse_id is None:
            return await self.default_warehouse(organization_id)

        warehouse = (
            await self.session.execute(
                select(Warehouse).where(
                    Warehouse.id == warehouse_id,
                    Warehouse.organization_id == organization_id,
                    Warehouse.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if warehouse is None:
            raise NotFoundError("Warehouse")
        if not warehouse.is_active:
            raise BusinessRuleError(f"Warehouse {warehouse.code} is inactive.")
        return warehouse

    # -------------------------------------------------------------------------
    # Stock levels
    # -------------------------------------------------------------------------
    async def _locked_level(
        self, organization_id: uuid.UUID, product_id: uuid.UUID, warehouse_id: uuid.UUID
    ) -> StockLevel:
        """Fetch the stock level for update, creating it if absent.

        ``with_for_update`` is the whole point: without it two concurrent receipts
        both read the pre-existing average, and whichever writes second overwrites
        the other's blend. The row lock makes the read-compute-write sequence
        atomic per (product, warehouse).
        """
        level = (
            await self.session.execute(
                select(StockLevel)
                .where(
                    StockLevel.product_id == product_id,
                    StockLevel.warehouse_id == warehouse_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()

        if level is None:
            level = StockLevel(
                organization_id=organization_id,
                product_id=product_id,
                warehouse_id=warehouse_id,
                quantity=ZERO,
                stock_value=ZERO,
                average_cost=ZERO,
            )
            self.session.add(level)
            await self.session.flush()
        return level

    async def level_for(
        self,
        organization_id: uuid.UUID,
        product_id: uuid.UUID,
        warehouse_id: uuid.UUID | None = None,
    ) -> StockLevel | None:
        """Read a position without locking. For reports and availability checks."""
        warehouse = await self.get_warehouse(organization_id, warehouse_id)
        return (
            await self.session.execute(
                select(StockLevel).where(
                    StockLevel.product_id == product_id,
                    StockLevel.warehouse_id == warehouse.id,
                )
            )
        ).scalar_one_or_none()

    # -------------------------------------------------------------------------
    # The single mutation path
    # -------------------------------------------------------------------------
    async def record(
        self,
        organization_id: uuid.UUID,
        *,
        product: Product,
        warehouse: Warehouse,
        kind: MovementKind,
        quantity: Decimal,
        unit_cost: Decimal | None,
        movement_date: dt.date,
        actor: User,
        source_type: str | None = None,
        source_id: uuid.UUID | None = None,
        reference: str | None = None,
        notes: str | None = None,
        journal_entry_id: uuid.UUID | None = None,
        reverses_id: uuid.UUID | None = None,
    ) -> StockMovement:
        """Record one stock movement and update the derived position.

        ``quantity`` is always supplied **positive**; ``kind`` decides the sign. A
        caller passing a negative quantity for an issue would double-negate, so the
        sign is derived here rather than trusted.

        Returns the movement, whose ``balance_after``/``average_cost_after`` capture
        the position at that instant - which is what makes a stock card auditable
        after the average has moved on.
        """
        if quantity <= 0:
            raise ValidationError("Movement quantity must be positive")
        if not product.tracks_stock:
            raise BusinessRuleError(
                f"{product.name} is a {product.kind} and does not carry stock.",
                details={"product_id": str(product.id), "kind": str(product.kind)},
            )

        level = await self._locked_level(organization_id, product.id, warehouse.id)
        current = ValuationState(quantity=level.quantity, value=level.stock_value)

        try:
            if kind.increases_stock:
                # Stated cost, else the running average, else what the product is
                # bought for. Without that last fallback the first stock to enter a
                # new product arrives at zero cost: the stock report shows the units
                # and the balance sheet shows no asset, and because both the ledger
                # and the sub-ledger are zero the control check agrees and never
                # flags it. Opening stock is the common case, so this is the path a
                # first-time user takes.
                cost = unit_cost if unit_cost is not None else current.average_cost
                if not cost:
                    cost = product.purchase_price
                outcome = apply_receipt(current, quantity=quantity, unit_cost=cost)
                new_state = outcome.state
                movement_cost = cost
                total_cost = outcome.value_added
                signed_quantity = quantity
            elif kind is MovementKind.REVERSAL:
                if unit_cost is None:
                    raise ValidationError("A reversal must state the original unit cost")
                new_state = reverse_receipt(current, quantity=quantity, unit_cost=unit_cost)
                movement_cost = unit_cost
                total_cost = round_value(quantity * unit_cost)
                signed_quantity = -quantity
            else:
                issue = apply_issue(current, quantity=quantity)
                new_state = issue.state
                # Issued at the average that applied *before* the issue, which is
                # the cost that goes to COGS.
                movement_cost = current.average_cost
                total_cost = issue.cost_of_goods_sold
                signed_quantity = -quantity
        except NegativeStockError as exc:
            raise BusinessRuleError(
                f"{product.name}: {exc}",
                details={
                    "product_id": str(product.id),
                    "warehouse": warehouse.code,
                    "on_hand": str(current.quantity),
                    "requested": str(quantity),
                },
            ) from exc

        level.quantity = new_state.quantity
        level.stock_value = new_state.value
        # Kept in step for querying and display; the value is what drives arithmetic.
        level.average_cost = new_state.average_cost
        level.last_movement_at = dt.datetime.now(dt.UTC)

        movement = StockMovement(
            organization_id=organization_id,
            product_id=product.id,
            warehouse_id=warehouse.id,
            kind=kind,
            movement_date=movement_date,
            quantity=signed_quantity,
            unit_cost=movement_cost,
            total_cost=total_cost,
            balance_after=new_state.quantity,
            average_cost_after=new_state.average_cost,
            source_type=source_type,
            source_id=source_id,
            reference=reference,
            notes=notes,
            journal_entry_id=journal_entry_id,
            reverses_id=reverses_id,
            created_by_id=actor.id,
        )
        self.session.add(movement)
        await self.session.flush()
        return movement

    # -------------------------------------------------------------------------
    # Operations that also post to the ledger
    # -------------------------------------------------------------------------
    async def issue_for_sale(
        self,
        organization_id: uuid.UUID,
        *,
        product: Product,
        quantity: Decimal,
        movement_date: dt.date,
        actor: User,
        warehouse_id: uuid.UUID | None = None,
        source_type: str = "invoice",
        source_id: uuid.UUID | None = None,
        reference: str | None = None,
        ctx: RequestContext | None = None,
    ) -> tuple[StockMovement, Decimal]:
        """Ship goods and recognise cost of sales.

        The seam sales calls when an invoice for a stocked product is posted:

        =====================  ========  ========
        Account                  Debit    Credit
        =====================  ========  ========
        Cost of Goods Sold        COGS
        Inventory                            COGS
        =====================  ========  ========

        Returns ``(movement, cogs)``. Kept as an explicit call rather than a
        database trigger or an event listener so the posting is visible in the code
        path that causes it.
        """
        warehouse = await self.get_warehouse(organization_id, warehouse_id)
        movement = await self.record(
            organization_id,
            product=product,
            warehouse=warehouse,
            kind=MovementKind.ISSUE,
            quantity=quantity,
            unit_cost=None,  # issues use the prevailing average
            movement_date=movement_date,
            actor=actor,
            source_type=source_type,
            source_id=source_id,
            reference=reference,
        )
        cogs = movement.total_cost

        if cogs > 0:
            entry = await self._post_pair(
                organization_id,
                actor=actor,
                entry_date=movement_date,
                narration=f"Cost of sales - {product.name}"
                + (f" ({reference})" if reference else ""),
                debit_account_id=await self._cogs_account_id(organization_id, product),
                credit_account_id=await self._inventory_account_id(organization_id, product),
                amount=cogs,
                reference=reference,
                source_type=source_type,
                source_id=source_id,
                ctx=ctx,
            )
            movement.journal_entry_id = entry.id
            await self.session.flush()

        return movement, cogs

    async def adjust(
        self,
        organization_id: uuid.UUID,
        *,
        product: Product,
        quantity_delta: Decimal,
        movement_date: dt.date,
        reason: str,
        actor: User,
        warehouse_id: uuid.UUID | None = None,
        unit_cost: Decimal | None = None,
        ctx: RequestContext | None = None,
    ) -> StockMovement:
        """Stock-take correction, posted against a shrinkage expense.

        Audited at warning severity: an adjustment writes value off with no
        commercial document behind it, which is precisely what a reviewer looks for.
        """
        if quantity_delta == 0:
            raise ValidationError("Adjustment cannot be zero")

        warehouse = await self.get_warehouse(organization_id, warehouse_id)
        increase = quantity_delta > 0

        movement = await self.record(
            organization_id,
            product=product,
            warehouse=warehouse,
            kind=MovementKind.ADJUSTMENT if increase else MovementKind.ISSUE,
            quantity=abs(quantity_delta),
            unit_cost=unit_cost,
            movement_date=movement_date,
            actor=actor,
            source_type="stock_adjustment",
            notes=reason,
        )

        value = movement.total_cost
        if value > 0:
            inventory = await self._inventory_account_id(organization_id, product)
            # A write-off is an expense; found stock reduces one.
            shrinkage = await self._cogs_account_id(organization_id, product)
            entry = await self._post_pair(
                organization_id,
                actor=actor,
                entry_date=movement_date,
                narration=f"Stock adjustment - {product.name}: {reason}",
                debit_account_id=inventory if increase else shrinkage,
                credit_account_id=shrinkage if increase else inventory,
                amount=value,
                source_type="stock_adjustment",
                source_id=movement.id,
                ctx=ctx,
            )
            movement.journal_entry_id = entry.id
            await self.session.flush()

        await self.audit.record(
            AuditAction.STOCK_ADJUSTED,
            actor=actor,
            organization_id=organization_id,
            resource_type="product",
            resource_id=product.id,
            summary=f"Adjusted {product.name} by {quantity_delta} in {warehouse.code}: {reason}",
            severity=AuditSeverity.WARNING,
            context={"delta": str(quantity_delta), "value": str(value), "reason": reason},
            **_audit_ctx(ctx),
        )
        return movement

    async def transfer(
        self,
        organization_id: uuid.UUID,
        *,
        product: Product,
        quantity: Decimal,
        from_warehouse_id: uuid.UUID,
        to_warehouse_id: uuid.UUID,
        movement_date: dt.date,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> tuple[StockMovement, StockMovement]:
        """Move stock between warehouses.

        **Posts nothing to the ledger**, deliberately: the goods are still owned and
        still worth the same, so total inventory value is unchanged. Posting a
        transfer would inflate both sides of the books for no economic event.

        The outbound movement's cost carries to the inbound one, so the receiving
        warehouse's average blends at the value the stock actually holds rather than
        at a fresh purchase price.
        """
        if from_warehouse_id == to_warehouse_id:
            raise ValidationError("Source and destination warehouses must differ")

        source = await self.get_warehouse(organization_id, from_warehouse_id)
        destination = await self.get_warehouse(organization_id, to_warehouse_id)

        outbound = await self.record(
            organization_id,
            product=product,
            warehouse=source,
            kind=MovementKind.TRANSFER_OUT,
            quantity=quantity,
            unit_cost=None,
            movement_date=movement_date,
            actor=actor,
            source_type="stock_transfer",
            notes=f"To {destination.code}",
        )
        inbound = await self.record(
            organization_id,
            product=product,
            warehouse=destination,
            kind=MovementKind.TRANSFER_IN,
            quantity=quantity,
            # Carries the outbound cost, so value is conserved across the move.
            unit_cost=outbound.unit_cost,
            movement_date=movement_date,
            actor=actor,
            source_type="stock_transfer",
            source_id=outbound.id,
            notes=f"From {source.code}",
        )

        await self.audit.record(
            AuditAction.STOCK_TRANSFERRED,
            actor=actor,
            organization_id=organization_id,
            resource_type="product",
            resource_id=product.id,
            summary=(
                f"Transferred {quantity} {product.name} from {source.code} to {destination.code}"
            ),
            **_audit_ctx(ctx),
        )
        return outbound, inbound

    # -------------------------------------------------------------------------
    # Account resolution
    # -------------------------------------------------------------------------
    async def _inventory_account_id(
        self, organization_id: uuid.UUID, product: Product
    ) -> uuid.UUID:
        """Product override first, then the organization's default."""
        if product.inventory_account_id is not None:
            return product.inventory_account_id
        account = await self.chart.resolve_system_account(organization_id, SystemAccount.INVENTORY)
        return account.id

    async def _cogs_account_id(self, organization_id: uuid.UUID, product: Product) -> uuid.UUID:
        if product.cogs_account_id is not None:
            return product.cogs_account_id
        account = await self.chart.resolve_system_account(
            organization_id, SystemAccount.COST_OF_GOODS_SOLD
        )
        return account.id

    async def _post_pair(
        self,
        organization_id: uuid.UUID,
        *,
        actor: User,
        entry_date: dt.date,
        narration: str,
        debit_account_id: uuid.UUID,
        credit_account_id: uuid.UUID,
        amount: Decimal,
        reference: str | None = None,
        source_type: str | None = None,
        source_id: uuid.UUID | None = None,
        ctx: RequestContext | None = None,
    ) -> Any:
        """Post a balanced two-line entry to the general journal."""
        journal = await self.journals.get_by_type(organization_id, JournalType.GENERAL)
        if journal is None:
            raise BusinessRuleError("No general journal is configured for this organization.")

        return await self.posting.create_entry(
            organization_id,
            JournalEntryCreate(
                journal_id=journal.id,
                entry_date=entry_date,
                narration=narration,
                reference=reference,
                post=True,
                lines=[
                    JournalEntryLineInput(account_id=debit_account_id, debit=amount),
                    JournalEntryLineInput(account_id=credit_account_id, credit=amount),
                ],
            ),
            actor,
            ctx,
            source_type=source_type,
            source_id=source_id,
        )
