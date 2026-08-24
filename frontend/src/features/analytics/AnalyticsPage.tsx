/**
 * Analytics - the same figures as the statements, arranged for scanning.
 *
 * Everything here is derived server-side from the ledger by the service that renders
 * the P&L, so nothing on this page can disagree with the accounts. Three deliberate
 * presentation choices:
 *
 * - **The period selector changes one thing: the window.** All five panels move
 *   together, because comparing a month-to-date revenue figure against a
 *   year-to-date customer ranking is how people reach wrong conclusions from
 *   correct numbers.
 * - **Concentration is stated, not left to be computed.** "These five customers are
 *   62% of revenue" is the useful reading of a top-five list; five names and five
 *   amounts on their own are not.
 * - **Reconciliation is shown when it passes, too.** A control that only appears when
 *   it fails offers no reassurance when it holds.
 */
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, ShieldCheck, TrendingUp } from 'lucide-react';
import { useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { Badge } from '@/components/ui/Badge';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import type { Column } from '@/components/ui/DataTable';
import { DataTable, PageHeader } from '@/components/ui/DataTable';
import { EmptyState } from '@/components/ui/EmptyState';
import { Skeleton } from '@/components/ui/Skeleton';
import {
  type ControlCheck,
  type Movement,
  type Period,
  type RankedRow,
  type Ranking,
  analyticsApi,
} from '@/features/analytics/api';
import { cn } from '@/lib/cn';
import { formatCompact, formatDate, formatMoney, isZeroMoney } from '@/lib/format';
import { localeSettings } from '@/lib/locale';

export function AnalyticsPage() {
  const [period, setPeriod] = useState<Period>('this_fiscal_year');

  const { data: options } = useQuery({
    queryKey: ['analytics-periods'],
    // Fiscal settings do not change while the tab is open.
    staleTime: Number.POSITIVE_INFINITY,
    queryFn: () => analyticsApi.periods(),
  });

  const { data: dashboard } = useQuery({
    queryKey: ['analytics-dashboard', period],
    queryFn: () => analyticsApi.dashboard(period),
  });

  const { data: trend } = useQuery({
    queryKey: ['analytics-trend', period],
    queryFn: () => analyticsApi.trend(period),
  });

  const { data: customers } = useQuery({
    queryKey: ['analytics-top-customers', period],
    queryFn: () => analyticsApi.topCustomers(period, 5),
  });

  const { data: products } = useQuery({
    queryKey: ['analytics-top-products', period],
    queryFn: () => analyticsApi.topProducts(period, 5),
  });

  const { data: checks } = useQuery({
    queryKey: ['analytics-control-checks'],
    queryFn: () => analyticsApi.controlChecks(),
  });

  // The organization's currency, not a literal: the response carries one, but the
  // fallback used to be 'INR' whatever the books were kept in.
  const currency = dashboard?.currency ?? localeSettings().currency;

  return (
    <div>
      <PageHeader
        title="Analytics"
        description={
          dashboard
            ? `${dashboard.period_label}: ${formatDate(dashboard.span.start)} to ${formatDate(dashboard.span.end)}, compared against ${formatDate(dashboard.comparison.start)} to ${formatDate(dashboard.comparison.end)}.`
            : 'Figures derived from the ledger - the same source as the financial statements.'
        }
        action={
          <select
            value={period}
            onChange={(event) => setPeriod(event.target.value as Period)}
            aria-label="Reporting period"
            className="border-border bg-surface text-content rounded-lg border px-2.5 py-1.5 text-[13px]"
          >
            {(options?.options ?? []).map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        }
      />

      <div className="space-y-4">
        {/* ---- Headline ---- */}
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <FigureTile label="Revenue" movement={dashboard?.revenue} currency={currency} />
          <FigureTile
            label="Gross profit"
            movement={dashboard?.gross_profit}
            currency={currency}
            hint="Revenue less cost of goods sold"
          />
          <FigureTile
            label="Expenses"
            movement={dashboard?.expenses}
            currency={currency}
            risingIsGood={false}
          />
          <FigureTile label="Net profit" movement={dashboard?.net_profit} currency={currency} />
        </div>

        {/* ---- Trend ---- */}
        <Card>
          <CardHeader
            title="Income and expenses by month"
            description="Bars sum exactly to the totals above - the series is derived from the same posted entries."
            action={
              trend ? (
                <span className="text-content-muted text-[12px] tabular-nums">
                  {formatMoney(trend.total_income, currency)} in ·{' '}
                  {formatMoney(trend.total_expenses, currency)} out
                </span>
              ) : undefined
            }
          />
          <CardBody>
            {trend === undefined ? (
              <Skeleton className="h-[300px] w-full" />
            ) : (
              <MonthlyBars trend={trend} currency={currency} />
            )}
          </CardBody>
        </Card>

        {/* ---- Rankings ---- */}
        <div className="grid gap-4 lg:grid-cols-2">
          <RankingCard
            title="Top customers"
            description="Ranked on taxable value - GST collected is the government's money, not revenue."
            ranking={customers}
            currency={currency}
            countLabel="invoices"
          />
          <RankingCard
            title="Best-selling lines"
            description="Grouped by line description, so services and one-off charges are counted too."
            ranking={products}
            currency={currency}
            countLabel="lines"
          />
        </div>

        {/* ---- Reconciliation ---- */}
        <ReconciliationCard checks={checks} currency={currency} />
      </div>
    </div>
  );
}

function FigureTile({
  label,
  movement,
  currency,
  risingIsGood = true,
  hint,
}: {
  label: string;
  movement: Movement | undefined;
  currency: string;
  risingIsGood?: boolean;
  hint?: string;
}) {
  const change =
    movement?.change_percent === null || movement?.change_percent === undefined
      ? null
      : Number(movement.change_percent);
  const good = (change ?? 0) >= 0 === risingIsGood;

  return (
    <Card className="p-4">
      <p className="text-content-muted text-[12px] font-medium">{label}</p>

      {movement === undefined ? (
        <Skeleton className="mt-2 h-7 w-28" />
      ) : (
        <p className="text-content mt-2 text-[22px] leading-none font-semibold tracking-[-0.02em] tabular-nums">
          {formatMoney(movement.current, currency)}
        </p>
      )}

      <div className="mt-2 min-h-[16px] text-[12px]">
        {change !== null ? (
          <span className={cn('font-medium tabular-nums', good ? 'text-success' : 'text-danger')}>
            {change >= 0 ? '+' : ''}
            {change.toFixed(1)}% vs previous
          </span>
        ) : movement && !isZeroMoney(movement.current) ? (
          // Not "+100%": there is no base to compare against.
          <span className="text-content-muted">no prior data to compare</span>
        ) : (
          <span className="text-content-muted">{hint ?? ''}</span>
        )}
      </div>
      {hint && change !== null && <p className="text-content-muted mt-1 text-[11px]">{hint}</p>}
    </Card>
  );
}

/**
 * Grouped bars per month.
 *
 * As on the dashboard, `Number()` is used only for bar geometry; the tooltip reads
 * the exact decimal string off the datum, so the figure anyone reads is exact even
 * though the pixel height is not.
 */
function MonthlyBars({
  trend,
  currency,
}: {
  trend: NonNullable<Awaited<ReturnType<typeof analyticsApi.trend>>>;
  currency: string;
}) {
  const data = trend.points.map((point) => ({
    label: point.label,
    income: Number(point.income),
    expenses: Number(point.expenses),
    incomeText: point.income,
    expensesText: point.expenses,
  }));

  if (data.every((point) => point.income === 0 && point.expenses === 0)) {
    return (
      <EmptyState
        icon={TrendingUp}
        title="Nothing posted in this period"
        description="Post an invoice or a bill and it appears here."
        className="py-16"
      />
    );
  }

  return (
    <div className="h-[300px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 4, right: 4, left: -18, bottom: 0 }}>
          <CartesianGrid stroke="var(--border)" vertical={false} />
          <XAxis
            dataKey="label"
            stroke="var(--content-muted)"
            fontSize={11}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            stroke="var(--content-muted)"
            fontSize={11}
            tickLine={false}
            axisLine={false}
            tickFormatter={(value: number) => formatCompact(value)}
          />
          <Tooltip
            cursor={{ fill: 'var(--surface-sunken)' }}
            contentStyle={{
              background: 'var(--surface-raised)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-lg)',
              fontSize: 12,
              boxShadow: 'var(--shadow-lg)',
            }}
            labelStyle={{ color: 'var(--content)', fontWeight: 600 }}
            formatter={(_value, name, item) => {
              const datum = item?.payload as (typeof data)[number] | undefined;
              const exact = name === 'income' ? datum?.incomeText : datum?.expensesText;
              return [
                formatMoney(exact ?? '0', currency),
                name === 'income' ? 'Income' : 'Expenses',
              ];
            }}
          />
          <Legend
            wrapperStyle={{ fontSize: 12 }}
            formatter={(value) => (value === 'income' ? 'Income' : 'Expenses')}
          />
          <Bar dataKey="income" fill="var(--primary)" radius={[3, 3, 0, 0]} maxBarSize={28} />
          <Bar dataKey="expenses" fill="var(--warning)" radius={[3, 3, 0, 0]} maxBarSize={28} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function RankingCard({
  title,
  description,
  ranking,
  currency,
  countLabel,
}: {
  title: string;
  description: string;
  ranking: Ranking | undefined;
  currency: string;
  countLabel: string;
}) {
  // Concentration: what share of the whole period the listed rows account for. The
  // reason a top-five list is worth showing at all.
  const shown = ranking?.rows.reduce((total, row) => total + Number(row.amount), 0) ?? 0;
  const whole = Number(ranking?.total ?? 0);
  const share = whole > 0 ? Math.round((shown / whole) * 100) : null;

  const columns: Column<RankedRow>[] = [
    {
      header: '',
      cell: (row) => <span className="truncate">{row.label}</span>,
    },
    {
      header: countLabel,
      numeric: true,
      hideOnMobile: true,
      cell: (row) => row.count,
    },
    {
      header: 'Value',
      numeric: true,
      cell: (row) => formatMoney(row.amount, currency),
    },
  ];

  return (
    <Card>
      <CardHeader
        title={title}
        description={description}
        action={
          share !== null ? (
            <Badge tone="neutral" title="Share of the period's total taxable value">
              {share}% of total
            </Badge>
          ) : undefined
        }
      />
      <DataTable
        columns={columns}
        rows={ranking?.rows ?? []}
        rowKey={(row) => row.id ?? row.label}
        isLoading={ranking === undefined}
        empty={{
          title: 'Nothing in this period',
          description: 'Post an invoice and it appears here.',
        }}
      />
    </Card>
  );
}

/**
 * Control-account reconciliation.
 *
 * Each figure is derived twice - once from the control account, once from the
 * documents that should have produced it. This is the check a bookkeeper does monthly
 * by hand; showing it means a document that updated one table and not the other is
 * caught in days rather than found by an accountant a year later.
 */
function ReconciliationCard({
  checks,
  currency,
}: {
  checks: { as_of: string; checks: ControlCheck[]; all_agree: boolean } | undefined;
  currency: string;
}) {
  const columns: Column<ControlCheck>[] = [
    { header: 'Control account', cell: (row) => row.name },
    {
      header: 'Ledger',
      numeric: true,
      cell: (row) => formatMoney(row.ledger, currency),
    },
    {
      header: 'Documents',
      numeric: true,
      cell: (row) => formatMoney(row.subledger, currency),
    },
    {
      header: 'Difference',
      numeric: true,
      cell: (row) => (
        <span className={cn(row.agrees ? 'text-content-muted' : 'text-danger font-semibold')}>
          {formatMoney(row.difference, currency)}
        </span>
      ),
    },
    {
      header: '',
      cell: (row) =>
        row.agrees ? <Badge tone="success">Agrees</Badge> : <Badge tone="danger">Check this</Badge>,
    },
  ];

  return (
    <Card>
      <CardHeader
        title="Reconciliation"
        description={
          checks
            ? `Ledger against source documents as at ${formatDate(checks.as_of)}. These must agree.`
            : 'Ledger against source documents.'
        }
        action={
          checks ? (
            checks.all_agree ? (
              <span className="text-success flex items-center gap-1.5 text-[12px] font-medium">
                <ShieldCheck className="h-3.5 w-3.5" aria-hidden />
                All reconcile
              </span>
            ) : (
              <span className="text-danger flex items-center gap-1.5 text-[12px] font-medium">
                <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
                Discrepancy found
              </span>
            )
          ) : undefined
        }
      />
      <DataTable
        columns={columns}
        rows={checks?.checks ?? []}
        rowKey={(row) => row.name}
        isLoading={checks === undefined}
      />
      {checks && !checks.all_agree && (
        <CardBody className="pt-3">
          <p className="text-content-secondary text-[12px]">
            A difference means something was recorded in one place and not the other - most often a
            document edited outside the normal flow. Every figure on this page is derived from the
            ledger, so resolve this before relying on them.
          </p>
        </CardBody>
      )}
    </Card>
  );
}
