import '../core/api_client.dart';
import '../models/inventory.dart';
import '../models/json.dart';
import '../models/page.dart';
import '../models/sales.dart';

/// Purchasing and inventory bindings.
class InventoryApi {
  const InventoryApi(this._client);

  final ApiClient _client;

  // --- Suppliers ---
  Future<Paged<Supplier>> suppliers({int page = 1, int pageSize = 25}) async =>
      Paged<Supplier>.fromJson(
        await _client.get<Json>(
          '/suppliers',
          query: <String, dynamic>{'page': page, 'page_size': pageSize},
        ),
        Supplier.fromJson,
      );

  /// Create a supplier.
  ///
  /// **`city`, not `billing_city`** - a supplier has one address, unlike a
  /// customer. The schema is `extra="forbid"`, so the wrong key is a 422.
  Future<CreatedParty> createSupplier({
    required String name,
    String? gstin,
    String? email,
    String? phone,
    String? city,
    int paymentTermsDays = 30,
  }) async => CreatedParty.fromJson(
    await _client.post<Json>(
      '/suppliers',
      body: <String, dynamic>{
        'name': name,
        if (gstin != null && gstin.isNotEmpty) 'gstin': gstin,
        if (email != null && email.isNotEmpty) 'email': email,
        if (phone != null && phone.isNotEmpty) 'phone': phone,
        if (city != null && city.isNotEmpty) 'city': city,
        'payment_terms_days': paymentTermsDays,
      },
    ),
  );

  // --- Products ---
  Future<Paged<Product>> products({
    int page = 1,
    int pageSize = 25,
    String? query,
  }) async => Paged<Product>.fromJson(
    await _client.get<Json>(
      '/products',
      query: <String, dynamic>{'page': page, 'page_size': pageSize, 'q': query},
    ),
    Product.fromJson,
  );

  Future<Product> createProduct({
    required String name,
    String? sku,
    String? barcode,
    String? hsnCode,
    String unit = 'pcs',
    String taxRate = '0',
    String salePrice = '0',
    String purchasePrice = '0',
    String reorderLevel = '0',
  }) async => Product.fromJson(
    await _client.post<Json>(
      '/products',
      body: <String, dynamic>{
        'name': name,
        if (sku != null && sku.isNotEmpty) 'sku': sku,
        if (barcode != null && barcode.isNotEmpty) 'barcode': barcode,
        if (hsnCode != null && hsnCode.isNotEmpty) 'hsn_code': hsnCode,
        'unit': unit,
        'tax_rate': taxRate,
        'sale_price': salePrice,
        'purchase_price': purchasePrice,
        'reorder_level': reorderLevel,
      },
    ),
  );

  /// Update a product.
  ///
  /// `sku` is deliberately absent: the update schema does not accept it, because a
  /// code already printed on a label or quoted on a bill should not silently change.
  Future<Product> updateProduct(
    String id, {
    String? name,
    String? barcode,
    String? hsnCode,
    String? unit,
    String? taxRate,
    String? salePrice,
    String? purchasePrice,
    String? reorderLevel,
    bool? isActive,
  }) async => Product.fromJson(
    await _client.patch<Json>(
      '/products/$id',
      body: <String, dynamic>{
        'name': ?name,
        if (barcode != null && barcode.isNotEmpty) 'barcode': barcode,
        if (hsnCode != null && hsnCode.isNotEmpty) 'hsn_code': hsnCode,
        'unit': ?unit,
        'tax_rate': ?taxRate,
        'sale_price': ?salePrice,
        'purchase_price': ?purchasePrice,
        'reorder_level': ?reorderLevel,
        'is_active': ?isActive,
      },
    ),
  );

  Future<Product> byBarcode(String barcode) async => Product.fromJson(
    await _client.get<Json>(
      '/products/by-barcode/${Uri.encodeComponent(barcode)}',
    ),
  );

  Future<List<ReorderRow>> reorderReport() async {
    final List<dynamic> raw = await _client.get<List<dynamic>>(
      '/products/reorder',
    );
    return raw.cast<Json>().map(ReorderRow.fromJson).toList(growable: false);
  }

  // --- Warehouses and stock ---
  Future<List<Warehouse>> warehouses() async {
    final List<dynamic> raw = await _client.get<List<dynamic>>(
      '/inventory/warehouses',
    );
    return raw.cast<Json>().map(Warehouse.fromJson).toList(growable: false);
  }

  Future<Warehouse> createWarehouse({
    required String code,
    required String name,
    bool isDefault = false,
  }) async => Warehouse.fromJson(
    await _client.post<Json>(
      '/inventory/warehouses',
      body: <String, dynamic>{
        'code': code,
        'name': name,
        'is_default': isDefault,
      },
    ),
  );

  Future<List<StockLevel>> levels() async {
    final List<dynamic> raw = await _client.get<List<dynamic>>(
      '/inventory/levels',
    );
    return raw.cast<Json>().map(StockLevel.fromJson).toList(growable: false);
  }

  Future<Paged<StockMovement>> movements({
    int page = 1,
    int pageSize = 25,
  }) async => Paged<StockMovement>.fromJson(
    await _client.get<Json>(
      '/inventory/movements',
      query: <String, dynamic>{'page': page, 'page_size': pageSize},
    ),
    StockMovement.fromJson,
  );

  Future<StockValuation> valuation() async =>
      StockValuation.fromJson(await _client.get<Json>('/inventory/valuation'));

  /// The API takes a signed delta; the form asks a direction and a positive number,
  /// because "-5" typed into a quantity box is easy to get backwards.
  Future<StockMovement> adjust({
    required String productId,
    required String quantityDelta,
    required String reason,
    String? warehouseId,
  }) async => StockMovement.fromJson(
    await _client.post<Json>(
      '/inventory/adjust',
      body: <String, dynamic>{
        'product_id': productId,
        'quantity_delta': quantityDelta,
        'reason': reason,
        if (warehouseId != null && warehouseId.isNotEmpty)
          'warehouse_id': warehouseId,
      },
    ),
  );

  Future<List<StockMovement>> transfer({
    required String productId,
    required String fromWarehouseId,
    required String toWarehouseId,
    required String quantity,
  }) async {
    final List<dynamic> raw = await _client.post<List<dynamic>>(
      '/inventory/transfer',
      body: <String, dynamic>{
        'product_id': productId,
        'from_warehouse_id': fromWarehouseId,
        'to_warehouse_id': toWarehouseId,
        'quantity': quantity,
      },
    );
    return raw.cast<Json>().map(StockMovement.fromJson).toList(growable: false);
  }

  // --- Bills and payables ---
  Future<Paged<Bill>> bills({int page = 1, int pageSize = 25}) async =>
      Paged<Bill>.fromJson(
        await _client.get<Json>(
          '/bills',
          query: <String, dynamic>{'page': page, 'page_size': pageSize},
        ),
        Bill.fromJson,
      );

  Future<Bill> postBill(String id) async =>
      Bill.fromJson(await _client.post<Json>('/bills/$id/post'));

  Future<Ageing> payablesAgeing() async =>
      Ageing.fromJson(await _client.get<Json>('/bills/ageing'));
}
