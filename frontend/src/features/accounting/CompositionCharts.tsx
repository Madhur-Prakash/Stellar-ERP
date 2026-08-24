/**
 * Composition and trend charts.
 *
 * Separate from `AccountingCharts` by the question they answer: those show *balances*
 * (where money sits), these show *proportion and direction* (what it went on, and which
 * way things are moving).
 *
 * **A donut is safe here and deliberately not used for balances.** Expense totals are
 * non-negative by construction, so every value has a real slice. A balance can be
 * negative — cash is, right now — and a negative has no slice at all: a pie would have to
 * drop it, hide it, or plot its absolute value, and all three misrepresent the books.
 */
import { ChartPie, LineChart as LineChartIcon } from 'lucide-react';
import {
  Area,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { InfoTip } from '@/components/ui/InfoTip';
import type { Account } from '@/features/accounting/api';
import type { TrendPoint } from '@/features/analytics/api';
import { formatCompact, formatMoney } from '@/lib/format';

const MOTION = {
  isAnimationActive: true,
  animationDuration: 600,
  animationEasing: 'ease-out' as const,
};

const TOOLTIP_STYLE = {
  background: 'var(--surface-raised)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-lg)',
  fontSize: 12,
  boxShadow: 'var(--shadow-lg)',
};

/**
 * Fixed and ordered rather than generated, so a category keeps its colour between
 * renders. A donut whose colours shuffle on every refetch cannot be read.
 */
const SLICE_COLOURS = [
  'var(--primary)',
  'var(--info)',
  'var(--success)',
  'var(--warning)',
  'var(--danger)',
  '#8b5cf6',
  '#0ea5e9',
  '#14b8a6',
  '#f59e0b',
  '#ec4899',
];

interface SliceDatum {
  name: string;
  value: number;
  exact: string;
  share: number;
}

/** Small categories are folded together — twelve two-percent slivers is a colour key. */
const MAX_SLICES = 7;

export function SpendingMixChart({ accounts }: { accounts: Account[] }) {
  const expenses = accounts
    .filter(
      (account) =>
        !account.is_group && account.account_type === 'expense' && Number(account.balance) > 0,
    )
    .map((account) => ({
      name: account.name,
      value: Number(account.balance),
      exact: account.balance,
    }))
    .sort((a, b) => b.value - a.value);

  const total = expenses.reduce((sum, item) => sum + item.value, 0);
  const share = (value: number) => (total > 0 ? (value / total) * 100 : 0);

  const data: SliceDatum[] = expenses
    .slice(0, MAX_SLICES)
    .map((item) => ({ ...item, share: share(item.value) }));

  const rest = expenses.slice(MAX_SLICES);
  if (rest.length > 0) {
    const amount = rest.reduce((sum, item) => sum + item.value, 0);
    data.push({
      name: `Other (${rest.length})`,
      value: amount,
      exact: String(amount),
      share: share(amount),
    });
  }

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <ChartPie className="text-content-muted h-4 w-4" aria-hidden />
            What you spent it on
          </span>
        }
        description="Share of total spending by category."
        action={
          <InfoTip label="spending mix" align="right">
            <p>
              Every expense category with a balance, as a percentage of total spending. Small ones
              are grouped into <strong>Other</strong> to keep it readable.
            </p>
            <p>Hover a slice for the amount as well as the share.</p>
          </InfoTip>
        }
      />
      <CardBody>
        {data.length === 0 ? (
          <EmptyState
            icon={ChartPie}
            title="Nothing spent yet"
            description="Record money out and the breakdown appears here."
            className="py-12"
          />
        ) : (
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={data}
                  dataKey="value"
                  nameKey="name"
                  // A donut, not a full pie: the hole gives the arcs room and they are
                  // easier to compare than wedges converging on a point.
                  innerRadius="52%"
                  outerRadius="78%"
                  paddingAngle={2}
                  {...MOTION}
                >
                  {data.map((datum, index) => (
                    <Cell key={datum.name} fill={SLICE_COLOURS[index % SLICE_COLOURS.length]} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  formatter={(_value, _name, item) => {
                    const datum = item?.payload as SliceDatum | undefined;
                    return [
                      `${formatMoney(datum?.exact ?? '0')} · ${(datum?.share ?? 0).toFixed(1)}%`,
                      datum?.name ?? '',
                    ];
                  }}
                />
                <Legend
                  verticalAlign="bottom"
                  height={56}
                  wrapperStyle={{ fontSize: 11 }}
                  formatter={(value) => <span className="text-content-secondary">{value}</span>}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

interface TrendDatum {
  label: string;
  income: number;
  expenses: number;
  profit: number;
  incomeText: string;
  expensesText: string;
  profitText: string;
}

const SERIES_LABELS: Record<string, string> = {
  income: 'Income',
  expenses: 'Spending',
  profit: 'Profit',
};

/**
 * Income, spending, and profit month by month.
 *
 * Lines for income and spending because this is a series over time and direction is the
 * primary reading. Profit is filled instead, so a loss shows as area below the axis — a
 * shaded region reads faster than a line dipping under a gridline.
 */
export function TrendChart({ points }: { points: TrendPoint[] | undefined }) {
  const data: TrendDatum[] = (points ?? []).map((point) => ({
    label: point.label,
    income: Number(point.income),
    expenses: Number(point.expenses),
    profit: Number(point.profit),
    incomeText: point.income,
    expensesText: point.expenses,
    profitText: point.profit,
  }));

  const hasActivity = data.some((point) => point.income !== 0 || point.expenses !== 0);

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <LineChartIcon className="text-content-muted h-4 w-4" aria-hidden />
            Trend over time
          </span>
        }
        description="Income, spending, and what was left, month by month."
        action={
          <InfoTip label="the trend" align="right">
            <p>
              Twelve months of posted entries. Months with nothing recorded show as zero rather than
              being skipped, so the line does not imply trading that did not happen.
            </p>
          </InfoTip>
        }
      />
      <CardBody>
        {!hasActivity ? (
          <EmptyState
            icon={LineChartIcon}
            title="Not enough history yet"
            description="Once entries span a few months, the trend appears here."
            className="py-12"
          />
        ) : (
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={data} margin={{ top: 8, right: 8, left: -12, bottom: 4 }}>
                <defs>
                  <linearGradient id="profitFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--primary)" stopOpacity={0.25} />
                    <stop offset="100%" stopColor="var(--primary)" stopOpacity={0} />
                  </linearGradient>
                </defs>
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
                  contentStyle={TOOLTIP_STYLE}
                  formatter={(_value, name, item) => {
                    // The exact decimal off the datum, never the float that was plotted.
                    const datum = item?.payload as TrendDatum | undefined;
                    const exact =
                      name === 'income'
                        ? datum?.incomeText
                        : name === 'expenses'
                          ? datum?.expensesText
                          : datum?.profitText;
                    return [formatMoney(exact ?? '0'), SERIES_LABELS[String(name)] ?? String(name)];
                  }}
                />
                <Legend
                  wrapperStyle={{ fontSize: 11 }}
                  formatter={(value) => (
                    <span className="text-content-secondary">
                      {SERIES_LABELS[String(value)] ?? String(value)}
                    </span>
                  )}
                />
                <Area
                  type="monotone"
                  dataKey="profit"
                  stroke="var(--primary)"
                  strokeWidth={2}
                  fill="url(#profitFill)"
                  {...MOTION}
                />
                <Line
                  type="monotone"
                  dataKey="income"
                  stroke="var(--success)"
                  strokeWidth={2}
                  dot={{ r: 3 }}
                  activeDot={{ r: 5 }}
                  {...MOTION}
                />
                <Line
                  type="monotone"
                  dataKey="expenses"
                  stroke="var(--danger)"
                  strokeWidth={2}
                  dot={{ r: 3 }}
                  activeDot={{ r: 5 }}
                  {...MOTION}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardBody>
    </Card>
  );
}
