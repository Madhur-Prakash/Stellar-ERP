import 'inventory.dart';
import 'json.dart';

/// Scanned-document contracts.
///
/// Confidence is a `String` for the same reason money is: it is a `Decimal`
/// server-side. Unlike money it *is* converted to a `double` for display, because a
/// threshold check for "should this be highlighted?" does not need exact decimal
/// arithmetic - nothing here lands in the ledger.
class OcrCapabilities {
  const OcrCapabilities({
    required this.enabled,
    required this.engines,
    required this.formats,
    required this.maxBytes,
    required this.anyEngineAvailable,
  });

  final bool enabled;
  final List<String> engines;
  final List<String> formats;
  final int maxBytes;
  final bool anyEngineAvailable;

  int get maxMegabytes => maxBytes ~/ (1024 * 1024);

  factory OcrCapabilities.fromJson(Json json) => OcrCapabilities(
    enabled: boolOf(json, 'enabled'),
    engines: stringList(json, 'engines'),
    formats: stringList(json, 'formats'),
    maxBytes: intOf(json, 'max_bytes'),
    anyEngineAvailable: boolOf(json, 'any_engine_available'),
  );
}

class DocumentSummary {
  const DocumentSummary({
    required this.id,
    required this.originalFilename,
    required this.contentType,
    required this.byteSize,
    required this.status,
    this.extractedSupplierName,
    this.extractedSupplierGstin,
    this.extractedInvoiceNumber,
    this.extractedInvoiceDate,
    this.extractedTotalAmount,
    this.overallConfidence,
    required this.totalsReconcile,
    required this.needsReview,
    required this.isDuplicate,
    this.matchedSupplierId,
    this.matchedSupplierName,
    this.billId,
  });

  final String id;
  final String originalFilename;
  final String contentType;
  final int byteSize;

  /// `uploaded`, `extracted`, `confirmed`, `rejected`, or `failed`.
  final String status;

  final String? extractedSupplierName;
  final String? extractedSupplierGstin;
  final String? extractedInvoiceNumber;
  final String? extractedInvoiceDate;
  final String? extractedTotalAmount;

  final String? overallConfidence;
  final bool totalsReconcile;
  final bool needsReview;
  final bool isDuplicate;

  final String? matchedSupplierId;
  final String? matchedSupplierName;
  final String? billId;

  bool get isPdf => contentType == 'application/pdf';

  factory DocumentSummary.fromJson(Json json) => DocumentSummary(
    id: str(json, 'id'),
    originalFilename: str(json, 'original_filename'),
    contentType: strOrNull(json, 'content_type') ?? 'application/pdf',
    byteSize: intOf(json, 'byte_size'),
    status: strOrNull(json, 'status') ?? 'uploaded',
    extractedSupplierName: strOrNull(json, 'extracted_supplier_name'),
    extractedSupplierGstin: strOrNull(json, 'extracted_supplier_gstin'),
    extractedInvoiceNumber: strOrNull(json, 'extracted_invoice_number'),
    extractedInvoiceDate: strOrNull(json, 'extracted_invoice_date'),
    extractedTotalAmount: moneyOrNull(json, 'extracted_total_amount'),
    overallConfidence: moneyOrNull(json, 'overall_confidence'),
    totalsReconcile: boolOf(json, 'totals_reconcile'),
    needsReview: boolOf(json, 'needs_review'),
    isDuplicate: boolOf(json, 'is_duplicate'),
    matchedSupplierId: strOrNull(json, 'matched_supplier_id'),
    matchedSupplierName: strOrNull(json, 'matched_supplier_name'),
    billId: strOrNull(json, 'bill_id'),
  );
}

class ScannedDocument extends DocumentSummary {
  const ScannedDocument({
    required super.id,
    required super.originalFilename,
    required super.contentType,
    required super.byteSize,
    required super.status,
    super.extractedSupplierName,
    super.extractedSupplierGstin,
    super.extractedInvoiceNumber,
    super.extractedInvoiceDate,
    super.extractedTotalAmount,
    super.overallConfidence,
    required super.totalsReconcile,
    required super.needsReview,
    required super.isDuplicate,
    super.matchedSupplierId,
    super.matchedSupplierName,
    super.billId,
    this.engine,
    this.pageCount,
    this.extractedSubtotal,
    this.extractedTaxAmount,
    required this.fieldConfidence,
    required this.lowConfidenceFields,
    required this.correctedFields,
    this.failureMessage,
    this.billNumber,
    this.reviewedAt,
  });

  /// `pdf-text-layer` when read exactly from a digital PDF; an OCR engine name
  /// when recognised from an image.
  final String? engine;
  final int? pageCount;

  final String? extractedSubtotal;
  final String? extractedTaxAmount;

  final Map<String, String> fieldConfidence;
  final List<String> lowConfidenceFields;

  /// Fields a human has typed over. Their confidence is 1 because a person set them,
  /// which is a different claim from "the engine was sure" - and only this list tells
  /// the two apart, so the UI must not render a corrected field as 100% confident.
  final List<String> correctedFields;

  final String? failureMessage;
  final String? billNumber;
  final String? reviewedAt;

  bool get readExactly => engine == 'pdf-text-layer';

  factory ScannedDocument.fromJson(Json json) {
    final DocumentSummary base = DocumentSummary.fromJson(json);
    final Json confidence = mapOf(json, 'field_confidence');
    return ScannedDocument(
      id: base.id,
      originalFilename: base.originalFilename,
      contentType: base.contentType,
      byteSize: base.byteSize,
      status: base.status,
      extractedSupplierName: base.extractedSupplierName,
      extractedSupplierGstin: base.extractedSupplierGstin,
      extractedInvoiceNumber: base.extractedInvoiceNumber,
      extractedInvoiceDate: base.extractedInvoiceDate,
      extractedTotalAmount: base.extractedTotalAmount,
      overallConfidence: base.overallConfidence,
      totalsReconcile: base.totalsReconcile,
      needsReview: base.needsReview,
      isDuplicate: base.isDuplicate,
      matchedSupplierId: base.matchedSupplierId,
      matchedSupplierName: base.matchedSupplierName,
      billId: base.billId,
      engine: strOrNull(json, 'engine'),
      pageCount: json['page_count'] == null ? null : intOf(json, 'page_count'),
      extractedSubtotal: moneyOrNull(json, 'extracted_subtotal'),
      extractedTaxAmount: moneyOrNull(json, 'extracted_tax_amount'),
      fieldConfidence: <String, String>{
        for (final MapEntry<String, dynamic> e in confidence.entries)
          e.key: '${e.value}',
      },
      lowConfidenceFields: stringList(json, 'low_confidence_fields'),
      correctedFields: stringList(json, 'corrected_fields'),
      failureMessage: strOrNull(json, 'failure_message'),
      billNumber: strOrNull(json, 'bill_number'),
      reviewedAt: strOrNull(json, 'reviewed_at'),
    );
  }
}

class DocumentText {
  const DocumentText({required this.text});

  final String text;

  factory DocumentText.fromJson(Json json) =>
      DocumentText(text: strOrNull(json, 'text') ?? '');
}

class DuplicateWarning {
  const DuplicateWarning({required this.reason, this.billNumber});

  final String reason;
  final String? billNumber;

  factory DuplicateWarning.fromJson(Json json) => DuplicateWarning(
    reason: str(json, 'reason'),
    billNumber: strOrNull(json, 'bill_number'),
  );
}

class UploadResult {
  const UploadResult({
    required this.document,
    this.duplicate,
    required this.alreadyUploaded,
  });

  final ScannedDocument document;
  final DuplicateWarning? duplicate;
  final bool alreadyUploaded;

  factory UploadResult.fromJson(Json json) {
    final Object? duplicate = json['duplicate'];
    return UploadResult(
      document: ScannedDocument.fromJson(mapOf(json, 'document')),
      duplicate: duplicate is Map
          ? DuplicateWarning.fromJson(duplicate.cast<String, dynamic>())
          : null,
      alreadyUploaded: boolOf(json, 'already_uploaded'),
    );
  }
}

class ConfirmResult {
  const ConfirmResult({required this.bill});

  final Bill bill;

  factory ConfirmResult.fromJson(Json json) =>
      ConfirmResult(bill: Bill.fromJson(mapOf(json, 'bill')));
}
