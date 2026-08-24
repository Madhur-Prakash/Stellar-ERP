/**
 * Sales - invoices, customers, and receivables.
 *
 * The invoice list is the default view because it is what a small business opens
 * this software to look at: who owes money, and how overdue.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useSearch } from '@tanstack/react-router';
import { AlertCircle, Plus, Trash2 } from 'lucide-react';
import { useMemo, useState } from 'react';
import { toast } from 'sonner';

import { Badge, type BadgeTone } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import type { Column } from '@/components/ui/DataTable';
import { DataTable, PageHeader, Pagination } from '@/components/ui/DataTable';
import { Input } from '@/components/ui/Input';
import { NumberInput } from '@/components/ui/NumberInput';
import { PartyFormModal } from '@/features/sales/PartyForm';
import {
  type Customer,
  type Invoice,
  type InvoiceStatus,
  type Payment,
  type SalesLineInput,
  salesApi,
} from '@/features/sales/api';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';
import { formatDate, formatMoney, isZeroMoney } from '@/lib/format';

type Tab = 'invoices' | 'customers' | 'payments' | 'ageing';

const STATUS_TONES: Record<InvoiceStatus, BadgeTone> = {
  draft: 'neutral',
  posted: 'info',
  partially_paid: 'warning',
  paid: 'success',
  cancelled: 'danger',
};

const STATUS_LABELS: Record<InvoiceStatus, string> = {
  draft: 'Draft',
  posted: 'Unpaid',
  partially_paid: 'Part paid',
  paid: 'Paid',
  cancelled: 'Cancelled',
};

/** Narrows an untrusted search param to a known tab, so a hand-edited query
 *  string falls back to the default instead of breaking the page. */
const TAB_KEYS = ['invoices', 'customers', 'payments', 'ageing'] as const;

function isTab(value: unknown): value is Tab {
  return typeof value === 'string' && (TAB_KEYS as readonly string[]).includes(value);
}

export function InvoicesPage() {
  // The tab lives in the URL, not in component state, so a reload returns to it and
  // the view can be linked to. Read untyped and narrowed by `isTab`: that is safer
  // than a typed `from`, because a hand-edited query string then falls back to the
  // default rather than throwing.
  const navigate = useNavigate();
  const search = useSearch({ strict: false });
  const tab: Tab = isTab(search.tab) ? search.tab : 'invoices';
  const [composing, setComposing] = useState(false);

  const setTab = (next: Tab) => {
    // Close the composer on the way out. It belongs to the Invoices tab, and a
    // half-written invoice hovering above the customer list is confusing — worse, the
    // "New invoice" button is only on that tab, so there was no way to dismiss it.
    setComposing(false);
    // `replace` keeps tab switching out of the back stack.
    void navigate({ to: '/invoices', search: { tab: next }, replace: true });
  };

  return (
    <div>
      <PageHeader
        title="Sales"
        description="Invoices post straight to the ledger. A posted invoice is a statutory record and cannot be edited."
        action={
          tab === 'invoices' ? (
            <Button onClick={() => setComposing(true)} leftIcon={<Plus />}>
              New invoice
            </Button>
          ) : undefined
        }
      />

      <div className="border-border mb-4 flex gap-1 overflow-x-auto border-b" role="tablist">
        {(
          [
            ['invoices', 'Invoices'],
            ['customers', 'Customers'],
            ['payments', 'Payments'],
            ['ageing', 'Ageing'],
          ] as [Tab, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={cn(
              'shrink-0 border-b-2 px-3 py-2 text-[13px] font-medium transition-colors',
              tab === key
                ? 'border-primary text-content'
                : 'text-content-muted hover:text-content border-transparent',
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'invoices' && composing && <InvoiceComposer onClose={() => setComposing(false)} />}

      {tab === 'invoices' && <InvoiceList />}
      {tab === 'customers' && <CustomerList />}
      {tab === 'payments' && <PaymentList />}
      {tab === 'ageing' && <AgeingReport />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Invoice list
// ---------------------------------------------------------------------------
function InvoiceList() {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [overdueOnly, setOverdueOnly] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ['invoices', page, overdueOnly],
    queryFn: () => salesApi.invoices({ page, page_size: 25, overdue_only: overdueOnly }),
  });

  const post = useMutation({
    mutationFn: (id: string) => salesApi.postInvoice(id),
    onSuccess: (invoice) => {
      toast.success(`${invoice.invoice_number} posted to the ledger`);
      void queryClient.invalidateQueries({ queryKey: ['invoices'] });
      void queryClient.invalidateQueries({ queryKey: ['trial-balance'] });
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not post the invoice'),
  });

  const columns: Column<Invoice>[] = [
    {
      header: 'Invoice',
      cell: (row) => (
        <div>
          <p className="text-content font-mono text-[12px]">{row.invoice_number}</p>
          <p className="text-content-muted text-[11px]">{formatDate(row.invoice_date)}</p>
        </div>
      ),
    },
    { header: 'Customer', cell: (row) => row.customer_name },
    {
      header: 'Due',
      hideOnMobile: true,
      cell: (row) => (
        <span className={cn(row.is_overdue && 'text-danger font-medium')}>
          {formatDate(row.due_date)}
          {row.is_overdue && <span className="ml-1 text-[11px]">({row.days_overdue}d late)</span>}
        </span>
      ),
    },
    {
      header: 'Status',
      cell: (row) => <Badge tone={STATUS_TONES[row.status]}>{STATUS_LABELS[row.status]}</Badge>,
    },
    { header: 'Total', numeric: true, cell: (row) => formatMoney(row.grand_total) },
    {
      header: 'Outstanding',
      numeric: true,
      cell: (row) =>
        isZeroMoney(row.outstanding) ? (
          <span className="text-content-muted">-</span>
        ) : (
          <span className="text-content font-medium">{formatMoney(row.outstanding)}</span>
        ),
    },
    {
      header: '',
      cell: (row) =>
        row.status === 'draft' ? (
          <Button
            size="sm"
            variant="secondary"
            loading={post.isPending && post.variables === row.id}
            onClick={() => post.mutate(row.id)}
          >
            Post
          </Button>
        ) : null,
    },
  ];

  return (
    <Card>
      <CardHeader
        title="Invoices"
        action={
          <label className="text-content-secondary flex items-center gap-2 text-[12px]">
            <input
              type="checkbox"
              checked={overdueOnly}
              onChange={(event) => {
                setOverdueOnly(event.target.checked);
                setPage(1);
              }}
              className="accent-primary"
            />
            Overdue only
          </label>
        }
      />
      <DataTable
        columns={columns}
        rows={data?.items ?? []}
        rowKey={(row) => row.id}
        isLoading={isLoading}
        empty={{
          title: overdueOnly ? 'Nothing overdue' : 'No invoices yet',
          description: overdueOnly
            ? 'Every posted invoice is within its payment terms.'
            : 'Create one to bill a customer.',
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
  );
}

// ---------------------------------------------------------------------------
// Invoice composer
// ---------------------------------------------------------------------------
interface DraftLine {
  description: string;
  quantity: string;
  unit_price: string;
  tax_rate: string;
}

const BLANK_LINE: DraftLine = { description: '', quantity: '1', unit_price: '', tax_rate: '18' };

function InvoiceComposer({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [customerId, setCustomerId] = useState('');
  const [lines, setLines] = useState<DraftLine[]>([{ ...BLANK_LINE }]);
  const [postNow, setPostNow] = useState(true);
  const [addingCustomer, setAddingCustomer] = useState(false);

  const { data: customers } = useQuery({
    queryKey: ['customers', 'all'],
    queryFn: () => salesApi.customers({ page_size: 200 }),
  });

  /**
   * A local preview only. The server recomputes every figure and its answer is
   * authoritative - this exists so the user is not typing blind, not to be the
   * source of the totals.
   */
  const preview = useMemo(() => {
    let taxable = 0;
    let tax = 0;
    for (const line of lines) {
      const quantity = Number(line.quantity) || 0;
      const price = Number(line.unit_price) || 0;
      const rate = Number(line.tax_rate) || 0;
      const base = quantity * price;
      taxable += base;
      tax += (base * rate) / 100;
    }
    return { taxable, tax, total: taxable + tax };
  }, [lines]);

  const create = useMutation({
    mutationFn: () =>
      salesApi.createInvoice({
        customer_id: customerId,
        post: postNow,
        lines: lines.map((line): SalesLineInput => ({
          description: line.description,
          quantity: line.quantity,
          unit_price: line.unit_price,
          tax_rate: line.tax_rate,
        })),
      }),
    onSuccess: (invoice) => {
      toast.success(
        `${invoice.invoice_number} created`,
        postNow ? { description: 'Posted to the ledger.' } : undefined,
      );
      void queryClient.invalidateQueries({ queryKey: ['invoices'] });
      void queryClient.invalidateQueries({ queryKey: ['trial-balance'] });
      onClose();
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not create the invoice'),
  });

  const canSubmit =
    customerId !== '' &&
    lines.length > 0 &&
    lines.every((line) => line.description.trim() !== '' && Number(line.unit_price) > 0);

  return (
    <Card className="mb-4">
      <CardHeader
        title="New invoice"
        description="Totals and the GST split are computed by the server from these lines."
      />
      <CardBody className="space-y-4">
        <div>
          <label
            htmlFor="invoice-customer"
            className="text-content-secondary mb-1.5 block text-[13px] font-medium"
          >
            Customer
          </label>
          <div className="flex gap-2">
            <select
              id="invoice-customer"
              value={customerId}
              onChange={(event) => setCustomerId(event.target.value)}
              className="border-border bg-surface text-content focus:border-primary w-full rounded-lg border px-3 py-2 text-[13px] outline-none"
            >
              <option value="">Select a customer…</option>
              {customers?.items.map((customer) => (
                <option key={customer.id} value={customer.id}>
                  {customer.name}
                  {customer.gstin ? ` · ${customer.gstin}` : ''}
                </option>
              ))}
            </select>
            {/* Inline, because the alternative is abandoning a half-typed invoice
                to go to another tab. On first use the list is empty, so this is the
                only path forward - the button is emphasised in that case. */}
            <Button
              variant={customers && customers.items.length === 0 ? 'primary' : 'secondary'}
              onClick={() => setAddingCustomer(true)}
              className="shrink-0"
            >
              <Plus className="h-3.5 w-3.5" aria-hidden />
              New
            </Button>
          </div>
          {customers && customers.items.length === 0 && (
            <p className="text-content-muted mt-1.5 text-[12px]">
              No customers yet - add one to raise your first invoice.
            </p>
          )}
        </div>

        <PartyFormModal
          kind="customer"
          open={addingCustomer}
          onClose={() => setAddingCustomer(false)}
          /* Select it straight away: the user asked for this customer in order to
             invoice them, so making them find it in the list again is busywork. */
          onCreated={(id) => setCustomerId(id)}
        />

        <div className="space-y-2">
          {lines.map((line, index) => (
            <div key={index} className="grid grid-cols-12 items-end gap-2">
              <div className="col-span-12 sm:col-span-5">
                <Input
                  label={index === 0 ? 'Description' : undefined}
                  placeholder="Widget"
                  value={line.description}
                  onChange={(event) =>
                    setLines((current) =>
                      current.map((item, position) =>
                        position === index ? { ...item, description: event.target.value } : item,
                      ),
                    )
                  }
                />
              </div>
              <div className="col-span-4 sm:col-span-2">
                <NumberInput
                  label={index === 0 ? 'Qty' : undefined}
                  value={line.quantity}
                  onValueChange={(quantity) =>
                    setLines((current) =>
                      current.map((item, position) =>
                        position === index ? { ...item, quantity } : item,
                      ),
                    )
                  }
                />
              </div>
              <div className="col-span-4 sm:col-span-2">
                <NumberInput
                  label={index === 0 ? 'Price' : undefined}
                  value={line.unit_price}
                  onValueChange={(unit_price) =>
                    setLines((current) =>
                      current.map((item, position) =>
                        position === index ? { ...item, unit_price } : item,
                      ),
                    )
                  }
                />
              </div>
              <div className="col-span-3 sm:col-span-2">
                <NumberInput
                  label={index === 0 ? 'GST %' : undefined}
                  value={line.tax_rate}
                  onValueChange={(tax_rate) =>
                    setLines((current) =>
                      current.map((item, position) =>
                        position === index ? { ...item, tax_rate } : item,
                      ),
                    )
                  }
                />
              </div>
              <div className="col-span-1 flex justify-end">
                {lines.length > 1 && (
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`Remove line ${index + 1}`}
                    onClick={() =>
                      setLines((current) => current.filter((_, position) => position !== index))
                    }
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                )}
              </div>
            </div>
          ))}
          <Button
            variant="ghost"
            size="sm"
            leftIcon={<Plus />}
            onClick={() => setLines((current) => [...current, { ...BLANK_LINE }])}
          >
            Add line
          </Button>
        </div>

        <div className="bg-surface-sunken space-y-1 rounded-lg p-3 text-[13px]">
          <div className="text-content-muted flex justify-between">
            <span>Taxable (estimate)</span>
            <span className="tabular-nums">{preview.taxable.toFixed(2)}</span>
          </div>
          <div className="text-content-muted flex justify-between">
            <span>GST (estimate)</span>
            <span className="tabular-nums">{preview.tax.toFixed(2)}</span>
          </div>
          <div className="border-border text-content flex justify-between border-t pt-1 font-semibold">
            <span>Total (estimate)</span>
            <span className="tabular-nums">{preview.total.toFixed(2)}</span>
          </div>
          <p className="text-content-muted pt-1 text-[11px]">
            An estimate. The server splits CGST/SGST or IGST by place of supply and its figures are
            the ones recorded.
          </p>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3">
          <label className="text-content-secondary flex items-center gap-2 text-[13px]">
            <input
              type="checkbox"
              checked={postNow}
              onChange={(event) => setPostNow(event.target.checked)}
              className="accent-primary"
            />
            Post to the ledger immediately
          </label>
          <div className="flex gap-2">
            <Button variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button
              disabled={!canSubmit}
              loading={create.isPending}
              onClick={() => create.mutate()}
            >
              {postNow ? 'Create and post' : 'Save draft'}
            </Button>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Customers
// ---------------------------------------------------------------------------
function CustomerList() {
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState('');
  const [adding, setAdding] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ['customers', page, query],
    queryFn: () => salesApi.customers({ page, page_size: 25, q: query || undefined }),
  });

  const columns: Column<Customer>[] = [
    {
      header: 'Code',
      cell: (row) => <span className="font-mono text-[12px]">{row.code}</span>,
    },
    {
      header: 'Customer',
      cell: (row) => (
        <div>
          <p className="text-content">{row.name}</p>
          {row.email && <p className="text-content-muted text-[11px]">{row.email}</p>}
        </div>
      ),
    },
    {
      header: 'GSTIN',
      hideOnMobile: true,
      cell: (row) =>
        row.gstin ? (
          <span className="font-mono text-[11px]">{row.gstin}</span>
        ) : (
          <span className="text-content-muted">-</span>
        ),
    },
    { header: 'Terms', hideOnMobile: true, cell: (row) => `${row.payment_terms_days} days` },
    { header: 'Credit limit', numeric: true, cell: (row) => formatMoney(row.credit_limit) },
  ];

  return (
    <Card>
      <CardHeader
        title="Customers"
        action={
          <div className="flex items-center gap-2">
            <Input
              placeholder="Search…"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setPage(1);
              }}
              className="w-48"
            />
            <Button onClick={() => setAdding(true)} className="shrink-0">
              <Plus className="h-3.5 w-3.5" aria-hidden />
              New customer
            </Button>
          </div>
        }
      />
      <PartyFormModal kind="customer" open={adding} onClose={() => setAdding(false)} />
      <DataTable
        columns={columns}
        rows={data?.items ?? []}
        rowKey={(row) => row.id}
        isLoading={isLoading}
        empty={{
          title: 'No customers',
          description: 'Add one to start invoicing.',
          // The empty state used to say "add one" with no way to do it.
          action: (
            <Button onClick={() => setAdding(true)}>
              <Plus className="h-3.5 w-3.5" aria-hidden />
              Add a customer
            </Button>
          ),
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
  );
}

// ---------------------------------------------------------------------------
// Payments
// ---------------------------------------------------------------------------
function PaymentList() {
  const [page, setPage] = useState(1);
  const { data, isLoading } = useQuery({
    queryKey: ['payments', page],
    queryFn: () => salesApi.payments({ page, page_size: 25 }),
  });

  const columns: Column<Payment>[] = [
    {
      header: 'Receipt',
      cell: (row) => (
        <div>
          <p className="font-mono text-[12px]">{row.payment_number}</p>
          <p className="text-content-muted text-[11px]">{formatDate(row.payment_date)}</p>
        </div>
      ),
    },
    { header: 'Customer', cell: (row) => row.customer_name },
    { header: 'Method', hideOnMobile: true, cell: (row) => row.method.replace('_', ' ') },
    { header: 'Amount', numeric: true, cell: (row) => formatMoney(row.amount) },
    {
      header: 'Unallocated',
      numeric: true,
      cell: (row) =>
        isZeroMoney(row.unallocated_amount) ? (
          <span className="text-content-muted">-</span>
        ) : (
          <Badge tone="warning">{formatMoney(row.unallocated_amount)}</Badge>
        ),
    },
  ];

  return (
    <Card>
      <DataTable
        columns={columns}
        rows={data?.items ?? []}
        rowKey={(row) => row.id}
        isLoading={isLoading}
        empty={{
          title: 'No payments recorded',
          description: 'Receipts appear here once customers start paying.',
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
  );
}

// ---------------------------------------------------------------------------
// Ageing
// ---------------------------------------------------------------------------
function AgeingReport() {
  const { data, isLoading } = useQuery({
    queryKey: ['receivables-ageing'],
    queryFn: () => salesApi.ageing(),
  });

  if (isLoading || !data) {
    return (
      <Card>
        <DataTable columns={[]} rows={[]} rowKey={() => ''} isLoading />
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <Card>
          <CardBody>
            <p className="text-content-muted text-[11px] font-semibold tracking-wider uppercase">
              Total outstanding
            </p>
            <p className="text-content mt-1.5 text-[20px] font-semibold tabular-nums">
              {formatMoney(data.total_outstanding)}
            </p>
          </CardBody>
        </Card>
        <Card className={cn(!isZeroMoney(data.total_overdue) && 'border-warning/40')}>
          <CardBody>
            <div className="flex items-center gap-2">
              {!isZeroMoney(data.total_overdue) && (
                <AlertCircle className="text-warning h-3.5 w-3.5" aria-hidden />
              )}
              <p className="text-content-muted text-[11px] font-semibold tracking-wider uppercase">
                Overdue
              </p>
            </div>
            <p className="text-content mt-1.5 text-[20px] font-semibold tabular-nums">
              {formatMoney(data.total_overdue)}
            </p>
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader title="Receivables ageing" description={`As at ${formatDate(data.as_of)}`} />
        <DataTable
          columns={[
            { header: 'Bucket', cell: (row) => row.label },
            { header: 'Invoices', numeric: true, cell: (row) => String(row.invoice_count) },
            { header: 'Amount', numeric: true, cell: (row) => formatMoney(row.amount) },
          ]}
          rows={data.buckets}
          rowKey={(row) => row.label}
        />
      </Card>
    </div>
  );
}
