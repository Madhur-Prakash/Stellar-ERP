"""Model registry - imports every ORM class exactly once.

Two things depend on this module existing:

1. **Alembic autogenerate.** ``Base.metadata`` only knows about tables whose
   classes have been imported. Anything missing here is silently omitted from
   migrations - the single most common cause of "the migration is empty".
2. **Mapper configuration.** Relationships are declared with string targets
   (``"OrganizationMember"``) to avoid circular imports between modules. They
   resolve on first use, which fails unless every class is registered.

Adding a model? Import it here in the same commit.
"""

from __future__ import annotations

from app.db.base import Base
from app.modules.accounting.models import (
    Account,
    AccountingPeriod,
    AccountSubtype,
    AccountType,
    BalanceSide,
    EntryStatus,
    FiscalYear,
    Journal,
    JournalEntry,
    JournalEntryLine,
    JournalType,
    NumberSequence,
    PeriodStatus,
)
from app.modules.audit.models import AuditAction, AuditLog, AuditSeverity
from app.modules.auth.models import LoginMethod, SessionRevocationReason, UserSession
from app.modules.billing.models import (
    BankAccountDetail,
    CardKind,
    CardNetwork,
    PaymentCard,
)
from app.modules.ocr.engines import DocumentFormat
from app.modules.ocr.models import Document, DocumentKind, DocumentStatus
from app.modules.organizations.models import (
    Invitation,
    InvitationStatus,
    MemberStatus,
    Organization,
    OrganizationMember,
    OrganizationPlan,
)
from app.modules.purchasing.models import (
    Bill,
    BillLine,
    BillStatus,
    GoodsReceipt,
    GoodsReceiptLine,
    GoodsReceiptStatus,
    MovementKind,
    Product,
    ProductKind,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
    StockLevel,
    StockMovement,
    Supplier,
    SupplierPayment,
    SupplierPaymentAllocation,
    Warehouse,
)
from app.modules.rbac.models import Role
from app.modules.sales.models import (
    Customer,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    Lead,
    LeadStatus,
    Payment,
    PaymentAllocation,
    PaymentMethod,
    Quotation,
    QuotationLine,
    QuotationStatus,
    SalesOrder,
    SalesOrderLine,
    SalesOrderStatus,
)
from app.modules.users.models import User

__all__ = [
    "Account",
    "AccountSubtype",
    "AccountType",
    "AccountingPeriod",
    "AuditAction",
    "AuditLog",
    "AuditSeverity",
    "BalanceSide",
    "BankAccountDetail",
    "Base",
    "Bill",
    "BillLine",
    "BillStatus",
    "CardKind",
    "CardNetwork",
    "Customer",
    "Document",
    "DocumentFormat",
    "DocumentKind",
    "DocumentStatus",
    "EntryStatus",
    "FiscalYear",
    "GoodsReceipt",
    "GoodsReceiptLine",
    "GoodsReceiptStatus",
    "Invitation",
    "InvitationStatus",
    "Invoice",
    "InvoiceLine",
    "InvoiceStatus",
    "Journal",
    "JournalEntry",
    "JournalEntryLine",
    "JournalType",
    "Lead",
    "LeadStatus",
    "LoginMethod",
    "MemberStatus",
    "MovementKind",
    "NumberSequence",
    "Organization",
    "OrganizationMember",
    "OrganizationPlan",
    "Payment",
    "PaymentAllocation",
    "PaymentCard",
    "PaymentMethod",
    "PeriodStatus",
    "Product",
    "ProductKind",
    "PurchaseOrder",
    "PurchaseOrderLine",
    "PurchaseOrderStatus",
    "Quotation",
    "QuotationLine",
    "QuotationStatus",
    "Role",
    "SalesOrder",
    "SalesOrderLine",
    "SalesOrderStatus",
    "SessionRevocationReason",
    "StockLevel",
    "StockMovement",
    "Supplier",
    "SupplierPayment",
    "SupplierPaymentAllocation",
    "User",
    "UserSession",
    "Warehouse",
]
