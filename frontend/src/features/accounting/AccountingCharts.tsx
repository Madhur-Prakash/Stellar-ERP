/**
 * Charts for the accounting screens.
 *
 * **Bars, not pie charts.** A cash account can hold a negative balance - it does right
 * now, which is itself a signal worth seeing - and a negative value has no possible pie
 * slice. A pie would have to drop it, hide it, or plot its absolute value, and all three
 * are lies about the books. Horizontal bars handle a negative naturally and are easier
 * to read against a label besides.
 *
 * **Only accounts with money.** The chart of accounts is 114 rows and four of them have
 * a balance; plotting the rest is 110 bars of nothing that bury the four that matter.
 *
 * **`Number()` appears here and nowhere else.** Recharts plots pixels, and a pixel
 * position does not need exact decimal arithmetic - but a figure someone reads does, so
 * every datum carries its original decimal string and the tooltips format from that. The
 * geometry is approximate; every number on screen is exact.
 */
import { ArrowDownLeft, ArrowUpRight, Landmark, TrendingUp, Wallet } from 'lucide-react';
import {
  Bar,
  BarChart,
  Cell,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { InfoTip } from '@/components/ui/InfoTip';
import type { Account, AccountType, JournalEntry } from '@/features/accounting/api';
import { formatCompact, formatDate, formatMoney, isZeroMoney } from '@/lib/format';

/** Animation shared by every chart here, so they feel like one system. */
const MOTION = {
  isAnimationActive: true,
  animationDuration: 600,
  animationEasing: 'ease-out' as const,
};

const TYPE_COLOUR: Record<AccountType, string> = {
  asset: 'var(--info)',
  liability: 'var(--warning)',
  equity: 'var(--primary)',
  income: 'var(--success)',
  expense: 'var(--danger)',
};

const TOOLTIP_STYLE = {
  background: 'var(--surface-raised)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-lg)',
  fontSize: 12,
  boxShadow: 'var(--shadow-lg)',
};

interface BalanceDatum {
  name: string;
  code: string;
  type: AccountType;
  value: number;
  exact: string;
}

/**
 * Every account holding money, longest bar first.
 *
 * Sorted by size rather than by code: the question this answers is "where is my money",
 * and code order scatters the answer across the chart.
 */
/**
 * Balance-sheet types only - the accounts that represent something you hold or owe.
 *
 * Income and expense accounts are deliberately excluded even though they carry balances.
 * They are *running totals* of what has been earned and spent, not places money sits, and
 * plotting them beside cash makes the axis mean two different things at once: further
 * right is more money for an asset, but more cost for an expense. That conflation is
 * genuinely misleading, and it is what the donut and the trend chart are for instead.
 */
const HOLDING_TYPES = new Set(['asset', 'liability', 'equity']);

export function AccountBalancesChart({ accounts }: { accounts: Account[] }) {
  const data: BalanceDatum[] = accounts
    .filter(
      (account) =>
        !account.is_group &&
        HOLDING_TYPES.has(account.account_type) &&
        !isZeroMoney(account.balance),
    )
    .map((account) => ({
      name: account.name,
      code: account.code,
      type: account.account_type,
      value: Number(account.balance),
      exact: account.balance,
    }))
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value));

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <Wallet className="text-content-muted h-4 w-4" aria-hidden />
            Where your money is
          </span>
        }
        description="What you hold and what you owe. Hover a bar for the exact figure."
        action={
          <InfoTip label="account balances" align="right">
            <p>
              What you actually have and owe, biggest first - cash, bank, stock, money owed to you,
              money you owe. Blue is an asset, amber a liability.
            </p>
            <p>
              Income and spending are <strong>not</strong> here on purpose. They are running totals
              rather than places money sits, and putting them on this axis would make &ldquo;further
              right&rdquo; mean more money for cash and more cost for an expense. Those are on the
              donut and the trend chart.
            </p>
            <p>
              <strong>A negative asset is worth a second look.</strong> Cash cannot really go below
              zero, so a negative cash bar means a payment was recorded against the wrong account.
            </p>
          </InfoTip>
        }
      />
      <CardBody>
        {data.length === 0 ? (
          <EmptyState
            icon={Wallet}
            title="Nothing held yet"
            description="Record money in or out and the accounts holding it appear here."
            className="py-12"
          />
        ) : (
          <div style={{ height: Math.max(180, data.length * 42) }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={data}
                layout="vertical"
                margin={{ top: 4, right: 16, left: 8, bottom: 4 }}
              >
                <CartesianGrid stroke="var(--border)" horizontal={false} />
                <XAxis
                  type="number"
                  stroke="var(--content-muted)"
                  fontSize={11}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(value: number) => formatCompact(value)}
                />
                <YAxis
                  type="category"
                  dataKey="name"
                  stroke="var(--content-muted)"
                  fontSize={11}
                  tickLine={false}
                  axisLine={false}
                  width={168}
                />
                <Tooltip
                  cursor={{ fill: 'var(--surface-sunken)' }}
                  contentStyle={TOOLTIP_STYLE}
                  formatter={(_value, _name, item) => {
                    // The account code is dropped: it is internal numbering, and nobody
                    // reading "where is my money" needs to know Sales Revenue is 4100.
                    // The type is kept because it is what the bar's colour means.
                    const datum = item?.payload as BalanceDatum | undefined;
                    const type = datum ? datum.type[0]!.toUpperCase() + datum.type.slice(1) : '';
                    return [formatMoney(datum?.exact ?? '0'), type];
                  }}
                />
                <Bar dataKey="value" radius={[0, 4, 4, 0]} maxBarSize={26} {...MOTION}>
                  {data.map((datum) => (
                    <Cell
                      key={datum.code}
                      // A negative balance is always shown in the danger colour, whatever
                      // the account type: it is the exception worth noticing.
                      fill={datum.value < 0 ? 'var(--danger)' : TYPE_COLOUR[datum.type]}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

/**
 * Net balance per account type - the accounting equation, drawn.
 *
 * Assets on one side, liabilities and equity on the other, with income and expenses
 * feeding the difference. Seeing them side by side is the fastest way to sanity-check a
 * set of books.
 */
export function BalanceByTypeChart({ accounts }: { accounts: Account[] }) {
  const totals = new Map<AccountType, number>();
  for (const account of accounts) {
    if (account.is_group) continue;
    const current = totals.get(account.account_type) ?? 0;
    totals.set(account.account_type, current + Number(account.balance));
  }

  const order: AccountType[] = ['asset', 'liability', 'equity', 'income', 'expense'];
  const data = order
    .map((type) => ({
      type,
      label: type[0]!.toUpperCase() + type.slice(1),
      value: totals.get(type) ?? 0,
    }))
    .filter((datum) => datum.value !== 0);

  if (data.length === 0) return null;

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <TrendingUp className="text-content-muted h-4 w-4" aria-hidden />
            Totals by type
          </span>
        }
        description="Assets, liabilities, equity, income, and expenses side by side."
      />
      <CardBody>
        <div className="h-56">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 12, right: 8, left: -12, bottom: 4 }}>
              <CartesianGrid stroke="var(--border)" vertical={false} />
              <XAxis
                dataKey="label"
                stroke="var(--content-muted)"
                fontSize={11}
                tickLine={false}
                axisLine={false}
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
                contentStyle={TOOLTIP_STYLE}
                formatter={(value) => [formatMoney(String(value)), 'Net balance']}
              />
              <Bar dataKey="value" radius={[4, 4, 0, 0]} maxBarSize={56} {...MOTION}>
                {data.map((datum) => (
                  <Cell key={datum.type} fill={TYPE_COLOUR[datum.type]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </CardBody>
    </Card>
  );
}

interface MovementDatum {
  date: string;
  label: string;
  inAmount: number;
  outAmount: number;
  entries: JournalEntry[];
}

/**
 * Every cash movement, by day, with the entries behind each bar.
 *
 * The point is the tooltip: hovering a day lists the individual entries that made it up,
 * with their numbers and narrations. A total with no way to see what it is composed of
 * invites the question it cannot answer.
 */
export function CashMovementChart({ entries }: { entries: JournalEntry[] }) {
  const byDay = new Map<string, MovementDatum>();

  for (const entry of entries) {
    if (entry.cash_direction === null) continue;

    const bucket = byDay.get(entry.entry_date) ?? {
      date: entry.entry_date,
      label: formatDate(entry.entry_date),
      inAmount: 0,
      outAmount: 0,
      entries: [],
    };
    const amount = Number(entry.cash_amount);
    if (entry.cash_direction === 'in') bucket.inAmount += amount;
    else bucket.outAmount += amount;
    bucket.entries.push(entry);
    byDay.set(entry.entry_date, bucket);
  }

  const data = [...byDay.values()].sort((a, b) => a.date.localeCompare(b.date));

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <Landmark className="text-content-muted h-4 w-4" aria-hidden />
            Cash movement
          </span>
        }
        description="Money in and out by day. Hover a bar to see the entries behind it."
        action={
          <InfoTip label="cash movement" align="right">
            <p>
              Only entries that moved cash or bank. An invoice posting moves receivables and revenue
              rather than money, so it does not appear here.
            </p>
            <p>
              A reversed entry and its reversal both show, one in each direction - which is exactly
              what cancelling out looks like.
            </p>
          </InfoTip>
        }
      />
      <CardBody>
        {data.length === 0 ? (
          <EmptyState
            icon={Landmark}
            title="No cash movement yet"
            description="Entries that move money appear here once recorded."
            className="py-12"
          />
        ) : (
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data} margin={{ top: 12, right: 8, left: -12, bottom: 4 }}>
                <CartesianGrid stroke="var(--border)" vertical={false} />
                <XAxis
                  dataKey="label"
                  stroke="var(--content-muted)"
                  fontSize={11}
                  tickLine={false}
                  axisLine={false}
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
                  contentStyle={TOOLTIP_STYLE}
                  content={({ active, payload }) => {
                    if (!active || !payload?.length) return null;
                    const datum = payload[0]?.payload as MovementDatum | undefined;
                    if (!datum) return null;

                    return (
                      <div className="bg-surface-raised border-border max-w-xs rounded-lg border p-3 shadow-lg">
                        <p className="text-content mb-2 text-[12px] font-semibold">{datum.label}</p>
                        <ul className="space-y-1.5">
                          {datum.entries.map((entry) => (
                            <li key={entry.id} className="flex items-start gap-2 text-[11px]">
                              {entry.cash_direction === 'in' ? (
                                <ArrowDownLeft
                                  className="text-success mt-0.5 h-3 w-3 shrink-0"
                                  aria-hidden
                                />
                              ) : (
                                <ArrowUpRight
                                  className="text-danger mt-0.5 h-3 w-3 shrink-0"
                                  aria-hidden
                                />
                              )}
                              <span className="min-w-0 flex-1">
                                <span className="text-content block truncate">
                                  {entry.narration}
                                </span>
                                <span className="text-content-muted">
                                  {entry.entry_number}
                                  {entry.status === 'reversed' && ' · reversed'}
                                </span>
                              </span>
                              <span
                                className={
                                  entry.cash_direction === 'in'
                                    ? 'text-success font-medium tabular-nums'
                                    : 'text-danger font-medium tabular-nums'
                                }
                              >
                                {formatMoney(entry.cash_amount)}
                              </span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    );
                  }}
                />
                <Bar
                  dataKey="inAmount"
                  name="In"
                  fill="var(--success)"
                  radius={[3, 3, 0, 0]}
                  maxBarSize={30}
                  {...MOTION}
                />
                <Bar
                  dataKey="outAmount"
                  name="Out"
                  fill="var(--danger)"
                  radius={[3, 3, 0, 0]}
                  maxBarSize={30}
                  {...MOTION}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardBody>
    </Card>
  );
}
