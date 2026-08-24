/** Purchasing and inventory API client. Money is always a `string`. */
import { api } from '@/lib/api';
import type { Money, Page, PageQuery } from '@/features/accounting/api';

export type ProductKind = 'stocked' | 'service' | 'consumable';
export type BillStatus = 'draft' | 'posted' | 'partially_paid' | 'paid' | 'cancelled';
export type ReceiptStatus = 'draft' | 'posted' | 'cancelled';
export type PurchaseOrderStatus =
  'draft' | 'pending_approval' | 'approved' | 'partially_received' | 'received' | 'cancelled';
export type MovementKind =
  | 'receipt'
  | 'issue'
  | 'adjustment'
  | 'transfer_out'
  | 'transfer_in'
  | 'return_out'
  | 'return_in'
  | 'reversal';

export interface Supplier {
  id: string;
  code: string;
  name: string;
  email: string | null;
  phone: string | null;
  contact_person: string | null;
  gstin: string | null;
  state_code: string | null;
  city: string | null;
  payment_terms_days: number;
  currency: string;
  is_active: boolean;
}

export interface Product {
  id: string;
  sku: string;
  name: string;
  description: string | null;
  barcode: string | null;
  kind: ProductKind;
  hsn_code: string | null;
  unit: string;
  tax_rate: Money;
  sale_price: Money;
  purchase_price: Money;
  reorder_level: Money;
  is_active: boolean;
  tracks_stock: boolean;
  /** Present on the list endpoint, which aggregates across warehouses. */
  quantity_on_hand?: Money;
  stock_value?: Money;
  needs_reorder?: boolean;
}

export interface Warehouse {
  id: string;
  code: string;
  name: string;
  city: string | null;
  is_default: boolean;
  is_active: boolean;
}

export interface StockLevel {
  product_id: string;
  product_sku: string;
  product_name: string;
  warehouse_id: string;
  warehouse_code: string;
  quantity: Money;
  reserved_quantity: Money;
  available_quantity: Money;
  average_cost: Money;
  total_value: Money;
  last_movement_at: string | null;
}

export interface StockMovement {
  id: string;
  created_at: string;
  product_id: string;
  product_name: string;
  warehouse_code: string;
  kind: MovementKind;
  movement_date: string;
  quantity: Money;
  unit_cost: Money;
  total_cost: Money;
  balance_after: Money;
  average_cost_after: Money;
  source_type: string | null;
  reference: string | null;
  notes: string | null;
  journal_entry_id: string | null;
}

export interface StockValuationRow {
  product_id: string;
  sku: string;
  name: string;
  quantity: Money;
  average_cost: Money;
  total_value: Money;
}

export interface StockValuation {
  as_of: string;
  rows: StockValuationRow[];
  total_value: Money;
  product_count: number;
}

export interface ReorderRow {
  product_id: string;
  sku: string;
  name: string;
  quantity_on_hand: Money;
  reorder_level: Money;
  shortfall: Money;
}

export interface PurchaseLine {
  id: string;
  line_number: number;
  product_id: string | null;
  description: string;
  quantity: Money;
  unit_price: Money;
  tax_rate: Money;
  cgst_amount: Money;
  sgst_amount: Money;
  igst_amount: Money;
  taxable_amount: Money;
  tax_amount: Money;
  line_total: Money;
}

export interface PurchaseLineInput {
  product_id?: string | null;
  description: string;
  quantity: Money;
  unit_price: Money;
  tax_rate?: Money;
}

export interface Bill {
  id: string;
  bill_number: string;
  supplier_invoice_number: string | null;
  supplier_id: string;
  supplier_name: string;
  goods_receipt_id: string | null;
  bill_date: string;
  due_date: string;
  status: BillStatus;
  paid_amount: Money;
  outstanding: Money;
  is_overdue: boolean;
  taxable_total: Money;
  tax_total: Money;
  grand_total: Money;
  currency: string;
  journal_entry_id: string | null;
  lines: PurchaseLine[];
}

export interface GoodsReceiptLine {
  id: string;
  line_number: number;
  product_id: string;
  product_name: string;
  quantity: Money;
  unit_cost: Money;
  total_cost: Money;
  rejected_quantity: Money;
  accepted_quantity: Money;
}

export interface GoodsReceipt {
  id: string;
  receipt_number: string;
  supplier_id: string;
  supplier_name: string;
  purchase_order_id: string | null;
  warehouse_code: string;
  receipt_date: string;
  supplier_reference: string | null;
  status: ReceiptStatus;
  total_cost: Money;
  journal_entry_id: string | null;
  lines: GoodsReceiptLine[];
}

export interface PurchaseOrder {
  id: string;
  order_number: string;
  supplier_id: string;
  supplier_name: string;
  order_date: string;
  expected_date: string | null;
  status: PurchaseOrderStatus;
  taxable_total: Money;
  tax_total: Money;
  grand_total: Money;
  currency: string;
  lines: (PurchaseLine & { received_quantity: Money; outstanding_quantity: Money })[];
}

export interface PayablesAgeing {
  as_of: string;
  buckets: { label: string; amount: Money; bill_count: number }[];
  total_outstanding: Money;
  total_overdue: Money;
}

export const inventoryApi = {
  suppliers: (params?: PageQuery) => api.get<Page<Supplier>>('/suppliers', { params }),
  /**
   * The body mirrors `SupplierCreate`, which is `extra="forbid"` - an unrecognised
   * field is a 422, so this type is deliberately explicit rather than
   * `Partial<Supplier>`. Note `city`, not `billing_city`: a supplier has one address,
   * unlike a customer.
   */
  createSupplier: (body: {
    name: string;
    code?: string;
    gstin?: string;
    email?: string;
    phone?: string;
    contact_person?: string;
    city?: string;
    state?: string;
    payment_terms_days?: number;
    notes?: string;
  }) => api.post<Supplier>('/suppliers', body),

  products: (params?: PageQuery & { q?: string; kind?: ProductKind }) =>
    api.get<Page<Product>>('/products', { params }),
  product: (id: string) => api.get<Product>(`/products/${id}`),
  createProduct: (body: {
    name: string;
    sku?: string;
    barcode?: string;
    kind?: ProductKind;
    unit?: string;
    tax_rate?: Money;
    sale_price?: Money;
    purchase_price?: Money;
    reorder_level?: Money;
  }) => api.post<Product>('/products', body),
  /**
   * Mirrors `ProductUpdate`, which is `extra="forbid"` — `Partial<Product>` was wrong
   * here, because it let read-only fields like `sku` and `quantity_on_hand` type-check
   * and then 422 at runtime. `sku` is genuinely not updatable: it may already be printed
   * on a label or quoted on a bill.
   */
  updateProduct: (
    id: string,
    body: {
      name?: string;
      description?: string | null;
      barcode?: string;
      hsn_code?: string;
      unit?: string;
      tax_rate?: Money;
      sale_price?: Money;
      purchase_price?: Money;
      reorder_level?: Money;
      is_active?: boolean;
    },
  ) => api.patch<Product>(`/products/${id}`, body),
  byBarcode: (barcode: string) =>
    api.get<Product>(`/products/by-barcode/${encodeURIComponent(barcode)}`),
  reorderReport: () => api.get<ReorderRow[]>('/products/reorder'),

  warehouses: () => api.get<Warehouse[]>('/inventory/warehouses'),
  createWarehouse: (body: { code: string; name: string; is_default?: boolean }) =>
    api.post<Warehouse>('/inventory/warehouses', body),

  levels: (params?: { warehouse_id?: string; product_id?: string }) =>
    api.get<StockLevel[]>('/inventory/levels', { params }),
  movements: (params?: PageQuery & { product_id?: string; warehouse_id?: string }) =>
    api.get<Page<StockMovement>>('/inventory/movements', { params }),
  valuation: (params?: { warehouse_id?: string }) =>
    api.get<StockValuation>('/inventory/valuation', { params }),
  adjust: (body: {
    product_id: string;
    quantity_delta: Money;
    reason: string;
    warehouse_id?: string;
  }) => api.post<StockMovement>('/inventory/adjust', body),
  transfer: (body: {
    product_id: string;
    from_warehouse_id: string;
    to_warehouse_id: string;
    quantity: Money;
  }) => api.post<StockMovement[]>('/inventory/transfer', body),

  purchaseOrders: (params?: PageQuery & { status?: PurchaseOrderStatus }) =>
    api.get<Page<PurchaseOrder>>('/purchase-orders', { params }),
  createPurchaseOrder: (body: { supplier_id: string; lines: PurchaseLineInput[] }) =>
    api.post<PurchaseOrder>('/purchase-orders', body),
  approvePurchaseOrder: (id: string) => api.post<PurchaseOrder>(`/purchase-orders/${id}/approve`),

  receipts: (params?: PageQuery & { status?: ReceiptStatus }) =>
    api.get<Page<GoodsReceipt>>('/goods-receipts', { params }),
  createReceipt: (body: {
    supplier_id: string;
    purchase_order_id?: string;
    lines: { product_id: string; quantity: Money; unit_cost: Money }[];
    post?: boolean;
  }) => api.post<GoodsReceipt>('/goods-receipts', body),
  postReceipt: (id: string) => api.post<GoodsReceipt>(`/goods-receipts/${id}/post`),

  bills: (params?: PageQuery & { status?: BillStatus; overdue_only?: boolean }) =>
    api.get<Page<Bill>>('/bills', { params }),
  bill: (id: string) => api.get<Bill>(`/bills/${id}`),
  createBill: (body: {
    supplier_id: string;
    supplier_invoice_number?: string;
    goods_receipt_id?: string;
    lines: PurchaseLineInput[];
    post?: boolean;
  }) => api.post<Bill>('/bills', body),
  postBill: (id: string) => api.post<Bill>(`/bills/${id}/post`),
  payablesAgeing: () => api.get<PayablesAgeing>('/bills/ageing'),

  paySupplier: (body: {
    supplier_id: string;
    amount: Money;
    method?: string;
    reference?: string;
    allocations?: { bill_id: string; amount: Money }[];
  }) => api.post<unknown>('/supplier-payments', body),
};
