/**
 * Accounting - chart of accounts, journal entries, and the financial statements.
 *
 * One page with tabs rather than four routes: an accountant moves between the
 * trial balance and the ledger constantly, and a full route transition (with its
 * refetch) on every switch is slower than keeping the queries warm in one place.
 */
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useSearch } from '@tanstack/react-router';
import type { LucideIcon } from 'lucide-react';
import {
  AlertTriangle,
  BookOpen,
  FileDown,
  FileSpreadsheet,
  Scale,
  TrendingUp,
  Undo2,
} from 'lucide-react';
import { useState } from 'react';

import { toast } from 'sonner';

import { Badge, type BadgeTone } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import type { Column } from '@/components/ui/DataTable';
import { DataTable, PageHeader, Pagination } from '@/components/ui/DataTable';
import { InfoTip } from '@/components/ui/InfoTip';
import { Skeleton } from '@/components/ui/Skeleton';
import {
  type JournalEntry,
  type ReportLine,
  type TrialBalanceRow,
  accountingApi,
} from '@/features/accounting/api';
import {
  AccountBalancesChart,
  BalanceByTypeChart,
  CashMovementChart,
} from '@/features/accounting/AccountingCharts';
import { SpendingMixChart, TrendChart } from '@/features/accounting/CompositionCharts';
import { ProfitWaterfallChart } from '@/features/accounting/WaterfallChart';
import { useReportRange } from '@/features/accounting/ReportRange';
import { analyticsApi } from '@/features/analytics/api';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';
import type { StatementPeriod } from '@/features/accounting/api';
import { formatDate, formatMoney, isZeroMoney } from '@/lib/format';

type Tab = 'chart' | 'entries' | 'trial-balance' | 'pnl' | 'balance-sheet';

const TABS: { key: Tab; label: string }[] = [
  { key: 'chart', label: 'Chart of accounts' },
  { key: 'entries', label: 'Journal entries' },
  { key: 'trial-balance', label: 'Trial balance' },
  { key: 'pnl', label: 'Profit & loss' },
  { key: 'balance-sheet', label: 'Balance sheet' },
];

/** Narrows an untrusted search param to a known tab, so a hand-edited query
 *  string falls back to the default instead of breaking the page. */
const TAB_KEYS = ['chart', 'entries', 'trial-balance', 'pnl', 'balance-sheet'] as const;

function isTab(value: unknown): value is Tab {
  return typeof value === 'string' && (TAB_KEYS as readonly string[]).includes(value);
}

export function AccountingPage() {
  // The tab lives in the URL, not in component state, so a reload returns to it and
  // the view can be linked to. Read untyped and narrowed by `isTab`: that is safer
  // than a typed `from`, because a hand-edited query string then falls back to the
  // default rather than throwing.
  const navigate = useNavigate();
  const search = useSearch({ strict: false });
  const tab: Tab = isTab(search.tab) ? search.tab : 'chart';
  const setTab = (next: Tab) => {
    // `replace` keeps tab switching out of the back stack.
    void navigate({ to: '/accounting', search: { tab: next }, replace: true });
  };

  return (
    <div>
      <PageHeader
        title="Accounting"
        description="Double-entry ledger. Posted entries are immutable - corrections are made by reversal."
      />

      <div
        className="border-border mb-4 flex gap-1 overflow-x-auto border-b"
        role="tablist"
        aria-label="Accounting views"
      >
        {TABS.map((item) => (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={tab === item.key}
            onClick={() => setTab(item.key)}
            className={cn(
              'shrink-0 border-b-2 px-3 py-2 text-[13px] font-medium transition-colors',
              tab === item.key
                ? 'border-primary text-content'
                : 'text-content-muted hover:text-content border-transparent',
            )}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === 'chart' && <ChartOfAccounts />}
      {tab === 'entries' && <JournalEntries />}
      {tab === 'trial-balance' && <TrialBalanceReport />}
      {tab === 'pnl' && <ProfitAndLossReport />}
      {tab === 'balance-sheet' && <BalanceSheetReport />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Chart of accounts
// ---------------------------------------------------------------------------
function ChartOfAccounts() {
  // One range control drives every chart on this tab. Separate filters per chart would
  // let two panels sit side by side showing different periods, which is a reliable way to
  // draw a wrong conclusion from correct numbers.
  const { range, control } = useReportRange();

  const { data, isLoading } = useQuery({
    // Balances are point-in-time, so only the end of the range applies — "cash over
    // March" is not a number.
    queryKey: ['accounts', range.to_date],
    queryFn: () => accountingApi.accounts({ as_of: range.to_date }),
  });

  // The waterfall's closing bar must equal the dashboard's net profit, so it is built
  // from the statement rather than recomputed.
  const { data: report, isLoading: reportLoading } = useQuery({
    queryKey: ['pnl', range],
    queryFn: () => accountingApi.profitAndLoss(range),
  });

  const { data: trend } = useQuery({
    queryKey: ['analytics-trend', range],
    queryFn: () => analyticsApi.trend('last_12_months', range),
  });

  const accounts = data ?? [];

  // The 114-row table is gone. It listed every account in the template, of which four
  // hold a balance, so it was a hundred rows of ₹0.00 in front of the four figures
  // anyone came here for. The charts show what has money; the trial balance is the
  // place to read exact per-account figures.
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Period"
          description={`Every chart below covers ${range.from_date} to ${range.to_date}.`}
          action={control}
        />
      </Card>

      <ProfitWaterfallChart report={report} isLoading={reportLoading} />

      {isLoading ? (
        <Card>
          <CardBody className="pt-5">
            <Skeleton className="h-64 w-full" />
          </CardBody>
        </Card>
      ) : (
        <>
          <div className="grid gap-4 xl:grid-cols-2">
            <AccountBalancesChart accounts={accounts} />
            <SpendingMixChart accounts={accounts} />
          </div>

          <TrendChart points={trend?.points} />

          <BalanceByTypeChart accounts={accounts} />
        </>
      )}
    </div>
  );
}

/**
 * The parties an account has dealt with.
 *
 * One column, not a from/to pair. An account that both received from and paid the same
 * person showed that name in both columns, which reads as a contradiction even though it
 * is exactly what happened - because direction belongs to a transaction and this row is a
 * balance over many of them. The Billing day book states each movement's direction, and so
 * does the journal.
 *
 * Names as typed into Billing's From/To field, never account names. An earlier version
 * filled blanks with the counter-account, which put "Cash on Hand" and "Salaries & Wages"
 * down the column - the chart of accounts restated, which the Account column already says.
 *
 * A dash means the entries behind this balance named nobody, which is the honest answer
 * for anything recorded before naming the party became required.
 */
function Parties({ names }: { names: string[] }) {
  if (names.length === 0) return <span className="text-content-muted">-</span>;

  const [first, ...rest] = names;
  return (
    <div className="min-w-0">
      <p className="text-content truncate text-[12px]">{first}</p>
      {rest.length > 0 && (
        // Counted, with the names in the tooltip: a cell that grew with the number of
        // parties would set the row height for the whole table.
        <p className="text-content-muted truncate text-[11px]" title={rest.join(', ')}>
          and {rest.length} more
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Journal entries
// ---------------------------------------------------------------------------
function JournalEntries() {
  const [page, setPage] = useState(1);
  const { data, isLoading } = useQuery({
    queryKey: ['journal-entries', page],
    queryFn: () => accountingApi.entries({ page, page_size: 25 }),
  });

  const statusTone: Record<string, BadgeTone> = {
    draft: 'neutral',
    posted: 'success',
    reversed: 'warning',
  };

  const columns: Column<JournalEntry>[] = [
    {
      header: 'Number',
      cell: (row) => (
        <span className="font-mono text-[12px]">
          {row.entry_number ?? <span className="text-content-muted">draft</span>}
        </span>
      ),
    },
    { header: 'Date', cell: (row) => formatDate(row.entry_date) },
    {
      header: 'Narration',
      cell: (row) => (
        <div>
          <p className="text-content">{row.narration}</p>
          <p className="text-content-muted text-[11px]">
            {row.journal_code}
            {row.reference && ` · ${row.reference}`}
          </p>
        </div>
      ),
    },
    {
      header: 'Money',
      hideOnMobile: true,
      cell: (row) =>
        row.cash_direction === null ? (
          // No cash leg, or a transfer between your own accounts that nets to nothing.
          <span className="text-content-muted text-[12px]">no cash movement</span>
        ) : (
          <span
            className={cn(
              'text-[12px] font-medium',
              row.cash_direction === 'in' ? 'text-success' : 'text-danger',
            )}
          >
            {row.cash_direction === 'in' ? 'In' : 'Out'} {formatMoney(row.cash_amount)}
          </span>
        ),
    },
    {
      header: 'Status',
      hideOnMobile: true,
      cell: (row) =>
        row.status === 'reversed' ? (
          <Badge tone="warning" title="Cancelled by an opposite entry. Both remain on the record.">
            Reversed - cancelled
          </Badge>
        ) : row.reverses_id ? (
          <Badge tone="neutral" title="This entry cancels an earlier one.">
            Reversal entry
          </Badge>
        ) : (
          <Badge tone={statusTone[row.status] ?? 'neutral'}>{row.status}</Badge>
        ),
    },
    {
      header: 'Amount',
      numeric: true,
      cell: (row) => (
        <span className={cn(row.status === 'reversed' && 'text-content-muted line-through')}>
          {formatMoney(row.total_debit)}
        </span>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <CashMovementChart entries={data?.items ?? []} />

      <Card>
        <DataTable
          columns={columns}
          rows={data?.items ?? []}
          rowKey={(row) => row.id}
          isLoading={isLoading}
          empty={{
            title: 'No journal entries',
            description: 'Entries appear here as invoices, bills, and payments are posted.',
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
// Trial balance
// ---------------------------------------------------------------------------
/**
 * Did this account have movement that cancelled out?
 *
 * Distinct from "no activity": an account whose ₹100 charge was reversed has a story,
 * an untouched account does not, and showing both as two dashes conflates them.
 */
function netsToNil(row: TrialBalanceRow): boolean {
  return (
    isZeroMoney(row.debit) &&
    isZeroMoney(row.credit) &&
    !(isZeroMoney(row.gross_debit) && isZeroMoney(row.gross_credit))
  );
}

function TrialBalanceReport() {
  const { data, isLoading } = useQuery({
    queryKey: ['trial-balance'],
    queryFn: () => accountingApi.trialBalance(),
  });

  return (
    <div className="space-y-4">
      {data && !data.is_balanced && (
        // Surfaced rather than hidden: an unbalanced ledger is the single most
        // serious condition this system can be in.
        <Card className="border-danger/40 bg-danger-bg">
          <CardBody className="flex items-start gap-3">
            <AlertTriangle className="text-danger mt-0.5 h-4 w-4 shrink-0" />
            <div>
              <p className="text-danger text-[13px] font-semibold">Ledger does not balance</p>
              <p className="text-content-secondary text-[12px]">
                Debits {formatMoney(data.total_debit)} ≠ credits {formatMoney(data.total_credit)}.
                This should be impossible - contact support.
              </p>
            </div>
          </CardBody>
        </Card>
      )}

      <Card>
        <CardHeader
          // The ⓘ rather than a paragraph under the heading: the explanation is long
          // enough to push the figures down the page, and most visits do not need it.
          title={
            <span className="flex items-center gap-1.5">
              Trial balance
              <InfoTip label="About the trial balance">
                <p>Every account that has money in it, and which side that money sits on.</p>
                <p className="mt-2">
                  <strong>Debit</strong> is what you have and what you have spent.{' '}
                  <strong>Credit</strong> is what you owe and what you have earned. They are just
                  the two sides of an entry, not good and bad.
                </p>
                <p className="mt-2">
                  Every entry puts the same amount on both sides, so the two totals at the bottom
                  must match. That is the one thing this table proves - and if they ever did not
                  match, something would be wrong with the books themselves rather than with any
                  single entry.
                </p>
                <p className="mt-2">
                  <strong>A cash or bank account should appear under Debit.</strong> If one shows
                  under Credit, the books say more went out of it than ever went in - which is
                  impossible for real cash, and usually means money that came from a different
                  account was recorded against this one. The totals still balance, because a wrong
                  pair of entries balances just as well as a right one.
                </p>
                <p className="mt-2">
                  <strong>Dealt with</strong> lists the people and businesses behind an account's
                  balance, from the From/To field on the Billing screen. A dash means those entries
                  did not name anyone.
                </p>
              </InfoTip>
            </span>
          }
          description={data ? `As at ${formatDate(data.as_of)}` : undefined}
          action={
            data?.is_balanced ? (
              <Badge tone="success" dot>
                Balanced
              </Badge>
            ) : undefined
          }
        />
        <DataTable
          columns={[
            // {
            //   header: 'Code',
            //   cell: (row) => <span className="font-mono text-[12px]">{row.code}</span>,
            // },
            {
              header: 'Account',
              cell: (row) => (
                <div>
                  <span className="text-content">{row.name}</span>
                  {netsToNil(row) && (
                    <p className="text-warning mt-1 flex items-start gap-1.5 text-[13px]">
                      <Undo2 className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                      <span>
                        <strong className="font-semibold">{formatMoney(row.gross_debit)}</strong>{' '}
                        was posted here and then reversed, so it does not affect the balance.
                      </span>
                    </p>
                  )}
                </div>
              ),
            },
            {
              header: 'Dealt with',
              hideOnMobile: true,
              cell: (row) => <Parties names={row.parties} />,
            },
            {
              header: 'Debit',
              numeric: true,
              cell: (row) =>
                isZeroMoney(row.debit) ? (
                  <span className="text-content-muted">-</span>
                ) : (
                  formatMoney(row.debit)
                ),
            },
            {
              header: 'Credit',
              numeric: true,
              cell: (row) =>
                isZeroMoney(row.credit) ? (
                  <span className="text-content-muted">-</span>
                ) : (
                  formatMoney(row.credit)
                ),
            },
          ]}
          rows={data?.rows ?? []}
          rowKey={(row) => row.account_id}
          isLoading={isLoading}
          empty={{ title: 'Nothing posted yet', description: 'Post an entry to see balances.' }}
          footer={
            data ? (
              <>
                <td className="px-3 py-2.5" colSpan={2}>
                  Total
                </td>
                <td className="px-3 py-2.5 text-right tabular-nums">
                  {formatMoney(data.total_debit)}
                </td>
                <td className="px-3 py-2.5 text-right tabular-nums">
                  {formatMoney(data.total_credit)}
                </td>
              </>
            ) : undefined
          }
        />
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Profit & loss
// ---------------------------------------------------------------------------
function ProfitAndLossReport() {
  // The range is a control now, and the fiscal-year start comes from the server rather
  // than a hardcoded April - which was wrong for any organization on a January year and
  // duplicated a rule the backend already owns.
  const { range, control } = useReportRange();

  const { data, isLoading } = useQuery({
    queryKey: ['pnl', range],
    queryFn: () => accountingApi.profitAndLoss(range),
  });

  if (isLoading || !data) {
    return (
      <div className="space-y-4">
        <Card>
          <CardHeader title="Profit & loss" action={control} />
        </Card>
        <Card>
          <DataTable columns={[]} rows={[]} rowKey={() => ''} isLoading />
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <StatTile label="Revenue" value={data.total_income} tone="success" icon={TrendingUp} />
        <StatTile label="Gross profit" value={data.gross_profit} tone="info" icon={Scale} />
        <StatTile
          label="Net profit"
          value={data.net_profit}
          tone={data.net_profit.startsWith('-') ? 'danger' : 'success'}
          icon={BookOpen}
        />
      </div>

      <Card>
        <CardHeader
          title="Profit & loss"
          description={`${formatDate(data.from_date)} to ${formatDate(data.to_date)}`}
          action={control}
        />
        <CardBody className="space-y-5">
          <ReportSection title="Income" lines={data.income} total={data.total_income} />
          <ReportSection title="Expenses" lines={data.expenses} total={data.total_expenses} />
          <div className="border-border flex items-center justify-between border-t pt-3">
            <span className="text-content text-[14px] font-semibold">Net profit</span>
            <span className="text-content text-[15px] font-semibold tabular-nums">
              {formatMoney(data.net_profit)}
            </span>
          </div>
        </CardBody>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Balance sheet
// ---------------------------------------------------------------------------
/**
 * The balance sheet, for a chosen window, exportable.
 *
 * **A balance sheet is a position at a date, not a total over a period** - so the period
 * picker chooses *which date*, and what it really buys you is the second column: the position
 * the day before the window opened. That pair is what shows movement, and it is how the
 * statement is presented on paper.
 *
 * The period is resolved on the server, not here. An organization's year may start in April,
 * and two clients each working out "this quarter" for themselves is a discrepancy that shows
 * up as different figures with nothing failing.
 */
function BalanceSheetReport() {
  const [period, setPeriod] = useState<StatementPeriod>('this_fiscal_year');
  const [asOf, setAsOf] = useState('');
  const [compareTo, setCompareTo] = useState('');
  const [downloading, setDownloading] = useState<'xlsx' | 'pdf' | null>(null);

  const custom = period === 'custom';
  // Dates are only sent for a custom window. On a named one the server owns both, and passing
  // a half-filled pair would silently override the period the user picked.
  const query = {
    period,
    ...(custom && asOf ? { as_of: asOf } : {}),
    ...(custom && compareTo ? { compare_to: compareTo } : {}),
  };

  const { data, isLoading } = useQuery({
    queryKey: ['balance-sheet-view', period, custom ? asOf : '', custom ? compareTo : ''],
    queryFn: () => accountingApi.balanceSheetView(query),
  });

  const download = async (format: 'xlsx' | 'pdf') => {
    setDownloading(format);
    try {
      // Where it goes is the user's choice: `api.download` opens a save dialog where the
      // browser has one. All that is passed from here is the name to suggest.
      await accountingApi.exportBalanceSheet(format, query, data?.sheet.as_of);
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : `Could not export the ${format} file`,
      );
    } finally {
      setDownloading(null);
    }
  };

  const sheet = data?.sheet;
  const prior = data?.comparative ?? null;

  return (
    <Card>
      <CardHeader
        title="Balance sheet"
        description={
          sheet
            ? prior
              ? `As at ${formatDate(sheet.as_of)}, beside ${formatDate(prior.as_of)}`
              : `As at ${formatDate(sheet.as_of)}`
            : 'Built from every posted entry'
        }
        action={
          sheet ? (
            <Badge tone={sheet.is_balanced ? 'success' : 'danger'} dot>
              {sheet.is_balanced ? 'Balanced' : 'Out of balance'}
            </Badge>
          ) : null
        }
      />
      <CardBody className="space-y-4">
        <div className="border-border flex flex-wrap items-end gap-3 border-b pb-4">
          {/* A segmented group, not a dropdown - the same control the report range above
              uses. Six mutually exclusive windows are worth showing at once: the choice is
              the point of the screen, and a closed `select` hides five of them behind a
              click. `aria-pressed` rather than a radio group, matching that control. */}
          <div className="border-border flex overflow-hidden rounded-lg border">
            {(
              [
                ['to_date', 'As things stand'],
                ['this_quarter', 'This quarter'],
                ['last_quarter', 'Last quarter'],
                ['this_fiscal_year', 'This financial year'],
                ['last_fiscal_year', 'Last financial year'],
                ['custom', 'Custom'],
              ] as [StatementPeriod, string][]
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                aria-pressed={period === key}
                onClick={() => setPeriod(key)}
                className={cn(
                  'px-2.5 py-1.5 text-[12px] font-medium whitespace-nowrap',
                  period === key
                    ? 'bg-primary text-white'
                    : 'text-content-muted hover:bg-surface-sunken',
                )}
              >
                {label}
              </button>
            ))}
          </div>

          {/* Only for a custom window. On a named one these are the server's to decide, and
              showing them empty would invite someone to fill one in and override it. */}
          {custom && (
            <>
              <Input
                label="As at"
                type="date"
                value={asOf}
                onChange={(event) => setAsOf(event.target.value)}
                className="w-40"
              />
              <Input
                label="Compare with"
                type="date"
                value={compareTo}
                onChange={(event) => setCompareTo(event.target.value)}
                className="w-40"
                hint="Optional second column."
              />
            </>
          )}

          <div className="ml-auto flex gap-2">
            <Button
              variant="secondary"
              onClick={() => void download('xlsx')}
              disabled={!sheet || downloading !== null}
            >
              <FileSpreadsheet className="h-4 w-4" aria-hidden />
              {downloading === 'xlsx' ? 'Preparing...' : 'Excel'}
            </Button>
            <Button
              variant="secondary"
              onClick={() => void download('pdf')}
              disabled={!sheet || downloading !== null}
            >
              <FileDown className="h-4 w-4" aria-hidden />
              {downloading === 'pdf' ? 'Preparing...' : 'PDF'}
            </Button>
          </div>
        </div>

        {isLoading || !sheet ? (
          <DataTable columns={[]} rows={[]} rowKey={() => ''} isLoading />
        ) : (
          <div className="space-y-5">
            {prior && (
              <div className="text-content-muted flex justify-end gap-6 text-[11px] font-semibold tracking-wider uppercase">
                <span className="w-32 text-right">{formatDate(sheet.as_of)}</span>
                <span className="w-32 text-right">{formatDate(prior.as_of)}</span>
              </div>
            )}

            <ReportSection
              title="Assets"
              lines={sheet.assets}
              total={sheet.total_assets}
              prior={prior?.assets}
              priorTotal={prior?.total_assets}
            />
            <ReportSection
              title="Liabilities"
              lines={sheet.liabilities}
              total={sheet.total_liabilities}
              prior={prior?.liabilities}
              priorTotal={prior?.total_liabilities}
            />
            <ReportSection
              title="Equity"
              lines={sheet.equity}
              total={sheet.total_equity}
              prior={prior?.equity}
              priorTotal={prior?.total_equity}
            />

            <div className="border-border flex items-center justify-between border-t pt-3">
              <span className="text-content text-[14px] font-semibold">Liabilities + equity</span>
              <div className="flex gap-6">
                <span className="text-content w-32 text-right text-[15px] font-semibold tabular-nums">
                  {/* Displayed for the reader to check against total assets. The
                      authoritative check is `is_balanced`, computed server-side. */}
                  {formatMoney(sheet.total_assets)}
                </span>
                {prior && (
                  <span className="text-content-muted w-32 text-right text-[15px] font-semibold tabular-nums">
                    {formatMoney(prior.total_assets)}
                  </span>
                )}
              </div>
            </div>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Shared report pieces
// ---------------------------------------------------------------------------
function ReportSection({
  title,
  lines,
  total,
  prior,
  priorTotal,
}: {
  title: string;
  lines: ReportLine[];
  total: string;
  /** The same section at the comparison date, when one was asked for. */
  prior?: ReportLine[];
  priorTotal?: string;
}) {
  // Matched by **label, never by position**: the two dates can hold different accounts - one
  // opened mid-period - so zipping the lists by index would pair unrelated rows and print a
  // confident wrong number. A row with no counterpart simply leaves its cell blank.
  const before = new Map((prior ?? []).map((line) => [line.label, line.amount]));
  const comparing = priorTotal !== undefined;

  return (
    <div>
      <p className="text-content-muted mb-1.5 text-[11px] font-semibold tracking-wider uppercase">
        {title}
      </p>
      {lines.length === 0 ? (
        <p className="text-content-muted py-1 text-[13px]">Nothing to report</p>
      ) : (
        <div className="space-y-0.5">
          {lines.map((line) => (
            <div
              key={`${line.account_code ?? ''}-${line.label}`}
              className="flex items-center justify-between py-1 text-[13px]"
              style={{ paddingLeft: `${(line.level - 1) * 12}px` }}
            >
              <span className="text-content-secondary">
                {line.account_code && (
                  <span className="text-content-muted mr-2 font-mono text-[11px]">
                    {line.account_code}
                  </span>
                )}
                {line.label}
              </span>
              <span className="flex gap-6">
                <span className="text-content w-32 text-right tabular-nums">
                  {formatMoney(line.amount)}
                </span>
                {comparing && (
                  <span className="text-content-muted w-32 text-right tabular-nums">
                    {/* A dash, not a zero, when the account has no counterpart: it did not
                        exist at the earlier date, and "0.00" would assert a balance that was
                        never recorded. */}
                    {before.get(line.label) === undefined
                      ? '-'
                      : formatMoney(before.get(line.label))}
                  </span>
                )}
              </span>
            </div>
          ))}
        </div>
      )}
      <div className="border-border/60 mt-1.5 flex items-center justify-between border-t pt-1.5 text-[13px] font-medium">
        <span className="text-content">Total {title.toLowerCase()}</span>
        <span className="flex gap-6">
          <span className="text-content w-32 text-right tabular-nums">{formatMoney(total)}</span>
          {comparing && (
            <span className="text-content-muted w-32 text-right tabular-nums">
              {formatMoney(priorTotal)}
            </span>
          )}
        </span>
      </div>
    </div>
  );
}

function StatTile({
  label,
  value,
  tone,
  icon: Icon,
}: {
  label: string;
  value: string;
  tone: BadgeTone;
  icon: LucideIcon;
}) {
  const toneClass: Record<string, string> = {
    success: 'text-success',
    danger: 'text-danger',
    info: 'text-info',
    primary: 'text-primary',
    warning: 'text-warning',
    neutral: 'text-content',
  };
  return (
    <Card>
      <CardBody>
        <div className="flex items-center gap-2">
          <Icon className={cn('h-3.5 w-3.5', toneClass[tone])} aria-hidden />
          <p className="text-content-muted text-[11px] font-semibold tracking-wider uppercase">
            {label}
          </p>
        </div>
        <p className="text-content mt-1.5 text-[20px] font-semibold tabular-nums">
          {formatMoney(value)}
        </p>
      </CardBody>
    </Card>
  );
}
