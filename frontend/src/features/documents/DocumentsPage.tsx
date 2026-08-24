/**
 * Scanned documents - the inbox between a supplier's PDF and the ledger.
 *
 * **This screen's job is to make a machine's guess easy to disbelieve.** Every
 * extracted value is shown with how much the server trusts it, low-confidence
 * fields are marked, and the confirm form is pre-filled but fully editable -
 * because what gets posted is what the reviewer approves, not what OCR read. The
 * backend enforces that (it never reads the extracted values on the confirm path);
 * the UI's job is to make the difference visible rather than hide it behind a
 * one-click "accept".
 *
 * The confirm form asks for **lines**, not just a total. A bill without lines
 * cannot be costed, taxed per HSN, or matched to a receipt - and extraction does
 * not read line items reliably enough to pretend otherwise. Typing two lines is
 * the price of a bill that is actually usable.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  CheckCircle2,
  Copy,
  FileText,
  Pencil,
  RefreshCw,
  Trash2,
  Upload,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

import { Badge, type BadgeTone } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import type { Column } from '@/components/ui/DataTable';
import { DataTable, PageHeader, Pagination } from '@/components/ui/DataTable';
import { Input } from '@/components/ui/Input';
import { NumberInput } from '@/components/ui/NumberInput';
import {
  type BillFromDocument,
  type Document,
  type DocumentFieldsUpdate,
  type DocumentStatus,
  type DocumentSummary,
  documentsApi,
} from '@/features/documents/api';
import { inventoryApi } from '@/features/inventory/api';
import { PartyFormModal } from '@/features/sales/PartyForm';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';
import { formatDate, formatMoney } from '@/lib/format';

/** Matches `HIGH_CONFIDENCE` in `app/modules/ocr/extraction.py`. */
const HIGH_CONFIDENCE = 0.85;

const STATUS_TONES: Record<DocumentStatus, BadgeTone> = {
  uploaded: 'neutral',
  extracted: 'info',
  confirmed: 'success',
  rejected: 'neutral',
  failed: 'danger',
};

const STATUS_LABELS: Record<DocumentStatus, string> = {
  uploaded: 'Reading',
  extracted: 'Needs review',
  confirmed: 'Entered',
  rejected: 'Rejected',
  failed: 'Could not read',
};

/** Field names as the API reports them, with the labels a person recognises. */
const FIELD_LABELS: Record<string, string> = {
  supplier_name: 'Supplier',
  supplier_gstin: 'GSTIN',
  invoice_number: 'Invoice number',
  invoice_date: 'Invoice date',
  subtotal: 'Taxable value',
  tax_amount: 'Tax',
  total_amount: 'Total',
};

export function DocumentsPage() {
  const [selected, setSelected] = useState<string | null>(null);

  return (
    <div>
      <PageHeader
        title="Scanned documents"
        description="Upload a supplier invoice and the fields are read out of it. Nothing is posted until you confirm it."
      />

      {selected ? (
        <DocumentReview id={selected} onClose={() => setSelected(null)} />
      ) : (
        <DocumentQueue onOpen={setSelected} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Queue
// ---------------------------------------------------------------------------
function DocumentQueue({ onOpen }: { onOpen: (id: string) => void }) {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [onlyReview, setOnlyReview] = useState(false);

  const { data: capabilities } = useQuery({
    queryKey: ['ocr-capabilities'],
    // Installed software does not change while the tab is open.
    staleTime: Number.POSITIVE_INFINITY,
    queryFn: () => documentsApi.capabilities(),
  });

  const { data, isLoading } = useQuery({
    queryKey: ['documents', page, search, onlyReview],
    queryFn: () =>
      documentsApi.list({
        page,
        page_size: 25,
        ...(search ? { q: search } : {}),
        ...(onlyReview ? { needs_review: true } : {}),
      }),
  });

  const columns: Column<DocumentSummary>[] = [
    {
      header: 'File',
      cell: (row) => (
        <span className="flex items-center gap-2">
          <FileText className="text-content-muted h-3.5 w-3.5 shrink-0" aria-hidden />
          <span className="max-w-[220px] truncate">{row.original_filename}</span>
          {row.is_duplicate && (
            <Badge tone="warning" title="An earlier document looks like the same invoice">
              <Copy className="h-3 w-3" aria-hidden />
              Duplicate?
            </Badge>
          )}
        </span>
      ),
    },
    {
      header: 'Supplier',
      hideOnMobile: true,
      cell: (row) =>
        row.matched_supplier_name ? (
          <span>{row.matched_supplier_name}</span>
        ) : (
          // The name OCR read is shown in a muted style: it is a guess, and the
          // GSTIN did not match anyone on file.
          <span className="text-content-muted italic">
            {row.extracted_supplier_name ?? 'Not identified'}
          </span>
        ),
    },
    {
      header: 'Invoice no.',
      hideOnMobile: true,
      cell: (row) => (
        <span className="font-mono text-[12px]">{row.extracted_invoice_number ?? '-'}</span>
      ),
    },
    {
      header: 'Date',
      hideOnMobile: true,
      cell: (row) => (row.extracted_invoice_date ? formatDate(row.extracted_invoice_date) : '-'),
    },
    {
      header: 'Total',
      numeric: true,
      cell: (row) => (row.extracted_total_amount ? formatMoney(row.extracted_total_amount) : '-'),
    },
    {
      header: 'Confidence',
      numeric: true,
      hideOnMobile: true,
      cell: (row) => <ConfidenceMeter value={row.overall_confidence} />,
    },
    {
      header: 'Status',
      cell: (row) => <Badge tone={STATUS_TONES[row.status]}>{STATUS_LABELS[row.status]}</Badge>,
    },
  ];

  return (
    <div className="space-y-4">
      {capabilities && !capabilities.any_engine_available && (
        <Card className="border-warning/30 bg-warning-bg">
          <CardBody className="flex gap-3 pt-5 text-[13px]">
            <AlertTriangle className="text-warning h-4 w-4 shrink-0" aria-hidden />
            <div>
              <p className="text-content font-medium">Document reading is not available</p>
              <p className="text-content-secondary mt-0.5">
                No OCR engine is installed on this server. Install Tesseract, or the backend's
                <code className="mx-1 font-mono text-[12px]">ocr</code>extra, to enable it. Uploads
                will still be stored and can be attached to a bill entered by hand.
              </p>
            </div>
          </CardBody>
        </Card>
      )}

      <UploadPanel capabilities={capabilities} onUploaded={onOpen} />

      <Card>
        <CardHeader
          title="Document inbox"
          description="Newest first - what arrived this morning should not be buried under old scans."
          action={
            <div className="flex items-center gap-2">
              <label className="text-content-secondary flex items-center gap-1.5 text-[12px]">
                <input
                  type="checkbox"
                  checked={onlyReview}
                  onChange={(event) => {
                    setOnlyReview(event.target.checked);
                    setPage(1);
                  }}
                  className="accent-primary"
                />
                Low confidence only
              </label>
              <Input
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setPage(1);
                }}
                placeholder="Search file, supplier, invoice no."
                className="w-56"
              />
            </div>
          }
        />
        <DataTable
          columns={columns}
          rows={data?.items ?? []}
          rowKey={(row) => row.id}
          isLoading={isLoading}
          onRowClick={(row) => onOpen(row.id)}
          empty={{
            title: 'No documents yet',
            description: 'Upload a supplier invoice and its fields will be read out of it.',
          }}
        />
        {data && (
          <Pagination
            page={data.meta.page}
            totalPages={data.meta.total_pages}
            totalItems={data.meta.total_items}
            onChange={setPage}
          />
        )}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Upload
// ---------------------------------------------------------------------------
function UploadPanel({
  capabilities,
  onUploaded,
}: {
  capabilities: { max_bytes: number; formats: string[] } | undefined;
  onUploaded: (id: string) => void;
}) {
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const upload = useMutation({
    mutationFn: (file: File) => documentsApi.upload(file),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['documents'] });

      if (result.already_uploaded) {
        toast.info('That file was already uploaded', {
          description: 'Opening the document that was created the first time.',
        });
      } else if (result.duplicate) {
        // Loud on purpose: paying one supplier invoice twice is the most
        // expensive clerical error in payables.
        toast.warning('This may be a duplicate invoice', {
          description: result.duplicate.reason,
          duration: 12_000,
        });
      } else if (result.document.status === 'failed') {
        toast.warning('The file was stored but could not be read', {
          description: result.document.failure_message ?? undefined,
          duration: 10_000,
        });
      } else {
        toast.success('Document read', {
          description: 'Check the values, then confirm to create the bill.',
        });
      }

      onUploaded(result.document.id);
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : 'Upload failed'),
  });

  const megabytes = capabilities ? Math.floor(capabilities.max_bytes / (1024 * 1024)) : null;

  return (
    <Card
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        const file = event.dataTransfer.files[0];
        if (file) upload.mutate(file);
      }}
      className={cn(
        'border-dashed transition-colors',
        dragging && 'border-primary bg-primary/5',
        upload.isPending && 'opacity-70',
      )}
    >
      <CardBody className="flex flex-wrap items-center justify-between gap-4 pt-5">
        <div className="flex items-center gap-3">
          <Upload className="text-content-muted h-5 w-5 shrink-0" aria-hidden />
          <div>
            <p className="text-content text-[14px] font-medium">
              {upload.isPending ? 'Reading the document…' : 'Drop an invoice here'}
            </p>
            <p className="text-content-muted text-[12px]">
              PDF, PNG, JPEG, TIFF, or WebP{megabytes ? ` · up to ${megabytes} MB` : ''}. A digital
              PDF is read exactly; a photo is recognised and will need more checking.
            </p>
          </div>
        </div>

        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.webp"
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) upload.mutate(file);
            // Reset so choosing the same file twice fires `change` again.
            event.target.value = '';
          }}
        />
        <Button
          variant="secondary"
          onClick={() => inputRef.current?.click()}
          disabled={upload.isPending}
        >
          Choose a file
        </Button>
      </CardBody>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Review
// ---------------------------------------------------------------------------
function DocumentReview({ id, onClose }: { id: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const { data: document, isLoading } = useQuery({
    queryKey: ['document', id],
    queryFn: () => documentsApi.get(id),
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['document', id] });
    void queryClient.invalidateQueries({ queryKey: ['documents'] });
  };

  const reextract = useMutation({
    mutationFn: () => documentsApi.reextract(id),
    onSuccess: () => {
      invalidate();
      toast.success('Read again from the stored text');
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not re-read'),
  });

  const remove = useMutation({
    mutationFn: () => documentsApi.remove(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['documents'] });
      toast.success('Document deleted');
      onClose();
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : 'Failed'),
  });

  if (isLoading || !document) {
    return (
      <Card>
        <CardBody className="pt-5 text-[13px]">Loading…</CardBody>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button variant="ghost" onClick={onClose}>
          ← Back to inbox
        </Button>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="ghost"
            onClick={() => reextract.mutate()}
            disabled={reextract.isPending || document.status === 'confirmed'}
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            Read again
          </Button>
          {/* No Reject action. Two buttons for "I do not want this document" was one too
              many - Delete says it plainly and refuses on anything backing a posted bill,
              which is the only case rejecting protected. Documents already rejected keep
              their status and still render; nothing here can create a new one. */}
          <Button
            variant="ghost"
            onClick={() => {
              if (window.confirm('Delete this document?')) remove.mutate();
            }}
            disabled={remove.isPending || document.bill_id !== null}
            title={
              document.bill_id
                ? 'This document is the evidence behind a posted bill and must be kept'
                : undefined
            }
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden />
            Delete
          </Button>
        </div>
      </div>

      {document.is_duplicate && document.status !== 'confirmed' && (
        <Card className="border-warning/30 bg-warning-bg">
          <CardBody className="flex gap-3 pt-5 text-[13px]">
            <Copy className="text-warning h-4 w-4 shrink-0" aria-hidden />
            <div>
              <p className="text-content font-medium">This may already have been entered</p>
              <p className="text-content-secondary mt-0.5">
                An earlier document has the same supplier GSTIN and invoice number. Check before
                confirming - this is a warning, not a block, because the values compared were read
                by a machine.
              </p>
            </div>
          </CardBody>
        </Card>
      )}

      {document.status === 'confirmed' && (
        <Card className="border-success/30 bg-success-bg">
          <CardBody className="flex gap-3 pt-5 text-[13px]">
            <CheckCircle2 className="text-success h-4 w-4 shrink-0" aria-hidden />
            <p className="text-content">
              Entered as bill <strong>{document.bill_number}</strong>
              {document.reviewed_at ? ` on ${formatDate(document.reviewed_at)}` : ''}.
            </p>
          </CardBody>
        </Card>
      )}

      {document.status === 'failed' && (
        <Card className="border-danger/30 bg-danger-bg">
          <CardBody className="flex gap-3 pt-5 text-[13px]">
            <AlertTriangle className="text-danger h-4 w-4 shrink-0" aria-hidden />
            <div>
              <p className="text-content font-medium">This document could not be read</p>
              <p className="text-content-secondary mt-0.5">{document.failure_message}</p>
              <p className="text-content-muted mt-1">
                The file is still stored. Enter the bill by hand and it stays attached as the
                supporting document.
              </p>
            </div>
          </CardBody>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {/* Keyed by document id so both panels reset their own state when the reviewer
            moves to another document - an in-progress edit or a "file is missing" from
            the previous one must not survive into this one. */}
        <ExtractedFields key={document.id} document={document} />
        <FilePreview key={document.id} document={document} />
      </div>

      {document.status !== 'confirmed' && document.status !== 'rejected' && (
        <ConfirmForm document={document} onConfirmed={invalidate} />
      )}
    </div>
  );
}

/** The seven correctable fields, in the order a reviewer reads them off an invoice. */
const EDITABLE_FIELDS = [
  'supplier_name',
  'supplier_gstin',
  'invoice_number',
  'invoice_date',
  'subtotal',
  'tax_amount',
  'total_amount',
] as const;

type EditableField = (typeof EDITABLE_FIELDS)[number];

/** The raw stored value per field - what an edit starts from and is compared against. */
function rawValues(document: Document): Record<EditableField, string> {
  return {
    supplier_name: document.extracted_supplier_name ?? '',
    supplier_gstin: document.extracted_supplier_gstin ?? '',
    invoice_number: document.extracted_invoice_number ?? '',
    // Already `YYYY-MM-DD` from the API, which is exactly what `<input type="date">`
    // wants - no parsing, and no timezone to shift it across a day boundary.
    invoice_date: document.extracted_invoice_date ?? '',
    subtotal: document.extracted_subtotal ?? '',
    tax_amount: document.extracted_tax_amount ?? '',
    total_amount: document.extracted_total_amount ?? '',
  };
}

/**
 * What the engine read, and the reviewer's chance to disagree with it.
 *
 * **Editable, because OCR misreads and the alternative is worse.** A smudged 8 read as a
 * 3, a GSTIN a character short, a total taken off the wrong line - before this, the only
 * ways out were to re-upload a better scan or to carry the correction in your head to the
 * confirm form, where it landed on the bill but left the document permanently claiming
 * something false. That matters beyond tidiness: duplicate detection keys off
 * `(GSTIN, invoice number)`, supplier matching keys off the GSTIN, and the review queue
 * orders by a confidence score that should not keep punishing a field a human has fixed.
 *
 * Saving sends **only** the fields that actually changed, so an unedited field is not
 * re-stamped as human-checked when someone corrects the one beside it.
 */
function ExtractedFields({ document }: { document: Document }) {
  const queryClient = useQueryClient();
  const [drafts, setDrafts] = useState<Record<EditableField, string> | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const editable = document.status !== 'confirmed' && document.status !== 'rejected';

  const save = useMutation({
    mutationFn: (fields: DocumentFieldsUpdate) => documentsApi.correct(document.id, fields),
    onSuccess: (updated) => {
      setDrafts(null);
      setFieldErrors({});
      void queryClient.invalidateQueries({ queryKey: ['document', document.id] });
      void queryClient.invalidateQueries({ queryKey: ['documents'] });
      toast.success('Corrections saved', {
        description: updated.is_duplicate
          ? 'These values now match an earlier upload - check the duplicate warning.'
          : 'The bill form below is pre-filled from the corrected values.',
      });
    },
    onError: (error: unknown) => {
      // A 422 already names the offending field and says why - the server sends
      // `details.fields` as `{field: message}`. Showing only the envelope's summary
      // ("One or more fields are invalid") throws that away and leaves the reviewer to
      // guess which of seven boxes it meant. The message goes on the field itself, and
      // the toast names the fields so it is legible even with the card scrolled away.
      if (error instanceof ApiError && error.isValidation) {
        const fields = error.fieldErrors;
        setFieldErrors(fields);
        toast.error('Check the highlighted fields', {
          description: Object.entries(fields)
            .map(([field, message]) => `${FIELD_LABELS[field] ?? field}: ${message}`)
            .join(' · '),
        });
        return;
      }
      toast.error(error instanceof ApiError ? error.message : 'Could not save the corrections');
    },
  });

  const commit = () => {
    if (drafts === null) return;
    setFieldErrors({});
    const original = rawValues(document);
    const changed: DocumentFieldsUpdate = {};
    for (const field of EDITABLE_FIELDS) {
      const next = drafts[field].trim();
      if (next === original[field].trim()) continue;
      // An emptied box means "the invoice does not carry this", which is `null` on the
      // wire. `''` would be a 422 on the text fields and a wrong zero on the amounts.
      changed[field] = next === '' ? null : next;
    }
    if (Object.keys(changed).length === 0) {
      setDrafts(null);
      return;
    }
    save.mutate(changed);
  };

  return (
    <Card>
      <CardHeader
        title="What was read"
        description={
          document.engine === 'pdf-text-layer'
            ? 'Read from the PDF text layer, so the characters are exact.'
            : 'Recognised from an image, so the characters are a best guess.'
        }
        action={
          <div className="flex items-center gap-2">
            {drafts === null ? (
              <>
                <ConfidenceMeter value={document.overall_confidence} />
                {editable && (
                  <Button variant="ghost" onClick={() => setDrafts(rawValues(document))}>
                    <Pencil className="h-3.5 w-3.5" aria-hidden />
                    Edit
                  </Button>
                )}
              </>
            ) : (
              <>
                <Button variant="ghost" onClick={() => setDrafts(null)} disabled={save.isPending}>
                  Cancel
                </Button>
                <Button onClick={commit} disabled={save.isPending}>
                  {save.isPending ? 'Saving…' : 'Save'}
                </Button>
              </>
            )}
          </div>
        }
      />
      <CardBody className="pt-0">
        {drafts !== null ? (
          <div className="space-y-2.5">
            {EDITABLE_FIELDS.map((field) => {
              const update = (value: string) => {
                // Clear this field's error as soon as it is touched: leaving it under a
                // box the reviewer has just retyped makes a fixed field look broken.
                setFieldErrors((current) => {
                  if (!(field in current)) return current;
                  const { [field]: _removed, ...rest } = current;
                  return rest;
                });
                setDrafts((current) =>
                  current === null ? current : { ...current, [field]: value },
                );
              };
              const isAmount =
                field === 'subtotal' || field === 'tax_amount' || field === 'total_amount';
              return (
                <label key={field} className="block">
                  <span className="text-content-secondary mb-1 block text-[12px] font-medium">
                    {FIELD_LABELS[field] ?? field}
                  </span>
                  {isAmount ? (
                    <NumberInput
                      value={drafts[field]}
                      onValueChange={update}
                      placeholder="Not found"
                      error={fieldErrors[field]}
                    />
                  ) : (
                    <Input
                      type={field === 'invoice_date' ? 'date' : 'text'}
                      value={drafts[field]}
                      onChange={(event) => update(event.target.value)}
                      placeholder={field === 'supplier_gstin' ? '27AABCU9603R1ZM' : 'Not found'}
                      error={fieldErrors[field]}
                      {...(field === 'supplier_gstin'
                        ? { maxLength: 15, className: 'font-mono uppercase' }
                        : {})}
                    />
                  )}
                </label>
              );
            })}
            <p className="text-content-muted pt-1 text-[12px]">
              Clearing a box records that the invoice does not carry that field. Corrections change
              this document only - the bill is still created from the form below, which you approve.
            </p>
          </div>
        ) : (
          <dl className="divide-border/60 divide-y text-[13px]">
            {EDITABLE_FIELDS.map((field) => {
              const value = displayValue(document, field);
              const confidence = document.field_confidence[field];
              const low = document.low_confidence_fields.includes(field);
              const corrected = document.corrected_fields.includes(field);
              return (
                <div key={field} className="flex items-baseline justify-between gap-3 py-2">
                  <dt className="text-content-muted">{FIELD_LABELS[field] ?? field}</dt>
                  <dd className="flex items-center gap-2 text-right">
                    <span
                      className={cn(
                        'text-content tabular-nums',
                        low && 'text-warning font-medium',
                        !value && 'text-content-muted italic',
                      )}
                    >
                      {value ?? 'Not found'}
                    </span>
                    {/* A corrected field's confidence is 1 because a person typed it. Showing
                        "100%" there would claim the engine was certain, which is the opposite
                        of what happened. */}
                    {corrected ? (
                      <span
                        className="text-success w-9 shrink-0 text-right text-[11px]"
                        title="Edited by a reviewer"
                      >
                        Edited
                      </span>
                    ) : (
                      confidence && <ConfidenceMeter value={confidence} compact />
                    )}
                  </dd>
                </div>
              );
            })}
          </dl>
        )}

        <div className="border-border mt-3 border-t pt-3 text-[12px]">
          {document.totals_reconcile ? (
            <p className="text-success flex items-center gap-1.5">
              <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
              Taxable value plus tax equals the total - strong evidence all three were read right.
            </p>
          ) : (
            <p className="text-content-muted flex items-center gap-1.5">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />
              The three amounts do not add up. Either a figure was misread, or the supplier's own
              arithmetic is off - check all three.
            </p>
          )}
        </div>
      </CardBody>
    </Card>
  );
}

/** One field, formatted for reading rather than for editing. */
function displayValue(document: Document, field: EditableField): string | null {
  switch (field) {
    case 'supplier_name':
      return document.extracted_supplier_name;
    case 'supplier_gstin':
      return document.extracted_supplier_gstin;
    case 'invoice_number':
      return document.extracted_invoice_number;
    case 'invoice_date':
      return document.extracted_invoice_date ? formatDate(document.extracted_invoice_date) : null;
    case 'subtotal':
      return document.extracted_subtotal ? formatMoney(document.extracted_subtotal) : null;
    case 'tax_amount':
      return document.extracted_tax_amount ? formatMoney(document.extracted_tax_amount) : null;
    case 'total_amount':
      return document.extracted_total_amount ? formatMoney(document.extracted_total_amount) : null;
  }
}

/**
 * The original file, and the recognised text behind it.
 *
 * Both matter. The image answers "is this the right invoice?"; the text answers
 * "where did this number come from?" months later when a figure is questioned.
 */
function FilePreview({ document }: { document: Document }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failure, setFailure] = useState<ApiError | null>(null);
  const [showText, setShowText] = useState(false);

  const { data: text } = useQuery({
    queryKey: ['document-text', document.id],
    enabled: showText,
    queryFn: () => documentsApi.text(document.id),
  });

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;

    // No state reset here: the caller keys this component by document id, so moving to
    // another document mounts a fresh one rather than reusing this one's `url` and
    // `failure`. Clearing them in the effect body would be a second render on every
    // mount to reach the state it already started in.
    void documentsApi
      .fileUrl(document.id)
      .then((created) => {
        objectUrl = created;
        if (cancelled) {
          // The effect was torn down mid-fetch; revoke rather than leak.
          URL.revokeObjectURL(created);
          return;
        }
        setUrl(created);
      })
      .catch((error: unknown) => {
        // Recorded, not swallowed. This used to `setUrl(null)`, which is the same state
        // as "still loading" - so a document whose bytes are gone (a 410 `blob_missing`,
        // the documented outcome for blobs uploaded before the database backend) sat on
        // "Loading preview…" forever, with nothing to tell the reviewer whether to wait,
        // retry, or go and find the paper copy.
        if (cancelled) return;
        setFailure(
          error instanceof ApiError
            ? error
            : new ApiError('The preview could not be loaded.', { code: 'preview_failed' }),
        );
      });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [document.id]);

  return (
    <Card>
      <CardHeader
        title="The document"
        description={`${document.original_filename} · ${Math.max(1, Math.round(document.byte_size / 1024))} KB${
          document.page_count ? ` · ${document.page_count} page(s)` : ''
        }`}
        action={
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" onClick={() => setShowText((value) => !value)}>
              {showText ? 'Show file' : 'Show text'}
            </Button>
            {/* A browser that declines to inline the file must not leave the reviewer with
                nothing: opened at the top level it is the browser's own viewer, exactly as
                if the file had been downloaded and double-clicked. */}
            {url !== null && (
              <Button
                variant="ghost"
                onClick={() => window.open(url, '_blank', 'noopener,noreferrer')}
              >
                Open in a new tab
              </Button>
            )}
          </div>
        }
      />
      <CardBody className="pt-0">
        {showText ? (
          <pre className="bg-surface-sunken text-content-secondary max-h-96 overflow-auto rounded-lg p-3 font-mono text-[11px] whitespace-pre-wrap">
            {text?.text ?? 'Loading…'}
          </pre>
        ) : failure !== null ? (
          // Not a toast, and not a retry button. The bytes being gone is a fact about
          // this document rather than a transient failure, so it belongs in the panel
          // that was going to show them - and the useful thing to say is which half of
          // the document survived, because the extracted values and the recognised text
          // live on the row and are completely unaffected.
          <div className="border-warning/30 bg-warning-bg flex gap-3 rounded-lg border p-3 text-[13px]">
            <AlertTriangle className="text-warning h-4 w-4 shrink-0" aria-hidden />
            <div>
              <p className="text-content font-medium">
                {failure.code === 'blob_missing'
                  ? 'The original file is no longer in storage'
                  : 'The preview could not be loaded'}
              </p>
              <p className="text-content-secondary mt-0.5">{failure.message}</p>
              {failure.code === 'blob_missing' && (
                <p className="text-content-muted mt-1">
                  What was read out of it is still here - press <strong>Show text</strong> for the
                  recognised text, and the values beside it are unchanged. Upload the file again if
                  you need the original back.
                </p>
              )}
              {failure.requestId !== undefined && (
                <p className="text-content-muted mt-1 font-mono text-[11px]">
                  Reference: {failure.requestId}
                </p>
              )}
            </div>
          </div>
        ) : url === null ? (
          <p className="text-content-muted text-[13px]">Loading preview…</p>
        ) : document.content_type === 'application/pdf' ? (
          // No `sandbox=""` here, deliberately - it used to be, and Chrome refused to
          // render the frame at all: its built-in PDF viewer cannot run with every
          // permission denied, so the preview showed "This page has been blocked by
          // Chrome" for every PDF.
          //
          // Removing it costs less than it looks like, because the barriers that matter are
          // server-side and unaffected: the media type is the one *sniffed from the bytes*
          // at upload rather than the one the client announced, so this branch is only
          // reached for something that really is a PDF and cannot be reinterpreted as HTML;
          // the response also carries `Content-Disposition: attachment`, `nosniff`, and its
          // own `sandbox` CSP. The framed document is a PDF rendered by Chrome's own
          // process-isolated viewer, which has no access to this page's DOM or storage.
          <iframe
            src={url}
            title={document.original_filename}
            referrerPolicy="no-referrer"
            className="border-border h-96 w-full rounded-lg border"
          />
        ) : (
          <img
            src={url}
            alt={document.original_filename}
            className="border-border max-h-96 w-full rounded-lg border object-contain"
          />
        )}
      </CardBody>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Confirm
// ---------------------------------------------------------------------------
interface LineDraft {
  description: string;
  quantity: string;
  unit_price: string;
  tax_rate: string;
}

function ConfirmForm({ document, onConfirmed }: { document: Document; onConfirmed: () => void }) {
  const [supplierId, setSupplierId] = useState(document.matched_supplier_id ?? '');
  const [invoiceNumber, setInvoiceNumber] = useState(document.extracted_invoice_number ?? '');
  const [billDate, setBillDate] = useState(document.extracted_invoice_date ?? '');
  const [post, setPost] = useState(true);
  const [addingSupplier, setAddingSupplier] = useState(false);
  const [lines, setLines] = useState<LineDraft[]>([
    {
      description: '',
      // Quantity 1 at the extracted taxable value is the fastest correct
      // single-line bill: the taxable value is pre-tax, so tax is computed rather
      // than double-counted from the total.
      quantity: '1',
      unit_price: document.extracted_subtotal ?? '',
      tax_rate: '18',
    },
  ]);

  const { data: suppliers } = useQuery({
    queryKey: ['suppliers', 'all'],
    queryFn: () => inventoryApi.suppliers({ page_size: 200 }),
  });

  const confirm = useMutation({
    mutationFn: (payload: BillFromDocument) => documentsApi.confirm(document.id, payload),
    onSuccess: (result) => {
      onConfirmed();
      toast.success(`Bill ${result.bill.bill_number} created`, {
        description: `${formatMoney(result.bill.grand_total)}${
          result.bill.status === 'posted' ? ' · posted to the ledger' : ' · saved as a draft'
        }`,
      });
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not create the bill'),
  });

  const submit = () => {
    if (!supplierId) {
      toast.error('Choose the supplier');
      return;
    }
    const usable = lines.filter((line) => line.description.trim() && line.unit_price);
    if (usable.length === 0) {
      toast.error('Add at least one line with a description and an amount');
      return;
    }

    confirm.mutate({
      supplier_id: supplierId,
      supplier_invoice_number: invoiceNumber || null,
      bill_date: billDate || null,
      post,
      lines: usable.map((line) => ({
        description: line.description.trim(),
        quantity: line.quantity || '1',
        unit_price: line.unit_price,
        tax_rate: line.tax_rate || '0',
      })),
    });
  };

  return (
    <Card>
      <CardHeader
        title="Enter as a bill"
        description="Pre-filled from the scan and fully editable. What posts to the ledger is what you approve here, not what was read."
      />
      <CardBody className="space-y-4 pt-0">
        <PartyFormModal
          kind="supplier"
          open={addingSupplier}
          onClose={() => setAddingSupplier(false)}
          onCreated={(id) => setSupplierId(id)}
        />

        {!document.matched_supplier_id && document.extracted_supplier_gstin && (
          <p className="text-content-muted text-[12px]">
            GSTIN <span className="font-mono">{document.extracted_supplier_gstin}</span> does not
            match any supplier on file. Pick one, or add the supplier first so the next invoice from
            them matches automatically.
          </p>
        )}

        <div className="grid gap-3 sm:grid-cols-3">
          <label className="block">
            <span className="text-content-secondary mb-1 block text-[12px] font-medium">
              Supplier
            </span>
            <div className="flex gap-1.5">
              <select
                value={supplierId}
                onChange={(event) => setSupplierId(event.target.value)}
                className="border-border bg-surface text-content w-full rounded-lg border px-2.5 py-1.5 text-[13px]"
              >
                <option value="">Choose…</option>
                {suppliers?.items.map((supplier) => (
                  <option key={supplier.id} value={supplier.id}>
                    {supplier.name}
                  </option>
                ))}
              </select>
              {/* The scan usually names a supplier who is not on file yet - that is
                  the normal case for a first invoice from someone, not an edge case.
                  Sending the user to another screen would lose the review. */}
              <Button
                variant="secondary"
                onClick={() => setAddingSupplier(true)}
                className="shrink-0"
              >
                New
              </Button>
            </div>
          </label>

          <label className="block">
            <span className="text-content-secondary mb-1 block text-[12px] font-medium">
              Supplier's invoice no.
            </span>
            <Input
              value={invoiceNumber}
              onChange={(event) => setInvoiceNumber(event.target.value)}
              placeholder="MW-2026-0142"
            />
          </label>

          <label className="block">
            <span className="text-content-secondary mb-1 block text-[12px] font-medium">
              Invoice date
            </span>
            <Input
              type="date"
              value={billDate}
              onChange={(event) => setBillDate(event.target.value)}
            />
          </label>
        </div>

        <div className="space-y-2">
          <p className="text-content-secondary text-[12px] font-medium">Lines</p>
          {lines.map((line, index) => (
            <div key={index} className="grid gap-2 sm:grid-cols-[1fr_5rem_7rem_5rem_2rem]">
              <Input
                value={line.description}
                onChange={(event) =>
                  setLines((current) =>
                    current.map((item, position) =>
                      position === index ? { ...item, description: event.target.value } : item,
                    ),
                  )
                }
                placeholder="Description"
              />
              <NumberInput
                value={line.quantity}
                onValueChange={(quantity) =>
                  setLines((current) =>
                    current.map((item, position) =>
                      position === index ? { ...item, quantity } : item,
                    ),
                  )
                }
                placeholder="Qty"
              />
              <NumberInput
                value={line.unit_price}
                onValueChange={(unit_price) =>
                  setLines((current) =>
                    current.map((item, position) =>
                      position === index ? { ...item, unit_price } : item,
                    ),
                  )
                }
                placeholder="Unit price"
              />
              <NumberInput
                value={line.tax_rate}
                onValueChange={(tax_rate) =>
                  setLines((current) =>
                    current.map((item, position) =>
                      position === index ? { ...item, tax_rate } : item,
                    ),
                  )
                }
                placeholder="GST %"
              />
              <button
                type="button"
                onClick={() => setLines((current) => current.filter((_, p) => p !== index))}
                disabled={lines.length === 1}
                className="text-content-muted hover:text-danger text-[12px] disabled:opacity-30"
                aria-label="Remove line"
              >
                ×
              </button>
            </div>
          ))}
          <Button
            variant="ghost"
            onClick={() =>
              setLines((current) => [
                ...current,
                { description: '', quantity: '1', unit_price: '', tax_rate: '18' },
              ])
            }
          >
            Add a line
          </Button>
        </div>

        {document.extracted_total_amount && (
          <p className="text-content-muted text-[12px]">
            The scan says the total is{' '}
            <strong className="text-content tabular-nums">
              {formatMoney(document.extracted_total_amount)}
            </strong>
            . The bill's total is computed from the lines above, so compare the two before posting -
            they should agree.
          </p>
        )}

        <div className="border-border flex flex-wrap items-center justify-between gap-3 border-t pt-3">
          <label className="text-content-secondary flex items-center gap-2 text-[13px]">
            <input
              type="checkbox"
              checked={post}
              onChange={(event) => setPost(event.target.checked)}
              className="accent-primary"
            />
            Post to the ledger immediately
          </label>
          <Button onClick={submit} disabled={confirm.isPending}>
            {confirm.isPending ? 'Creating…' : post ? 'Create and post bill' : 'Create draft bill'}
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Confidence
// ---------------------------------------------------------------------------
/**
 * How much the server trusts a value.
 *
 * `Number()` is safe here, unlike for money: this is a display threshold, not a
 * figure that lands in the ledger, so a float's last bit does not matter.
 */
function ConfidenceMeter({ value, compact }: { value: string | null; compact?: boolean }) {
  if (value === null) {
    return <span className="text-content-muted text-[11px]">-</span>;
  }

  const fraction = Number(value);
  const percent = Math.round(fraction * 100);
  const tone: BadgeTone =
    fraction >= HIGH_CONFIDENCE ? 'success' : fraction >= 0.5 ? 'warning' : 'danger';

  if (compact) {
    return (
      <span
        className={cn(
          'w-9 shrink-0 text-right text-[11px] tabular-nums',
          tone === 'success' && 'text-success',
          tone === 'warning' && 'text-warning',
          tone === 'danger' && 'text-danger',
        )}
        title={`${percent}% confident`}
      >
        {percent}%
      </span>
    );
  }

  return (
    <Badge tone={tone} title={`${percent}% confident`}>
      {percent}%
    </Badge>
  );
}
