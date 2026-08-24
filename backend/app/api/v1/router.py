"""v1 API router aggregation.

Every module's router is mounted here, and nowhere else. One file answers "what
does this API expose?", and versioning is a matter of adding a ``v2`` package
rather than editing routes in place - existing clients keep working while a new
contract is introduced alongside.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.accounting.router import (
    accounts_router,
    calendar_router,
    entries_router,
    journals_router,
    reports_router,
)
from app.modules.analytics.router import router as analytics_router
from app.modules.audit.router import router as audit_router
from app.modules.auth.router import router as auth_router
from app.modules.billing.router import router as billing_router
from app.modules.ocr.router import router as documents_router
from app.modules.organizations.router import (
    invitations_router,
)
from app.modules.organizations.router import (
    router as organizations_router,
)
from app.modules.purchasing.router import (
    bills_router,
    inventory_router,
    products_router,
    purchase_orders_router,
    receipts_router,
    supplier_payments_router,
    suppliers_router,
)
from app.modules.rbac.router import router as roles_router
from app.modules.sales.router import (
    customers_router,
    invoices_router,
    leads_router,
    orders_router,
    payments_router,
    quotations_router,
)
from app.modules.users.router import router as users_router

api_router = APIRouter()

# Ordered as a reader would explore the API: authenticate, then act.
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(organizations_router)
api_router.include_router(invitations_router)
api_router.include_router(roles_router)
api_router.include_router(audit_router)

# Stage 2 - accounting. Ordered chart -> calendar -> journals -> entries ->
# reports, which is the order the books are actually set up and used.
api_router.include_router(accounts_router)
api_router.include_router(calendar_router)
api_router.include_router(journals_router)
api_router.include_router(entries_router)
api_router.include_router(reports_router)

# Billing - the simple path: money in and money out, no customer or supplier
# needed. Mounted before sales because for most users it is the only screen they
# touch, and these are real ledger postings so everything downstream picks them up.
api_router.include_router(billing_router)

# Sales. Ordered along the document chain: parties, then quote -> order ->
# invoice -> payment, which is the order they are created in practice.
api_router.include_router(customers_router)
api_router.include_router(leads_router)
api_router.include_router(quotations_router)
api_router.include_router(orders_router)
api_router.include_router(invoices_router)
api_router.include_router(payments_router)

# Purchasing and inventory. Masters first, then the document chain:
# PO -> goods receipt -> bill -> payment.
api_router.include_router(suppliers_router)
api_router.include_router(products_router)
api_router.include_router(inventory_router)
api_router.include_router(purchase_orders_router)
api_router.include_router(receipts_router)
api_router.include_router(bills_router)
api_router.include_router(supplier_payments_router)

# Scanned documents. Last because it consumes purchasing: confirming a document
# creates a bill through the same service `POST /bills` uses.
api_router.include_router(documents_router)

# Analytics. Last because it summarises everything above it: the dashboard's
# figures come from the same ReportingService that renders the statements.
api_router.include_router(analytics_router)

__all__ = ["api_router"]
