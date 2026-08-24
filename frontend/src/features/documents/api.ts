/**
 * Scanned documents API client.
 *
 * Money is a `string` throughout - see `features/accounting/api.ts` for why it is
 * never widened to `number`. Confidence is a string for the same reason: it is a
 * `Decimal` server-side, and JSON's only numeric type is a float.
 */
import { api, http } from '@/lib/api';
import type { Money, Page, PageQuery } from '@/features/accounting/api';
import type { Bill, PurchaseLineInput } from '@/features/inventory/api';

export type DocumentFormat =
  'application/pdf' | 'image/png' | 'image/jpeg' | 'image/tiff' | 'image/webp';

export type DocumentKind = 'purchase_invoice' | 'receipt' | 'quotation' | 'other';
export type DocumentStatus = 'uploaded' | 'extracted' | 'confirmed' | 'rejected' | 'failed';

/**
 * A confidence score, 0–1, as a decimal string.
 *
 * Compared with `Number()` only at the point of display - a threshold check for
 * "should this be highlighted?" does not need exact decimal arithmetic, unlike
 * money, which lands in the ledger.
 */
export type Confidence = string;

export interface OcrCapabilities {
  enabled: boolean;
  engines: string[];
  formats: DocumentFormat[];
  max_bytes: number;
  any_engine_available: boolean;
}

export interface DocumentSummary {
  id: string;
  created_at: string;
  updated_at: string;
  original_filename: string;
  content_type: DocumentFormat;
  byte_size: number;
  kind: DocumentKind;
  status: DocumentStatus;

  extracted_supplier_name: string | null;
  extracted_supplier_gstin: string | null;
  extracted_invoice_number: string | null;
  extracted_invoice_date: string | null;
  extracted_total_amount: Money | null;

  overall_confidence: Confidence | null;
  totals_reconcile: boolean;
  needs_review: boolean;
  is_duplicate: boolean;

  matched_supplier_id: string | null;
  matched_supplier_name: string | null;
  bill_id: string | null;
  duplicate_of_id: string | null;
}

export interface Document extends DocumentSummary {
  sha256: string;
  engine: string | null;
  engine_confidence: Confidence | null;
  page_count: number | null;

  extracted_subtotal: Money | null;
  extracted_tax_amount: Money | null;

  field_confidence: Record<string, Confidence>;
  low_confidence_fields: string[];
  /** Fields a human has typed over. Their confidence is 1 because a person set them,
   *  which is not the same claim as "the engine was sure" - only this list separates
   *  the two. */
  corrected_fields: string[];

  failure_code: string | null;
  failure_message: string | null;

  bill_number: string | null;
  reviewed_at: string | null;
  notes: string | null;
}

export interface DocumentText {
  document_id: string;
  engine: string | null;
  engine_confidence: Confidence | null;
  page_count: number | null;
  text: string;
}

export interface DuplicateWarning {
  document_id: string;
  status: DocumentStatus;
  bill_id: string | null;
  bill_number: string | null;
  uploaded_at: string;
  reason: string;
}

export interface UploadResult {
  document: Document;
  duplicate: DuplicateWarning | null;
  already_uploaded: boolean;
}

export interface ConfirmResult {
  document: Document;
  bill: Bill;
}

/**
 * Corrections to what the engine read.
 *
 * **Omission and `null` mean different things**, and the endpoint is a PATCH precisely
 * so they can: a key that is absent leaves that field alone, while `null` clears it.
 * Build this by including only the fields the reviewer actually changed - sending the
 * whole form back would rewrite six fields to say what they already said, each one
 * marked as a human correction.
 */
export interface DocumentFieldsUpdate {
  supplier_name?: string | null;
  supplier_gstin?: string | null;
  invoice_number?: string | null;
  invoice_date?: string | null;
  subtotal?: Money | null;
  tax_amount?: Money | null;
  total_amount?: Money | null;
}

export interface BillFromDocument {
  supplier_id: string;
  supplier_invoice_number?: string | null;
  bill_date?: string | null;
  due_date?: string | null;
  lines: PurchaseLineInput[];
  notes?: string | null;
  post?: boolean;
}

export const documentsApi = {
  capabilities: () => api.get<OcrCapabilities>('/documents/capabilities'),

  list: (
    params?: PageQuery & {
      status?: DocumentStatus;
      kind?: DocumentKind;
      needs_review?: boolean;
      q?: string;
    },
  ) => api.get<Page<DocumentSummary>>('/documents', { params }),

  get: (id: string) => api.get<Document>(`/documents/${id}`),
  text: (id: string) => api.get<DocumentText>(`/documents/${id}/text`),

  /**
   * Upload one file.
   *
   * `Content-Type` is deleted rather than set: the browser has to generate the
   * multipart boundary, and an explicit `multipart/form-data` header without one
   * makes the server unable to parse the body. The axios instance sets
   * `application/json` by default, so it must be removed here.
   */
  upload: (file: File, kind: DocumentKind = 'purchase_invoice') => {
    const form = new FormData();
    form.append('file', file);
    form.append('kind', kind);
    return api.post<UploadResult>('/documents', form, {
      headers: { 'Content-Type': undefined },
      // Recognition runs inline and a scanned page can take several seconds, so
      // the default 30s client timeout is too tight for this one call.
      timeout: 120_000,
    });
  },

  reextract: (id: string) => api.post<Document>(`/documents/${id}/reextract`),

  /** Correct what was read. Send only the fields that changed - see the type. */
  correct: (id: string, fields: DocumentFieldsUpdate) =>
    api.patch<Document>(`/documents/${id}/extracted`, fields),

  confirm: (id: string, bill: BillFromDocument) =>
    api.post<ConfirmResult>(`/documents/${id}/confirm`, { bill }),
  remove: (id: string) => api.delete<{ message: string }>(`/documents/${id}`),

  /**
   * Fetch the original file as an object URL for preview.
   *
   * Goes through `http` rather than a bare `<img src>` or `<iframe src>` because
   * the endpoint requires the `Authorization` header, which a browser-initiated
   * subresource request cannot carry. The caller must revoke the URL when done or
   * the blob leaks for the lifetime of the document.
   */
  fileUrl: async (id: string): Promise<string> => {
    const response = await http.get(`/documents/${id}/file`, { responseType: 'blob' });
    return URL.createObjectURL(response.data as Blob);
  },
};
