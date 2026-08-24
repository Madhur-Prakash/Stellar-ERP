import 'dart:io';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../core/api_error.dart';
import '../../core/format.dart';
import '../../core/locale_settings.dart';
import '../../models/documents.dart';
import '../../models/inventory.dart';
import '../../models/page.dart';
import '../../models/sales.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../../theme/app_theme.dart';
import '../../theme/oklch.dart';
import '../../theme/tokens.dart';
import '../../widgets/app_badge.dart';
import '../../widgets/app_button.dart';
import '../../widgets/app_card.dart';
import '../../widgets/app_input.dart';
import '../../widgets/app_modal.dart';
import '../../widgets/app_select.dart';
import '../../widgets/data_table.dart';
import '../../widgets/primitives.dart';
import '../../widgets/toast.dart';
import '../sales/party_form.dart';

/// Scanned documents - the inbox between a supplier's PDF and the ledger.
///
/// **This screen's job is to make a machine's guess easy to disbelieve.** Every extracted
/// value is shown with how much the server trusts it, low-confidence fields are marked, and
/// the confirm form is pre-filled but fully editable - because what gets posted is what the
/// reviewer approves, not what OCR read. The backend enforces that (it never reads the
/// extracted values on the confirm path); this screen's job is to make the difference
/// visible rather than hide it behind a one-click "accept".
///
/// The confirm form asks for **lines**, not just a total. A bill without lines cannot be
/// costed, taxed per HSN, or matched to a receipt - and extraction does not read line items
/// reliably enough to pretend otherwise.
class DocumentsScreen extends ConsumerStatefulWidget {
  const DocumentsScreen({super.key});

  @override
  ConsumerState<DocumentsScreen> createState() => _DocumentsScreenState();
}

class _DocumentsScreenState extends ConsumerState<DocumentsScreen> {
  String? _selected;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        const PageHeader(
          title: 'Scanned documents',
          description:
              'Upload a supplier invoice and the fields are read out of it. Nothing is posted '
              'until you confirm it.',
        ),
        if (_selected != null)
          _DocumentReview(
            id: _selected!,
            onClose: () => setState(() => _selected = null),
          )
        else
          _DocumentQueue(onOpen: (String id) => setState(() => _selected = id)),
      ],
    );
  }
}

/// Matches `HIGH_CONFIDENCE` in `app/modules/ocr/extraction.py`.
const double _highConfidence = 0.85;

const Map<String, BadgeTone> _statusTones = <String, BadgeTone>{
  'uploaded': BadgeTone.neutral,
  'extracted': BadgeTone.info,
  'confirmed': BadgeTone.success,
  'rejected': BadgeTone.neutral,
  'failed': BadgeTone.danger,
};

const Map<String, String> _statusLabels = <String, String>{
  'uploaded': 'Reading',
  'extracted': 'Needs review',
  'confirmed': 'Entered',
  'rejected': 'Rejected',
  'failed': 'Could not read',
};

/// Field names as the API reports them, with the labels a person recognises.
const Map<String, String> _fieldLabels = <String, String>{
  'supplier_name': 'Supplier',
  'supplier_gstin': 'GSTIN',
  'invoice_number': 'Invoice number',
  'invoice_date': 'Invoice date',
  'subtotal': 'Taxable value',
  'tax_amount': 'Tax',
  'total_amount': 'Total',
};

// =============================================================================
// Queue
// =============================================================================
class _DocumentQueue extends ConsumerStatefulWidget {
  const _DocumentQueue({required this.onOpen});

  final ValueChanged<String> onOpen;

  @override
  ConsumerState<_DocumentQueue> createState() => _DocumentQueueState();
}

class _DocumentQueueState extends ConsumerState<_DocumentQueue> {
  DocumentQuery _query = const DocumentQuery();
  final TextEditingController _search = TextEditingController();
  bool _uploading = false;

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  Future<void> _pickAndUpload() async {
    final FilePickerResult? picked = await FilePicker.pickFiles(
      type: FileType.custom,
      allowedExtensions: const <String>[
        'pdf',
        'png',
        'jpg',
        'jpeg',
        'tif',
        'tiff',
        'webp',
      ],
      withData: true,
    );
    final PlatformFile? file = picked?.files.firstOrNull;
    if (file == null) return;

    // `withData` fills `bytes` on desktop; the path fallback covers a picker that streams
    // instead.
    final Uint8List? bytes =
        file.bytes ??
        (file.path != null ? await File(file.path!).readAsBytes() : null);
    if (bytes == null) {
      if (mounted) context.toastError('Could not read that file');
      return;
    }

    setState(() => _uploading = true);
    try {
      final UploadResult result = await ref
          .read(documentsApiProvider)
          .upload(filename: file.name, bytes: bytes);
      ref.invalidate(documentsProvider);

      if (!mounted) return;
      if (result.alreadyUploaded) {
        context.toastInfo(
          'That file was already uploaded',
          description: 'Opening the document that was created the first time.',
        );
      } else if (result.duplicate != null) {
        // Loud on purpose: paying one supplier invoice twice is the most expensive
        // clerical error in payables.
        context.toastWarning(
          'This may be a duplicate invoice',
          description: result.duplicate!.reason,
          duration: const Duration(seconds: 12),
        );
      } else if (result.document.status == 'failed') {
        context.toastWarning(
          'The file was stored but could not be read',
          description: result.document.failureMessage,
          duration: const Duration(seconds: 10),
        );
      } else {
        context.toastSuccess(
          'Document read',
          description: 'Check the values, then confirm to create the bill.',
        );
      }

      widget.onOpen(result.document.id);
    } catch (error) {
      if (mounted) context.toastApiError(error, 'Upload failed');
    } finally {
      if (mounted) setState(() => _uploading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final OcrCapabilities? capabilities = ref
        .watch(ocrCapabilitiesProvider)
        .valueOrNull;
    final AsyncValue<Paged<DocumentSummary>> documents = ref.watch(
      documentsProvider(_query),
    );
    final Paged<DocumentSummary>? page = documents.valueOrNull;
    final String currency = localeSettings().currency;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      spacing: 16,
      children: <Widget>[
        if (capabilities != null && !capabilities.anyEngineAvailable)
          AppCard(
            borderColour: t.warning.at(0.3),
            background: t.warningBg,
            padding: const EdgeInsets.all(20),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              spacing: 12,
              children: <Widget>[
                Padding(
                  padding: const EdgeInsets.only(top: 2),
                  child: Icon(
                    LucideIcons.triangleAlert,
                    size: 16,
                    color: t.warning,
                  ),
                ),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(
                        'Document reading is not available',
                        style: TextStyle(
                          fontSize: 13,
                          fontWeight: FontWeight.w500,
                          color: t.content,
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        'No OCR engine is installed on this server. Install Tesseract, or '
                        "the backend's ocr extra, to enable it. Uploads will still be "
                        'stored and can be attached to a bill entered by hand.',
                        style: TextStyle(
                          fontSize: 13,
                          color: t.contentSecondary,
                          height: 1.5,
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),

        _UploadPanel(
          capabilities: capabilities,
          uploading: _uploading,
          onPick: _pickAndUpload,
        ),

        AppCard(
          child: Column(
            children: <Widget>[
              CardHeader(
                title: 'Document inbox',
                description:
                    'Newest first - what arrived this morning should not be buried under old '
                    'scans.',
                action: Row(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.center,
                  spacing: 8,
                  children: <Widget>[
                    CheckRow(
                      value: _query.onlyReview,
                      label: 'Low confidence only',
                      fontSize: 12,
                      onChanged: (bool next) => setState(
                        () => _query = DocumentQuery(
                          search: _query.search,
                          onlyReview: next,
                        ),
                      ),
                    ),
                    AppInput(
                      controller: _search,
                      placeholder: 'Search file, supplier, invoice no.',
                      width: 224,
                      onChanged: (String value) => setState(
                        () => _query = DocumentQuery(
                          search: value,
                          onlyReview: _query.onlyReview,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
              AppDataTable<DocumentSummary>(
                rows: page?.items ?? const <DocumentSummary>[],
                rowKey: (DocumentSummary row) => row.id,
                isLoading: documents.isLoading,
                onRowTap: (DocumentSummary row) => widget.onOpen(row.id),
                empty: const EmptyState(
                  title: 'No documents yet',
                  description:
                      'Upload a supplier invoice and its fields will be read out of it.',
                ),
                columns: <AppColumn<DocumentSummary>>[
                  AppColumn<DocumentSummary>(
                    header: 'File',
                    cell: (DocumentSummary row) => Row(
                      spacing: 8,
                      children: <Widget>[
                        Icon(
                          LucideIcons.fileText,
                          size: 14,
                          color: t.contentMuted,
                        ),
                        Flexible(
                          child: Text(
                            row.originalFilename,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                        if (row.isDuplicate)
                          const AppBadge(
                            'Duplicate?',
                            tone: BadgeTone.warning,
                            icon: LucideIcons.copy,
                            tooltip:
                                'An earlier document looks like the same invoice',
                          ),
                      ],
                    ),
                  ),
                  AppColumn<DocumentSummary>(
                    header: 'Supplier',
                    hideOnNarrow: true,
                    cell: (DocumentSummary row) =>
                        row.matchedSupplierName != null
                        ? Text(
                            row.matchedSupplierName!,
                            overflow: TextOverflow.ellipsis,
                          )
                        // The name OCR read is shown in a muted italic: it is a guess, and
                        // the GSTIN did not match anyone on file.
                        : Text(
                            row.extractedSupplierName ?? 'Not identified',
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                              color: t.contentMuted,
                              fontStyle: FontStyle.italic,
                            ),
                          ),
                  ),
                  AppColumn<DocumentSummary>(
                    header: 'Invoice no.',
                    hideOnNarrow: true,
                    fixedWidth: 150,
                    cell: (DocumentSummary row) => Text(
                      row.extractedInvoiceNumber ?? '-',
                      style: monoStyle(color: t.content),
                    ),
                  ),
                  AppColumn<DocumentSummary>(
                    header: 'Date',
                    hideOnNarrow: true,
                    fixedWidth: 116,
                    cell: (DocumentSummary row) => Text(
                      row.extractedInvoiceDate != null
                          ? formatDate(row.extractedInvoiceDate!)
                          : '-',
                    ),
                  ),
                  AppColumn<DocumentSummary>(
                    header: 'Total',
                    numeric: true,
                    cell: (DocumentSummary row) => Text(
                      row.extractedTotalAmount != null
                          ? formatMoney(
                              row.extractedTotalAmount,
                              currency: currency,
                            )
                          : '-',
                    ),
                  ),
                  AppColumn<DocumentSummary>(
                    header: 'Confidence',
                    numeric: true,
                    hideOnNarrow: true,
                    fixedWidth: 110,
                    cell: (DocumentSummary row) =>
                        ConfidenceMeter(value: row.overallConfidence),
                  ),
                  AppColumn<DocumentSummary>(
                    header: 'Status',
                    fixedWidth: 130,
                    cell: (DocumentSummary row) => AppBadge(
                      _statusLabels[row.status] ?? row.status,
                      tone: _statusTones[row.status] ?? BadgeTone.neutral,
                    ),
                  ),
                ],
              ),
              if (page != null)
                Pagination(
                  page: page.meta.page,
                  totalPages: page.meta.totalPages,
                  totalItems: page.meta.totalItems,
                  onChanged: (int next) => setState(
                    () => _query = DocumentQuery(
                      page: next,
                      search: _query.search,
                      onlyReview: _query.onlyReview,
                    ),
                  ),
                ),
            ],
          ),
        ),
      ],
    );
  }
}

// =============================================================================
// Upload
// =============================================================================
class _UploadPanel extends StatelessWidget {
  const _UploadPanel({
    required this.capabilities,
    required this.uploading,
    required this.onPick,
  });

  final OcrCapabilities? capabilities;
  final bool uploading;
  final VoidCallback onPick;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final int? megabytes = capabilities?.maxMegabytes;

    return Opacity(
      opacity: uploading ? 0.7 : 1,
      child: AppCard(
        dashed: true,
        padding: const EdgeInsets.all(20),
        child: Row(
          spacing: 16,
          children: <Widget>[
            Icon(LucideIcons.upload, size: 20, color: t.contentMuted),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    uploading
                        ? 'Reading the document…'
                        : 'Choose an invoice to upload',
                    style: TextStyle(
                      fontSize: 14,
                      fontWeight: FontWeight.w500,
                      color: t.content,
                    ),
                  ),
                  Text(
                    'PDF, PNG, JPEG, TIFF, or WebP'
                    '${megabytes != null ? ' · up to $megabytes MB' : ''}. A digital PDF is '
                    'read exactly; a photo is recognised and will need more checking.',
                    style: TextStyle(
                      fontSize: 12,
                      color: t.contentMuted,
                      height: 1.5,
                    ),
                  ),
                ],
              ),
            ),
            AppButton(
              onPressed: uploading ? null : onPick,
              variant: AppButtonVariant.secondary,
              label: 'Choose a file',
            ),
          ],
        ),
      ),
    );
  }
}

// =============================================================================
// Review
// =============================================================================
class _DocumentReview extends ConsumerWidget {
  const _DocumentReview({required this.id, required this.onClose});

  final String id;
  final VoidCallback onClose;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AppTokens t = context.tokens;
    final AsyncValue<ScannedDocument> query = ref.watch(documentProvider(id));
    final ScannedDocument? document = query.valueOrNull;

    if (document == null) {
      return AppCard(
        padding: const EdgeInsets.all(20),
        child: const Skeleton(height: 240),
      );
    }

    void invalidate() {
      ref.invalidate(documentProvider(id));
      ref.invalidate(documentsProvider);
    }

    Future<void> reextract() async {
      try {
        await ref.read(documentsApiProvider).reextract(id);
        invalidate();
        if (context.mounted) {
          context.toastSuccess('Read again from the stored text');
        }
      } catch (error) {
        if (context.mounted) context.toastApiError(error, 'Could not re-read');
      }
    }

    Future<void> remove() async {
      final bool confirmed = await confirmAction(
        context,
        title: 'Delete this document?',
        message:
            'The stored file is removed. Anything already entered as a bill keeps its own '
            'record.',
        confirmLabel: 'Delete',
      );
      if (!confirmed) return;
      try {
        await ref.read(documentsApiProvider).remove(id);
        ref.invalidate(documentsProvider);
        if (context.mounted) context.toastSuccess('Document deleted');
        onClose();
      } catch (error) {
        if (context.mounted) {
          context.toastApiError(error, 'Could not delete it');
        }
      }
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      spacing: 16,
      children: <Widget>[
        Row(
          children: <Widget>[
            AppButton(
              onPressed: onClose,
              variant: AppButtonVariant.ghost,
              leftIcon: LucideIcons.arrowLeft,
              label: 'Back to inbox',
            ),
            const Spacer(),
            AppButton(
              onPressed: document.status == 'confirmed' ? null : reextract,
              variant: AppButtonVariant.ghost,
              leftIcon: LucideIcons.refreshCw,
              label: 'Read again',
            ),
            // No Reject action. Two buttons for "I do not want this document" was one too
            // many - Delete says it plainly and refuses on anything backing a posted bill,
            // which is the only case rejecting protected. Documents already rejected keep
            // their status and still render; nothing here can create a new one.
            const SizedBox(width: 8),
            AppButton(
              onPressed: document.billId != null ? null : remove,
              variant: AppButtonVariant.ghost,
              leftIcon: LucideIcons.trash2,
              label: 'Delete',
              tooltip: document.billId != null
                  ? 'This document is the evidence behind a posted bill and must be kept'
                  : null,
            ),
          ],
        ),

        if (document.isDuplicate && document.status != 'confirmed')
          _Notice(
            icon: LucideIcons.copy,
            tone: t.warning,
            background: t.warningBg,
            title: 'This may already have been entered',
            body:
                'An earlier document has the same supplier GSTIN and invoice number. Check '
                'before confirming - this is a warning, not a block, because the values '
                'compared were read by a machine.',
          ),

        if (document.status == 'confirmed')
          _Notice(
            icon: LucideIcons.circleCheckBig,
            tone: t.success,
            background: t.successBg,
            title: 'Entered as bill ${document.billNumber}',
            body: document.reviewedAt != null
                ? 'Confirmed on ${formatDate(document.reviewedAt!)}.'
                : 'Confirmed.',
          ),

        if (document.status == 'failed')
          _Notice(
            icon: LucideIcons.triangleAlert,
            tone: t.danger,
            background: t.dangerBg,
            title: 'This document could not be read',
            body:
                '${document.failureMessage ?? ''}\n'
                'The file is still stored. Enter the bill by hand and it stays attached as '
                'the supporting document.',
          ),

        LayoutBuilder(
          builder: (BuildContext context, BoxConstraints constraints) {
            final Widget fields = _ExtractedFields(document: document);
            final Widget preview = _FilePreview(document: document);
            if (constraints.maxWidth < 1000) {
              return Column(spacing: 16, children: <Widget>[fields, preview]);
            }
            return Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              spacing: 16,
              children: <Widget>[
                Expanded(child: fields),
                Expanded(child: preview),
              ],
            );
          },
        ),

        if (document.status != 'confirmed' && document.status != 'rejected')
          _ConfirmForm(document: document, onConfirmed: invalidate),
      ],
    );
  }
}

class _Notice extends StatelessWidget {
  const _Notice({
    required this.icon,
    required this.tone,
    required this.background,
    required this.title,
    required this.body,
  });

  final IconData icon;
  final Color tone;
  final Color background;
  final String title;
  final String body;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    return AppCard(
      borderColour: tone.at(0.3),
      background: background,
      padding: const EdgeInsets.all(20),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        spacing: 12,
        children: <Widget>[
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Icon(icon, size: 16, color: tone),
          ),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  title,
                  style: TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w500,
                    color: t.content,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  body,
                  style: TextStyle(
                    fontSize: 13,
                    color: t.contentSecondary,
                    height: 1.5,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// The seven correctable fields, in the order a reviewer reads them off an invoice.
const List<String> _editableFields = <String>[
  'supplier_name',
  'supplier_gstin',
  'invoice_number',
  'invoice_date',
  'subtotal',
  'tax_amount',
  'total_amount',
];

/// What the engine read, and the reviewer's chance to disagree with it.
///
/// **Editable, because OCR misreads and the alternative is worse.** A smudged 8 read as a
/// 3, a GSTIN a character short, a total taken off the wrong line - without this the only
/// ways out were to upload a better scan or to carry the correction in your head down to
/// the confirm form, where it reached the bill but left the document permanently claiming
/// something false. That matters beyond tidiness: duplicate detection keys off
/// `(GSTIN, invoice number)`, supplier matching keys off the GSTIN, and the review queue
/// orders by a confidence score that should stop punishing a field a human has fixed.
///
/// Saving sends **only** the fields that changed, so correcting one value does not stamp
/// the six beside it as human-checked.
class _ExtractedFields extends ConsumerStatefulWidget {
  const _ExtractedFields({required this.document});

  final ScannedDocument document;

  @override
  ConsumerState<_ExtractedFields> createState() => _ExtractedFieldsState();
}

class _ExtractedFieldsState extends ConsumerState<_ExtractedFields> {
  /// Non-null only while editing; the keys are [_editableFields].
  Map<String, TextEditingController>? _drafts;
  bool _saving = false;

  /// Server-side validation messages, keyed by field name.
  ///
  /// A 422 already names the offending field and says why - the envelope carries
  /// `details.fields` as `{field: message}`. Showing only its summary ("One or more
  /// fields are invalid") throws that away and leaves the reviewer to guess which of
  /// seven boxes it meant.
  Map<String, String> _fieldErrors = const <String, String>{};

  ScannedDocument get document => widget.document;

  bool get _editable =>
      document.status != 'confirmed' && document.status != 'rejected';

  /// The raw stored value per field - what an edit starts from and is compared against.
  ///
  /// Raw, not formatted: `formatMoney` produces "₹1,234.00" and `formatDate` a display
  /// date, and round-tripping either back through the API would be a parse waiting to go
  /// wrong. The date is already `YYYY-MM-DD`, which is what the picker and the server
  /// both want.
  Map<String, String> _rawValues() => <String, String>{
    'supplier_name': document.extractedSupplierName ?? '',
    'supplier_gstin': document.extractedSupplierGstin ?? '',
    'invoice_number': document.extractedInvoiceNumber ?? '',
    'invoice_date': document.extractedInvoiceDate ?? '',
    'subtotal': document.extractedSubtotal ?? '',
    'tax_amount': document.extractedTaxAmount ?? '',
    'total_amount': document.extractedTotalAmount ?? '',
  };

  void _startEditing() {
    final Map<String, String> raw = _rawValues();
    setState(() {
      _drafts = <String, TextEditingController>{
        for (final String field in _editableFields)
          field: TextEditingController(text: raw[field]),
      };
    });
  }

  void _stopEditing() {
    _drafts?.values.forEach((TextEditingController c) => c.dispose());
    setState(() => _drafts = null);
  }

  Future<void> _save() async {
    final Map<String, TextEditingController>? drafts = _drafts;
    if (drafts == null) return;

    final Map<String, String> original = _rawValues();
    final Map<String, Object?> changed = <String, Object?>{};
    for (final String field in _editableFields) {
      final String next = drafts[field]!.text.trim();
      if (next == (original[field] ?? '').trim()) continue;
      // An emptied box means "the invoice does not carry this", which is `null` on the
      // wire. `''` would be a 422 on the text fields and a wrong zero on the amounts.
      changed[field] = next.isEmpty ? null : next;
    }

    if (changed.isEmpty) {
      _stopEditing();
      return;
    }

    setState(() {
      _saving = true;
      _fieldErrors = const <String, String>{};
    });
    try {
      await ref.read(documentsApiProvider).correct(document.id, changed);
      ref.invalidate(documentProvider(document.id));
      ref.invalidate(documentsProvider);
      if (mounted) {
        _stopEditing();
        context.toastSuccess(
          'Corrections saved',
          description:
              'The bill form below is pre-filled from the corrected values.',
        );
      }
    } catch (error) {
      if (!mounted) return;
      final ApiError failure = ApiError.from(error);
      if (failure.isValidation && failure.fieldErrors.isNotEmpty) {
        setState(() => _fieldErrors = failure.fieldErrors);
        context.toastError(
          'Check the highlighted fields',
          description: failure.fieldErrors.entries
              .map(
                (MapEntry<String, String> e) =>
                    '${_fieldLabels[e.key] ?? e.key}: ${e.value}',
              )
              .join(' · '),
        );
      } else {
        context.toastApiError(error, 'Could not save the corrections');
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  void dispose() {
    _drafts?.values.forEach((TextEditingController c) => c.dispose());
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final String currency = localeSettings().currency;

    final List<(String, String?)> rows = <(String, String?)>[
      ('supplier_name', document.extractedSupplierName),
      ('supplier_gstin', document.extractedSupplierGstin),
      ('invoice_number', document.extractedInvoiceNumber),
      (
        'invoice_date',
        document.extractedInvoiceDate != null
            ? formatDate(document.extractedInvoiceDate!)
            : null,
      ),
      (
        'subtotal',
        document.extractedSubtotal != null
            ? formatMoney(document.extractedSubtotal, currency: currency)
            : null,
      ),
      (
        'tax_amount',
        document.extractedTaxAmount != null
            ? formatMoney(document.extractedTaxAmount, currency: currency)
            : null,
      ),
      (
        'total_amount',
        document.extractedTotalAmount != null
            ? formatMoney(document.extractedTotalAmount, currency: currency)
            : null,
      ),
    ];

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          CardHeader(
            title: 'What was read',
            description: document.readExactly
                ? 'Read from the PDF text layer, so the characters are exact.'
                : 'Recognised from an image, so the characters are a best guess.',
            action: Row(
              mainAxisSize: MainAxisSize.min,
              spacing: 8,
              children: _drafts == null
                  ? <Widget>[
                      ConfidenceMeter(value: document.overallConfidence),
                      if (_editable)
                        AppButton(
                          onPressed: _startEditing,
                          variant: AppButtonVariant.ghost,
                          size: AppButtonSize.sm,
                          leftIcon: LucideIcons.pencil,
                          label: 'Edit',
                        ),
                    ]
                  : <Widget>[
                      AppButton(
                        onPressed: _saving ? null : _stopEditing,
                        variant: AppButtonVariant.ghost,
                        size: AppButtonSize.sm,
                        label: 'Cancel',
                      ),
                      AppButton(
                        onPressed: _saving ? null : _save,
                        loading: _saving,
                        size: AppButtonSize.sm,
                        label: 'Save',
                      ),
                    ],
            ),
          ),
          CardBody(
            padding: const EdgeInsets.only(left: 20, right: 20, bottom: 20),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                if (_drafts != null) ...<Widget>[
                  for (final String field in _editableFields) ...<Widget>[
                    AppInput(
                      label: _fieldLabels[field] ?? field,
                      controller: _drafts![field],
                      enabled: !_saving,
                      error: _fieldErrors[field],
                      // Clear the message as soon as the box is touched: leaving it
                      // under a field the reviewer has just retyped makes a fixed value
                      // look broken.
                      onChanged: _fieldErrors.containsKey(field)
                          ? (String _) => setState(
                              () => _fieldErrors = <String, String>{
                                for (final MapEntry<String, String> e
                                    in _fieldErrors.entries)
                                  if (e.key != field) e.key: e.value,
                              },
                            )
                          : null,
                      placeholder: field == 'supplier_gstin'
                          ? '27AABCU9603R1ZM'
                          : field == 'invoice_date'
                          ? 'YYYY-MM-DD'
                          : 'Not found',
                      maxLength: field == 'supplier_gstin' ? 15 : null,
                      keyboardType:
                          field == 'subtotal' ||
                              field == 'tax_amount' ||
                              field == 'total_amount'
                          ? const TextInputType.numberWithOptions(decimal: true)
                          : null,
                    ),
                    const SizedBox(height: 10),
                  ],
                  Text(
                    'Clearing a box records that the invoice does not carry that field. '
                    'Corrections change this document only - the bill is still created from '
                    'the form below, which you approve.',
                    style: TextStyle(
                      fontSize: 12,
                      height: 1.5,
                      color: t.contentMuted,
                    ),
                  ),
                ] else
                  for (final (String field, String? value) in rows)
                    Container(
                      padding: const EdgeInsets.symmetric(vertical: 8),
                      decoration: BoxDecoration(
                        border: Border(
                          bottom: BorderSide(color: t.border.at(0.6)),
                        ),
                      ),
                      child: Row(
                        children: <Widget>[
                          Text(
                            _fieldLabels[field] ?? field,
                            style: TextStyle(
                              fontSize: 13,
                              color: t.contentMuted,
                            ),
                          ),
                          const Spacer(),
                          Text(
                            value ?? 'Not found',
                            style: TextStyle(
                              fontSize: 13,
                              color: value == null
                                  ? t.contentMuted
                                  : document.lowConfidenceFields.contains(field)
                                  ? t.warning
                                  : t.content,
                              fontStyle: value == null
                                  ? FontStyle.italic
                                  : null,
                              fontWeight:
                                  document.lowConfidenceFields.contains(field)
                                  ? FontWeight.w500
                                  : FontWeight.w400,
                              fontFeatures: tabularFigures,
                            ),
                          ),
                          // A corrected field's confidence is 1 because a person typed
                          // it. Rendering "100%" there would claim the engine was
                          // certain, which is the opposite of what happened.
                          if (document.correctedFields.contains(field)) ...<Widget>[
                            const SizedBox(width: 8),
                            Text(
                              'Edited',
                              style: TextStyle(fontSize: 11, color: t.success),
                            ),
                          ] else if (document.fieldConfidence[field] !=
                              null) ...<Widget>[
                            const SizedBox(width: 8),
                            ConfidenceMeter(
                              value: document.fieldConfidence[field],
                              compact: true,
                            ),
                          ],
                        ],
                      ),
                    ),
                const SizedBox(height: 12),
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  spacing: 6,
                  children: <Widget>[
                    Icon(
                      document.totalsReconcile
                          ? LucideIcons.circleCheckBig
                          : LucideIcons.triangleAlert,
                      size: 14,
                      color: document.totalsReconcile
                          ? t.success
                          : t.contentMuted,
                    ),
                    Expanded(
                      child: Text(
                        document.totalsReconcile
                            ? 'Taxable value plus tax equals the total - strong evidence all '
                                  'three were read right.'
                            : 'The three amounts do not add up. Either a figure was '
                                  "misread, or the supplier's own arithmetic is off - check "
                                  'all three.',
                        style: TextStyle(
                          fontSize: 12,
                          height: 1.5,
                          color: document.totalsReconcile
                              ? t.success
                              : t.contentMuted,
                        ),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// The original file, and the recognised text behind it.
///
/// Both matter. The image answers "is this the right invoice?"; the text answers "where did
/// this number come from?" months later when a figure is questioned.
///
/// **The web app frames a PDF in an `<iframe>` and lets the browser render it. A desktop app
/// has no such viewer**, and bundling a PDF rasteriser to show one page of one invoice is a
/// large dependency for a preview. So an image is shown inline, and a PDF is handed to
/// whatever the machine already uses to read PDFs - which is also what the reviewer would do
/// with the paper copy. The recognised text is always available inline, and it is the half
/// that answers the question a year later.
class _FilePreview extends ConsumerStatefulWidget {
  const _FilePreview({required this.document});

  final ScannedDocument document;

  @override
  ConsumerState<_FilePreview> createState() => _FilePreviewState();
}

class _FilePreviewState extends ConsumerState<_FilePreview> {
  Uint8List? _bytes;
  ApiError? _failure;
  bool _showText = false;
  bool _opening = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    // Images only: fetching a multi-megabyte PDF that cannot be drawn would be waste.
    if (widget.document.isPdf) return;
    try {
      final Uint8List bytes = await ref
          .read(documentsApiProvider)
          .fileBytes(widget.document.id);
      if (mounted) setState(() => _bytes = bytes);
    } catch (error) {
      // Recorded, not swallowed. The preview is a convenience - the extracted values and
      // the text are the substance - but "no bytes" and "still loading" are the same
      // state to `_bytes == null`, so silence left a document whose file is gone showing
      // a loading skeleton that would never resolve.
      if (mounted) setState(() => _failure = ApiError.from(error));
    }
  }

  /// Write the file to a temporary path and hand it to the OS.
  ///
  /// A temp file is unavoidable here - the OS viewer takes a path, not bytes - so it goes in
  /// the system temp directory rather than anywhere persistent, and is named after the
  /// document so a reviewer with three open can tell them apart.
  Future<void> _openExternally() async {
    setState(() => _opening = true);
    try {
      final Uint8List bytes =
          _bytes ??
          await ref.read(documentsApiProvider).fileBytes(widget.document.id);
      final Directory temp = await getTemporaryDirectory();
      final File file = File(
        p.join(temp.path, widget.document.originalFilename),
      );
      await file.writeAsBytes(bytes);
      await launchUrl(file.uri);
    } catch (error) {
      if (mounted) context.toastApiError(error, 'Could not open the file');
    } finally {
      if (mounted) setState(() => _opening = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final ScannedDocument document = widget.document;
    final DocumentText? text = _showText
        ? ref.watch(documentTextProvider(document.id)).valueOrNull
        : null;

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          CardHeader(
            title: 'The document',
            description:
                '${document.originalFilename} · '
                '${(document.byteSize / 1024).round().clamp(1, 1 << 30)} KB'
                '${document.pageCount != null ? ' · ${document.pageCount} page(s)' : ''}',
            action: Row(
              mainAxisSize: MainAxisSize.min,
              spacing: 8,
              children: <Widget>[
                AppButton(
                  onPressed: () => setState(() => _showText = !_showText),
                  variant: AppButtonVariant.ghost,
                  size: AppButtonSize.sm,
                  label: _showText ? 'Show file' : 'Show text',
                ),
                AppButton(
                  onPressed: _opening ? null : _openExternally,
                  loading: _opening,
                  variant: AppButtonVariant.ghost,
                  size: AppButtonSize.sm,
                  label: 'Open',
                ),
              ],
            ),
          ),
          CardBody(
            padding: const EdgeInsets.only(left: 20, right: 20, bottom: 20),
            child: _showText
                ? Container(
                    constraints: const BoxConstraints(maxHeight: 384),
                    width: double.infinity,
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: t.surfaceSunken,
                      borderRadius: BorderRadius.circular(Radii.lg),
                    ),
                    child: SingleChildScrollView(
                      child: SelectableText(
                        text?.text ?? 'Loading…',
                        style: monoStyle(
                          fontSize: 11,
                          color: t.contentSecondary,
                          height: 1.5,
                        ),
                      ),
                    ),
                  )
                : _failure != null
                ? _FileUnavailable(failure: _failure!)
                : document.isPdf
                ? _PdfPlaceholder(onOpen: _opening ? null : _openExternally)
                : _bytes == null
                ? const Skeleton(height: 320)
                : ClipRRect(
                    borderRadius: BorderRadius.circular(Radii.lg),
                    child: Container(
                      decoration: BoxDecoration(
                        border: Border.all(color: t.border),
                        borderRadius: BorderRadius.circular(Radii.lg),
                      ),
                      constraints: const BoxConstraints(maxHeight: 384),
                      width: double.infinity,
                      child: Image.memory(_bytes!, fit: BoxFit.contain),
                    ),
                  ),
          ),
        ],
      ),
    );
  }
}

/// The file could not be fetched - most often because its bytes are no longer in storage.
///
/// Worded around which half of the document survived. A `blob_missing` 410 means the row,
/// the recognised text and every extracted value are intact and only the original file is
/// gone, so the reviewer's next step is "read the text" rather than "report a bug" - and
/// the file is recoverable only by uploading it again.
class _FileUnavailable extends StatelessWidget {
  const _FileUnavailable({required this.failure});

  final ApiError failure;

  @override
  Widget build(BuildContext context) {
    final bool missing = failure.code == 'blob_missing';
    return EmptyState(
      icon: LucideIcons.triangleAlert,
      title: missing
          ? 'The original file is no longer in storage'
          : 'The preview could not be loaded',
      description: missing
          ? '${failure.message} What was read out of it is still here - press Show text for '
                'the recognised text, and the values beside it are unchanged. Upload the file '
                'again if you need the original back.'
          : failure.message,
      verticalPadding: 48,
    );
  }
}

class _PdfPlaceholder extends StatelessWidget {
  const _PdfPlaceholder({required this.onOpen});

  final VoidCallback? onOpen;

  @override
  Widget build(BuildContext context) {
    return EmptyState(
      icon: LucideIcons.fileText,
      title: 'A PDF opens in your own viewer',
      description:
          'Press Open to read it there. Show text displays what was recognised from it, '
          'which is what a figure can be traced back to later.',
      verticalPadding: 48,
      action: AppButton(
        onPressed: onOpen,
        variant: AppButtonVariant.secondary,
        label: 'Open the PDF',
      ),
    );
  }
}

// =============================================================================
// Confirm
// =============================================================================
class _LineDraft {
  _LineDraft({String? unitPrice})
    : description = TextEditingController(),
      quantity = TextEditingController(text: '1'),
      unitPrice = TextEditingController(text: unitPrice ?? ''),
      taxRate = TextEditingController(text: '18');

  final TextEditingController description;
  final TextEditingController quantity;
  final TextEditingController unitPrice;
  final TextEditingController taxRate;

  bool get isUsable =>
      description.text.trim().isNotEmpty && unitPrice.text.trim().isNotEmpty;

  PurchaseLineInput toInput() => PurchaseLineInput(
    description: description.text.trim(),
    quantity: quantity.text.isEmpty ? '1' : quantity.text,
    unitPrice: unitPrice.text,
    taxRate: taxRate.text.isEmpty ? '0' : taxRate.text,
  );

  void dispose() {
    description.dispose();
    quantity.dispose();
    unitPrice.dispose();
    taxRate.dispose();
  }
}

class _ConfirmForm extends ConsumerStatefulWidget {
  const _ConfirmForm({required this.document, required this.onConfirmed});

  final ScannedDocument document;
  final VoidCallback onConfirmed;

  @override
  ConsumerState<_ConfirmForm> createState() => _ConfirmFormState();
}

class _ConfirmFormState extends ConsumerState<_ConfirmForm> {
  late String _supplierId = widget.document.matchedSupplierId ?? '';
  late final TextEditingController _invoiceNumber = TextEditingController(
    text: widget.document.extractedInvoiceNumber ?? '',
  );
  late String _billDate = widget.document.extractedInvoiceDate ?? '';
  bool _post = true;
  bool _saving = false;

  late final List<_LineDraft> _lines = <_LineDraft>[
    // Quantity 1 at the extracted taxable value is the fastest correct single-line bill:
    // the taxable value is pre-tax, so tax is computed rather than double-counted from the
    // total.
    _LineDraft(unitPrice: widget.document.extractedSubtotal),
  ];

  @override
  void dispose() {
    _invoiceNumber.dispose();
    for (final _LineDraft line in _lines) {
      line.dispose();
    }
    super.dispose();
  }

  Future<void> _submit() async {
    if (_supplierId.isEmpty) {
      context.toastError('Choose the supplier');
      return;
    }
    final List<_LineDraft> usable = _lines
        .where((_LineDraft line) => line.isUsable)
        .toList();
    if (usable.isEmpty) {
      context.toastError(
        'Add at least one line with a description and an amount',
      );
      return;
    }

    setState(() => _saving = true);
    try {
      final Bill bill = await ref
          .read(documentsApiProvider)
          .confirm(
            widget.document.id,
            supplierId: _supplierId,
            supplierInvoiceNumber: _invoiceNumber.text.trim().isEmpty
                ? null
                : _invoiceNumber.text.trim(),
            billDate: _billDate.isEmpty ? null : _billDate,
            post: _post,
            lines: usable.map((_LineDraft line) => line.toInput()).toList(),
          );
      invalidateDocuments(ref);
      widget.onConfirmed();
      if (!mounted) return;
      context.toastSuccess(
        'Bill ${bill.billNumber} created',
        description:
            '${formatMoney(bill.grandTotal, currency: localeSettings().currency)}'
            '${bill.status == 'posted' ? ' · posted to the ledger' : ' · saved as a draft'}',
      );
    } catch (error) {
      if (mounted) context.toastApiError(error, 'Could not create the bill');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final Paged<Supplier>? suppliers = ref
        .watch(allSuppliersProvider)
        .valueOrNull;
    final String currency = localeSettings().currency;
    final ScannedDocument document = widget.document;

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const CardHeader(
            title: 'Enter as a bill',
            description:
                'Pre-filled from the scan and fully editable. What posts to the ledger is '
                'what you approve here, not what was read.',
          ),
          CardBody(
            padding: const EdgeInsets.only(left: 20, right: 20, bottom: 20),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              spacing: 16,
              children: <Widget>[
                if (document.matchedSupplierId == null &&
                    document.extractedSupplierGstin != null)
                  Text.rich(
                    TextSpan(
                      children: <InlineSpan>[
                        const TextSpan(text: 'GSTIN '),
                        TextSpan(
                          text: document.extractedSupplierGstin,
                          style: monoStyle(
                            fontSize: 12,
                            color: t.contentSecondary,
                          ),
                        ),
                        const TextSpan(
                          text:
                              ' does not match any supplier on file. Pick one, or add the '
                              'supplier first so the next invoice from them matches '
                              'automatically.',
                        ),
                      ],
                    ),
                    style: TextStyle(
                      fontSize: 12,
                      color: t.contentMuted,
                      height: 1.5,
                    ),
                  ),

                Row(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  spacing: 12,
                  children: <Widget>[
                    Expanded(
                      child: AppSelect(
                        label: 'Supplier',
                        value: _supplierId,
                        placeholder: 'Choose…',
                        options: <SelectOption>[
                          for (final Supplier supplier
                              in suppliers?.items ?? const <Supplier>[])
                            SelectOption(
                              value: supplier.id,
                              label: supplier.name,
                            ),
                        ],
                        onChanged: (String next) =>
                            setState(() => _supplierId = next),
                      ),
                    ),
                    // The scan usually names a supplier who is not on file yet - that is the
                    // normal case for a first invoice from someone, not an edge case.
                    // Sending the user to another screen would lose the review.
                    AppButton(
                      onPressed: () async {
                        final CreatedParty? created = await showPartyForm(
                          context,
                          kind: PartyKind.supplier,
                        );
                        if (created != null) {
                          setState(() => _supplierId = created.id);
                        }
                      },
                      variant: AppButtonVariant.secondary,
                      label: 'New',
                    ),
                    Expanded(
                      child: AppInput(
                        label: "Supplier's invoice no.",
                        controller: _invoiceNumber,
                        placeholder: 'MW-2026-0142',
                      ),
                    ),
                    AppDateInput(
                      label: 'Invoice date',
                      value: _billDate,
                      width: 170,
                      onChanged: (String next) =>
                          setState(() => _billDate = next),
                    ),
                  ],
                ),

                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  spacing: 8,
                  children: <Widget>[
                    Text(
                      'Lines',
                      style: TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.w500,
                        color: t.contentSecondary,
                      ),
                    ),
                    for (int index = 0; index < _lines.length; index++)
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.end,
                        spacing: 8,
                        children: <Widget>[
                          Expanded(
                            child: AppInput(
                              controller: _lines[index].description,
                              placeholder: 'Description',
                              onChanged: (_) => setState(() {}),
                            ),
                          ),
                          SizedBox(
                            width: 80,
                            child: AppNumberInput(
                              controller: _lines[index].quantity,
                              placeholder: 'Qty',
                            ),
                          ),
                          SizedBox(
                            width: 112,
                            child: AppNumberInput(
                              controller: _lines[index].unitPrice,
                              decimals: 2,
                              placeholder: 'Unit price',
                              onChanged: (_) => setState(() {}),
                            ),
                          ),
                          SizedBox(
                            width: 80,
                            child: AppNumberInput(
                              controller: _lines[index].taxRate,
                              decimals: 2,
                              placeholder: 'GST %',
                            ),
                          ),
                          SizedBox(
                            width: 36,
                            child: _lines.length > 1
                                ? AppIconButton(
                                    icon: LucideIcons.x,
                                    tooltip: 'Remove line ${index + 1}',
                                    size: 14,
                                    onPressed: () => setState(
                                      () => _lines.removeAt(index).dispose(),
                                    ),
                                  )
                                : const SizedBox.shrink(),
                          ),
                        ],
                      ),
                    AppButton(
                      onPressed: () => setState(() => _lines.add(_LineDraft())),
                      variant: AppButtonVariant.ghost,
                      size: AppButtonSize.sm,
                      leftIcon: LucideIcons.plus,
                      label: 'Add a line',
                    ),
                  ],
                ),

                if (document.extractedTotalAmount != null)
                  Text.rich(
                    TextSpan(
                      children: <InlineSpan>[
                        const TextSpan(text: 'The scan says the total is '),
                        TextSpan(
                          text: formatMoney(
                            document.extractedTotalAmount,
                            currency: currency,
                          ),
                          style: TextStyle(
                            color: t.content,
                            fontWeight: FontWeight.w600,
                            fontFeatures: tabularFigures,
                          ),
                        ),
                        const TextSpan(
                          text:
                              ". The bill's total is computed from the lines above, so "
                              'compare the two before posting - they should agree.',
                        ),
                      ],
                    ),
                    style: TextStyle(
                      fontSize: 12,
                      color: t.contentMuted,
                      height: 1.5,
                    ),
                  ),

                Container(
                  padding: const EdgeInsets.only(top: 12),
                  decoration: BoxDecoration(
                    border: Border(top: BorderSide(color: t.border)),
                  ),
                  child: Row(
                    children: <Widget>[
                      CheckRow(
                        value: _post,
                        label: 'Post to the ledger immediately',
                        onChanged: (bool next) => setState(() => _post = next),
                      ),
                      const Spacer(),
                      AppButton(
                        onPressed: _saving ? null : _submit,
                        loading: _saving,
                        label: _saving
                            ? 'Creating…'
                            : _post
                            ? 'Create and post bill'
                            : 'Create draft bill',
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

// =============================================================================
// Confidence
// =============================================================================
/// How much the server trusts a value.
///
/// A `double` is safe here, unlike for money: this is a display threshold, not a figure that
/// lands in the ledger, so a float's last bit does not matter.
class ConfidenceMeter extends StatelessWidget {
  const ConfidenceMeter({super.key, required this.value, this.compact = false});

  final String? value;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;

    if (value == null) {
      return Text('-', style: TextStyle(fontSize: 11, color: t.contentMuted));
    }

    final double fraction = double.tryParse(value!) ?? 0;
    final int percent = (fraction * 100).round();
    final (BadgeTone tone, Color colour) = fraction >= _highConfidence
        ? (BadgeTone.success, t.success)
        : fraction >= 0.5
        ? (BadgeTone.warning, t.warning)
        : (BadgeTone.danger, t.danger);

    if (compact) {
      return Tooltip(
        message: '$percent% confident',
        child: SizedBox(
          width: 36,
          child: Text(
            '$percent%',
            textAlign: TextAlign.right,
            style: TextStyle(
              fontSize: 11,
              color: colour,
              fontFeatures: tabularFigures,
            ),
          ),
        ),
      );
    }

    return AppBadge('$percent%', tone: tone, tooltip: '$percent% confident');
  }
}
