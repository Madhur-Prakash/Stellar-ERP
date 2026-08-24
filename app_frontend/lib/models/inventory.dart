import 'json.dart';

/// Purchasing and inventory contracts. Money is always a `String`.
class Supplier {
  const Supplier({
    required this.id,
    required this.code,
    required this.name,
    this.email,
    this.phone,
    this.gstin,
    this.city,
    required this.paymentTermsDays,
  });

  final String id;
  final String code;
  final String name;
  final String? email;
  final String? phone;
  final String? gstin;
  final String? city;
  final int paymentTermsDays;

  factory Supplier.fromJson(Json json) => Supplier(
    id: str(json, 'id'),
    code: strOrNull(json, 'code') ?? '',
    name: str(json, 'name'),
    email: strOrNull(json, 'email'),
    phone: strOrNull(json, 'phone'),
    gstin: strOrNull(json, 'gstin'),
    city: strOrNull(json, 'city'),
    paymentTermsDays: intOf(json, 'payment_terms_days', 30),
  );
}

class Product {
  const Product({
    required this.id,
    required this.sku,
    required this.name,
    this.barcode,
    this.hsnCode,
    required this.kind,
    required this.unit,
    required this.taxRate,
    required this.salePrice,
    required this.purchasePrice,
    required this.reorderLevel,
    required this.isActive,
    required this.tracksStock,
    this.quantityOnHand,
    this.stockValue,
    required this.needsReorder,
  });

  final String id;
  final String sku;
  final String name;
  final String? barcode;
  final String? hsnCode;

  /// `stocked`, `service`, or `consumable`.
  final String kind;
  final String unit;
  final String taxRate;
  final String salePrice;
  final String purchasePrice;
  final String reorderLevel;
  final bool isActive;
  final bool tracksStock;

  /// Present on the list endpoint, which aggregates across warehouses.
  final String? quantityOnHand;
  final String? stockValue;
  final bool needsReorder;

  factory Product.fromJson(Json json) => Product(
    id: str(json, 'id'),
    sku: str(json, 'sku'),
    name: str(json, 'name'),
    barcode: strOrNull(json, 'barcode'),
    hsnCode: strOrNull(json, 'hsn_code'),
    kind: strOrNull(json, 'kind') ?? 'stocked',
    unit: strOrNull(json, 'unit') ?? 'pcs',
    taxRate: money(json, 'tax_rate'),
    salePrice: money(json, 'sale_price'),
    purchasePrice: money(json, 'purchase_price'),
    reorderLevel: money(json, 'reorder_level'),
    isActive: boolOf(json, 'is_active', true),
    tracksStock: boolOf(json, 'tracks_stock'),
    quantityOnHand: moneyOrNull(json, 'quantity_on_hand'),
    stockValue: moneyOrNull(json, 'stock_value'),
    needsReorder: boolOf(json, 'needs_reorder'),
  );
}

class Warehouse {
  const Warehouse({
    required this.id,
    required this.code,
    required this.name,
    required this.isDefault,
  });

  final String id;
  final String code;
  final String name;
  final bool isDefault;

  factory Warehouse.fromJson(Json json) => Warehouse(
    id: str(json, 'id'),
    code: str(json, 'code'),
    name: str(json, 'name'),
    isDefault: boolOf(json, 'is_default'),
  );
}

class StockLevel {
  const StockLevel({
    required this.productId,
    required this.productSku,
    required this.productName,
    required this.warehouseId,
    required this.warehouseCode,
    required this.quantity,
    required this.averageCost,
    required this.totalValue,
  });

  final String productId;
  final String productSku;
  final String productName;
  final String warehouseId;
  final String warehouseCode;
  final String quantity;
  final String averageCost;
  final String totalValue;

  factory StockLevel.fromJson(Json json) => StockLevel(
    productId: str(json, 'product_id'),
    productSku: str(json, 'product_sku'),
    productName: str(json, 'product_name'),
    warehouseId: str(json, 'warehouse_id'),
    warehouseCode: str(json, 'warehouse_code'),
    quantity: money(json, 'quantity'),
    averageCost: money(json, 'average_cost'),
    totalValue: money(json, 'total_value'),
  );
}

class StockMovement {
  const StockMovement({
    required this.id,
    required this.productName,
    required this.kind,
    required this.movementDate,
    required this.quantity,
    required this.totalCost,
    required this.balanceAfter,
    this.journalEntryId,
  });

  final String id;
  final String productName;

  /// `receipt`, `issue`, `adjustment`, `transfer_in`, `transfer_out`,
  /// `return_in`, `return_out`, or `reversal`.
  final String kind;
  final String movementDate;
  final String quantity;
  final String totalCost;
  final String balanceAfter;

  /// Null for a transfer, which moves no value and so correctly posts nothing.
  final String? journalEntryId;

  factory StockMovement.fromJson(Json json) => StockMovement(
    id: str(json, 'id'),
    productName: strOrNull(json, 'product_name') ?? '',
    kind: strOrNull(json, 'kind') ?? 'adjustment',
    movementDate: str(json, 'movement_date'),
    quantity: money(json, 'quantity'),
    totalCost: money(json, 'total_cost'),
    balanceAfter: money(json, 'balance_after'),
    journalEntryId: strOrNull(json, 'journal_entry_id'),
  );
}

class StockValuation {
  const StockValuation({
    required this.asOf,
    required this.totalValue,
    required this.productCount,
  });

  final String asOf;
  final String totalValue;
  final int productCount;

  factory StockValuation.fromJson(Json json) => StockValuation(
    asOf: str(json, 'as_of'),
    totalValue: money(json, 'total_value'),
    productCount: intOf(json, 'product_count'),
  );
}

class ReorderRow {
  const ReorderRow({
    required this.productId,
    required this.sku,
    required this.name,
    required this.quantityOnHand,
    required this.reorderLevel,
    required this.shortfall,
  });

  final String productId;
  final String sku;
  final String name;
  final String quantityOnHand;
  final String reorderLevel;
  final String shortfall;

  factory ReorderRow.fromJson(Json json) => ReorderRow(
    productId: str(json, 'product_id'),
    sku: str(json, 'sku'),
    name: str(json, 'name'),
    quantityOnHand: money(json, 'quantity_on_hand'),
    reorderLevel: money(json, 'reorder_level'),
    shortfall: money(json, 'shortfall'),
  );
}

/// One purchase line as the create endpoints take it.
class PurchaseLineInput {
  const PurchaseLineInput({
    required this.description,
    required this.quantity,
    required this.unitPrice,
    required this.taxRate,
    this.productId,
  });

  final String description;
  final String quantity;
  final String unitPrice;
  final String taxRate;
  final String? productId;

  Map<String, dynamic> toJson() => <String, dynamic>{
    'description': description,
    'quantity': quantity,
    'unit_price': unitPrice,
    'tax_rate': taxRate,
    if (productId != null) 'product_id': productId,
  };
}

class Bill {
  const Bill({
    required this.id,
    required this.billNumber,
    this.supplierInvoiceNumber,
    required this.supplierName,
    required this.billDate,
    required this.dueDate,
    required this.status,
    required this.grandTotal,
    required this.outstanding,
    required this.isOverdue,
  });

  final String id;
  final String billNumber;
  final String? supplierInvoiceNumber;
  final String supplierName;
  final String billDate;
  final String dueDate;

  /// `draft`, `posted`, `partially_paid`, `paid`, or `cancelled`.
  final String status;
  final String grandTotal;
  final String outstanding;
  final bool isOverdue;

  factory Bill.fromJson(Json json) => Bill(
    id: str(json, 'id'),
    billNumber: str(json, 'bill_number'),
    supplierInvoiceNumber: strOrNull(json, 'supplier_invoice_number'),
    supplierName: strOrNull(json, 'supplier_name') ?? '',
    billDate: str(json, 'bill_date'),
    dueDate: str(json, 'due_date'),
    status: strOrNull(json, 'status') ?? 'draft',
    grandTotal: money(json, 'grand_total'),
    outstanding: money(json, 'outstanding'),
    isOverdue: boolOf(json, 'is_overdue'),
  );
}
