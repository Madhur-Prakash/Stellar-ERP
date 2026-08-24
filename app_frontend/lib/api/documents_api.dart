import 'dart:typed_data';

import 'package:dio/dio.dart';

import '../core/api_client.dart';
import '../core/api_error.dart';
import '../models/documents.dart';
import '../models/inventory.dart';
import '../models/json.dart';
import '../models/page.dart';

/// Scanned-document bindings.
class DocumentsApi {
  const DocumentsApi(this._client);

  final ApiClient _client;

  Future<OcrCapabilities> capabilities() async => OcrCapabilities.fromJson(
    await _client.get<Json>('/documents/capabilities'),
  );

  Future<Paged<DocumentSummary>> list({
    int page = 1,
    int pageSize = 25,
    String? query,
    bool? needsReview,
  }) async => Paged<DocumentSummary>.fromJson(
    await _client.get<Json>(
      '/documents',
      query: <String, dynamic>{
        'page': page,
        'page_size': pageSize,
        'q': query,
        'needs_review': needsReview,
      },
    ),
    DocumentSummary.fromJson,
  );

  Future<ScannedDocument> get(String id) async =>
      ScannedDocument.fromJson(await _client.get<Json>('/documents/$id'));

  Future<DocumentText> text(String id) async =>
      DocumentText.fromJson(await _client.get<Json>('/documents/$id/text'));

  /// Upload one file.
  ///
  /// The `Content-Type` header is left for Dio to write, not set here: multipart
  /// needs a generated boundary, and an explicit `multipart/form-data` without one
  /// makes the server unable to parse the body. The client's default
  /// `application/json` has to be displaced, which `FormData` does.
  ///
  /// The timeout is raised well past the default: recognition runs inline and a
  /// scanned page can take several seconds.
  Future<UploadResult> upload({
    required String filename,
    required Uint8List bytes,
    String kind = 'purchase_invoice',
  }) async {
    final FormData form = FormData.fromMap(<String, dynamic>{
      'file': MultipartFile.fromBytes(bytes, filename: filename),
      'kind': kind,
    });

    final Json json = await _client.post<Json>(
      '/documents',
      body: form,
      options: Options(
        receiveTimeout: const Duration(seconds: 120),
        sendTimeout: const Duration(seconds: 120),
      ),
    );
    return UploadResult.fromJson(json);
  }

  Future<ScannedDocument> reextract(String id) async =>
      ScannedDocument.fromJson(
        await _client.post<Json>('/documents/$id/reextract'),
      );

  /// Correct what the engine read.
  ///
  /// **Only send the fields that changed.** The endpoint is a PATCH that reads
  /// `model_fields_set`, so a key that is absent leaves that field alone while an
  /// explicit `null` clears it. Passing the whole form back would work, but every
  /// untouched field would be recorded as a human correction - and the point of the
  /// record is to say which values a person actually checked.
  Future<ScannedDocument> correct(String id, Map<String, Object?> fields) async =>
      ScannedDocument.fromJson(
        await _client.patch<Json>('/documents/$id/extracted', body: fields),
      );

  Future<void> remove(String id) => _client.delete<Json>('/documents/$id');

  Future<Bill> confirm(
    String id, {
    required String supplierId,
    required List<PurchaseLineInput> lines,
    String? supplierInvoiceNumber,
    String? billDate,
    bool post = true,
  }) async {
    final Json json = await _client.post<Json>(
      '/documents/$id/confirm',
      body: <String, dynamic>{
        'bill': <String, dynamic>{
          'supplier_id': supplierId,
          'supplier_invoice_number': supplierInvoiceNumber,
          'bill_date': billDate,
          'post': post,
          'lines': lines
              .map((PurchaseLineInput line) => line.toJson())
              .toList(),
        },
      },
    );
    return ConfirmResult.fromJson(json).bill;
  }

  /// Fetch the original file's bytes.
  ///
  /// Goes through the authenticated client rather than handing a URL to an image
  /// widget, because the endpoint requires the `Authorization` header - the same
  /// reason the web app fetches a blob instead of setting `<img src>`.
  ///
  /// Bytes rather than a temp file: the preview is transient, and writing every
  /// document a reviewer opens to disk would leave copies of supplier invoices
  /// outside the database this product exists to keep them in.
  Future<Uint8List> fileBytes(String id) async {
    try {
      final Response<List<int>> response = await _client.dio.get<List<int>>(
        '/documents/$id/file',
        options: Options(responseType: ResponseType.bytes),
      );
      return Uint8List.fromList(response.data ?? const <int>[]);
    } catch (error) {
      throw ApiError.from(error);
    }
  }
}
