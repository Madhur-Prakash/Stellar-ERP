/** Sales API client. Money is always a `string` - see `features/accounting/api.ts`. */
import { api } from '@/lib/api';
import type { Money, Page, PageQuery } from '@/features/accounting/api';

export type TaxTreatment = 'intra_state' | 'inter_state' | 'export' | 'exempt';
export type InvoiceStatus = 'draft' | 'posted' | 'partially_paid' | 'paid' | 'cancelled';
export type QuotationStatus = 'draft' | 'sent' | 'accepted' | 'rejected' | 'expired' | 'converted';
export type LeadStatus = 'new' | 'contacted' | 'qualified' | 'proposal_sent' | 'won' | 'lost';
export type PaymentMethod = 'cash' | 'bank_transfer' | 'cheque' | 'upi' | 'card' | 'other';

// ---------------------------------------------------------------------------
// Customers
// ---------------------------------------------------------------------------
export interface Customer {
  id: string;
  code: string;
  name: string;
  legal_name: string | null;
  email: string | null;
  phone: string | null;
  contact_person: string | null;
  gstin: string | null;
  state_code: string | null;
  is_tax_exempt: boolean;
  billing_city: string | null;
  billing_state: string | null;
  billing_country: string;
  payment_terms_days: number;
  credit_limit: Money;
  default_discount_percent: Money;
  currency: string;
  is_active: boolean;
  notes: string | null;
}

export interface CustomerStatement {
  customer: Customer;
  invoice_count: number;
  total_invoiced: Money;
  total_paid: Money;
  total_outstanding: Money;
  overdue_amount: Money;
  credit_limit: Money;
  credit_available: Money;
}

// ---------------------------------------------------------------------------
// Documents
// ---------------------------------------------------------------------------
export interface SalesLine {
  id: string;
  line_number: number;
  description: string;
  hsn_code: string | null;
  quantity: Money;
  unit: string | null;
  unit_price: Money;
  discount_percent: Money;
  discount_amount: Money;
  tax_rate: Money;
  cgst_amount: Money;
  sgst_amount: Money;
  igst_amount: Money;
  gross_amount: Money;
  taxable_amount: Money;
  tax_amount: Money;
  line_total: Money;
}

export interface SalesLineInput {
  description: string;
  hsn_code?: string | null;
  quantity: Money;
  unit?: string | null;
  unit_price: Money;
  discount_percent?: Money;
  tax_rate?: Money;
}

interface DocumentTotals {
  subtotal: Money;
  discount_total: Money;
  taxable_total: Money;
  cgst_total: Money;
  sgst_total: Money;
  igst_total: Money;
  tax_total: Money;
  round_off: Money;
  grand_total: Money;
  currency: string;
  tax_treatment: TaxTreatment;
}

export interface Invoice extends DocumentTotals {
  id: string;
  invoice_number: string;
  customer_id: string;
  customer_name: string;
  sales_order_id: string | null;
  invoice_date: string;
  due_date: string;
  status: InvoiceStatus;
  paid_amount: Money;
  outstanding: Money;
  is_overdue: boolean;
  days_overdue: number;
  customer_gstin: string | null;
  journal_entry_id: string | null;
  posted_at: string | null;
  notes: string | null;
  lines: SalesLine[];
}

export interface Quotation extends DocumentTotals {
  id: string;
  quotation_number: string;
  customer_id: string;
  customer_name: string;
  quotation_date: string;
  valid_until: string | null;
  status: QuotationStatus;
  is_expired: boolean;
  notes: string | null;
  lines: SalesLine[];
}

export interface Lead {
  id: string;
  name: string;
  company: string | null;
  email: string | null;
  phone: string | null;
  status: LeadStatus;
  source: string | null;
  estimated_value: Money;
  expected_close_date: string | null;
  converted_customer_id: string | null;
  notes: string | null;
}

export interface PaymentAllocation {
  id: string;
  invoice_id: string;
  invoice_number: string;
  amount: Money;
}

export interface Payment {
  id: string;
  payment_number: string;
  customer_id: string;
  customer_name: string;
  payment_date: string;
  amount: Money;
  unallocated_amount: Money;
  allocated_amount: Money;
  method: PaymentMethod;
  reference: string | null;
  currency: string;
  allocations: PaymentAllocation[];
}

// ---------------------------------------------------------------------------
// Reports
// ---------------------------------------------------------------------------
export interface AgeingBucket {
  label: string;
  amount: Money;
  invoice_count: number;
}

export interface ReceivablesAgeing {
  as_of: string;
  buckets: AgeingBucket[];
  total_outstanding: Money;
  total_overdue: Money;
}

export interface SalesSummary {
  from_date: string;
  to_date: string;
  invoice_count: number;
  gross_sales: Money;
  tax_collected: Money;
  net_sales: Money;
  payments_received: Money;
  outstanding: Money;
}

export const salesApi = {
  customers: (params?: PageQuery & { q?: string }) =>
    api.get<Page<Customer>>('/customers', { params }),
  customer: (id: string) => api.get<Customer>(`/customers/${id}`),
  createCustomer: (body: Partial<Customer> & { name: string }) =>
    api.post<Customer>('/customers', body),
  updateCustomer: (id: string, body: Partial<Customer>) =>
    api.patch<Customer>(`/customers/${id}`, body),
  statement: (id: string) => api.get<CustomerStatement>(`/customers/${id}/statement`),

  leads: (params?: PageQuery & { status?: LeadStatus; open_only?: boolean }) =>
    api.get<Page<Lead>>('/leads', { params }),
  createLead: (body: { name: string; company?: string; estimated_value?: Money }) =>
    api.post<Lead>('/leads', body),
  convertLead: (id: string) => api.post<Customer>(`/leads/${id}/convert`, {}),
  pipeline: () => api.get<Record<string, Money>>('/leads/pipeline'),

  quotations: (params?: PageQuery & { status?: QuotationStatus }) =>
    api.get<Page<Quotation>>('/quotations', { params }),
  createQuotation: (body: {
    customer_id: string;
    lines: SalesLineInput[];
    valid_until?: string;
    notes?: string;
  }) => api.post<Quotation>('/quotations', body),
  sendQuotation: (id: string) => api.post<Quotation>(`/quotations/${id}/send`),
  acceptQuotation: (id: string) => api.post<Quotation>(`/quotations/${id}/accept`),
  quotationToOrder: (id: string) => api.post<unknown>(`/sales-orders/from-quotation/${id}`),

  invoices: (
    params?: PageQuery & { status?: InvoiceStatus; customer_id?: string; overdue_only?: boolean },
  ) => api.get<Page<Invoice>>('/invoices', { params }),
  invoice: (id: string) => api.get<Invoice>(`/invoices/${id}`),
  createInvoice: (body: {
    customer_id: string;
    lines: SalesLineInput[];
    invoice_date?: string;
    due_date?: string;
    notes?: string;
    round_to_whole?: boolean;
    post?: boolean;
  }) => api.post<Invoice>('/invoices', body),
  postInvoice: (id: string) => api.post<Invoice>(`/invoices/${id}/post`),
  cancelInvoice: (id: string, reason: string) =>
    api.post<Invoice>(`/invoices/${id}/cancel`, { reason }),
  ageing: () => api.get<ReceivablesAgeing>('/invoices/ageing'),
  summary: (params: { from_date: string; to_date: string }) =>
    api.get<SalesSummary>('/invoices/summary', { params }),

  payments: (params?: PageQuery & { customer_id?: string }) =>
    api.get<Page<Payment>>('/payments', { params }),
  recordPayment: (body: {
    customer_id: string;
    amount: Money;
    method?: PaymentMethod;
    reference?: string;
    allocations?: { invoice_id: string; amount: Money }[];
  }) => api.post<Payment>('/payments', body),
  autoAllocate: (id: string) => api.post<Payment>(`/payments/${id}/auto-allocate`),
};
