import '../core/api_client.dart';
import '../models/json.dart';
import '../models/page.dart';
import '../models/sales.dart';

/// Sales bindings.
class SalesApi {
  const SalesApi(this._client);

  final ApiClient _client;

  Future<Paged<Customer>> customers({
    int page = 1,
    int pageSize = 25,
    String? query,
  }) async => Paged<Customer>.fromJson(
    await _client.get<Json>(
      '/customers',
      query: <String, dynamic>{'page': page, 'page_size': pageSize, 'q': query},
    ),
    Customer.fromJson,
  );

  /// Create a customer.
  ///
  /// **`billing_city`, not `city`.** A customer has a billing address and a
  /// separate shipping one, so its column is `billing_city`; a supplier has one
  /// address and uses `city`. Both request schemas are `extra="forbid"`, so sending
  /// the wrong key is a 422 - which is why the two create calls do not share a body
  /// builder.
  Future<CreatedParty> createCustomer({
    required String name,
    String? gstin,
    String? email,
    String? phone,
    String? city,
    int paymentTermsDays = 30,
  }) async => CreatedParty.fromJson(
    await _client.post<Json>(
      '/customers',
      body: <String, dynamic>{
        'name': name,
        if (gstin != null && gstin.isNotEmpty) 'gstin': gstin,
        if (email != null && email.isNotEmpty) 'email': email,
        if (phone != null && phone.isNotEmpty) 'phone': phone,
        if (city != null && city.isNotEmpty) 'billing_city': city,
        'payment_terms_days': paymentTermsDays,
      },
    ),
  );

  Future<Paged<Invoice>> invoices({
    int page = 1,
    int pageSize = 25,
    bool? overdueOnly,
  }) async => Paged<Invoice>.fromJson(
    await _client.get<Json>(
      '/invoices',
      query: <String, dynamic>{
        'page': page,
        'page_size': pageSize,
        'overdue_only': overdueOnly,
      },
    ),
    Invoice.fromJson,
  );

  Future<Invoice> createInvoice({
    required String customerId,
    required List<SalesLineInput> lines,
    bool post = true,
  }) async => Invoice.fromJson(
    await _client.post<Json>(
      '/invoices',
      body: <String, dynamic>{
        'customer_id': customerId,
        'post': post,
        'lines': lines.map((SalesLineInput line) => line.toJson()).toList(),
      },
    ),
  );

  Future<Invoice> postInvoice(String id) async =>
      Invoice.fromJson(await _client.post<Json>('/invoices/$id/post'));

  Future<Ageing> ageing() async =>
      Ageing.fromJson(await _client.get<Json>('/invoices/ageing'));

  Future<Paged<Payment>> payments({int page = 1, int pageSize = 25}) async =>
      Paged<Payment>.fromJson(
        await _client.get<Json>(
          '/payments',
          query: <String, dynamic>{'page': page, 'page_size': pageSize},
        ),
        Payment.fromJson,
      );
}
