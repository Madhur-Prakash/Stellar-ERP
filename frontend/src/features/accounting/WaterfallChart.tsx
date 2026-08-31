/**
 * A waterfall: income, then each cost stepping down, ending on net profit.
 *
 * **Built from the P&L response, not recomputed.** The closing bar has to be the same
 * net profit the dashboard shows, and the only way to guarantee that is to use the
 * figures the statement itself returned rather than adding up something similar. If they
 * ever disagreed, nobody could tell which was right.
 *
 * **How a waterfall is drawn in Recharts**, since there is no waterfall series: two bars
 * on one `stackId`, where the first is invisible and just lifts the second to the right
 * height. Each step therefore carries a `base` (where the bar starts) and a `delta` (how
 * tall it is), computed from the running total.
 */
import { TrendingDown } from 'lucide-react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { InfoTip } from '@/components/ui/InfoTip';
import type { ProfitAndLoss } from '@/features/accounting/api';
import { formatCompact, formatMoney } from '@/lib/format';

interface Step {
  label: string;
  /** Invisible lifter, so the visible bar floats at the running total. */
  base: number;
  /** Height of the visible bar. Always positive - direction is carried by `sign`. */
  delta: number;
  sign: 'up' | 'down' | 'total';
  /** The exact decimal, for the tooltip and the label. */
  exact: string;
  /** The running total after this step, so a tooltip can say where you stand. */
  runningTotal: string;
}

/** Costs below this share of income are folded together - a wall of slivers is unreadable. */
const MAX_COST_STEPS = 8;

function buildSteps(report: ProfitAndLoss): Step[] {
  const steps: Step[] = [];
  let running = 0;

  const income = Number(report.total_income);
  steps.push({
    label: 'Income',
    base: Math.min(0, income),
    delta: Math.abs(income),
    sign: 'up',
    exact: report.total_income,
    runningTotal: String(income),
  });
  running = income;

  // Biggest cost first: the eye should meet the thing worth asking about immediately.
  const costs = [...report.expenses]
    .map((line) => ({ label: line.label, value: Number(line.amount), exact: line.amount }))
    .filter((line) => line.value !== 0)
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value));

  const shown = costs.slice(0, MAX_COST_STEPS);
  const folded = costs.slice(MAX_COST_STEPS);

  for (const cost of shown) {
    const after = running - cost.value;
    steps.push({
      label: cost.label,
      base: Math.min(running, after),
      delta: Math.abs(cost.value),
      sign: 'down',
      exact: cost.exact,
      runningTotal: String(after),
    });
    running = after;
  }

  if (folded.length > 0) {
    const amount = folded.reduce((sum, cost) => sum + cost.value, 0);
    const after = running - amount;
    steps.push({
      label: `Other (${folded.length})`,
      base: Math.min(running, after),
      delta: Math.abs(amount),
      sign: 'down',
      exact: String(amount),
      runningTotal: String(after),
    });
    running = after;
  }

  // The closing bar rises from zero, because it is a total rather than a change.
  const net = Number(report.net_profit);
  steps.push({
    label: net < 0 ? 'Net loss' : 'Net profit',
    base: Math.min(0, net),
    delta: Math.abs(net),
    sign: 'total',
    exact: report.net_profit,
    runningTotal: report.net_profit,
  });

  return steps;
}

function stepColour(step: Step): string {
  if (step.sign === 'up') return 'var(--success)';
  if (step.sign === 'down') return 'var(--danger)';
  // The total is coloured by its own sign: a loss should not look like a win.
  return Number(step.exact) < 0 ? 'var(--danger)' : 'var(--primary)';
}

export function ProfitWaterfallChart({
  report,
  isLoading,
}: {
  report: ProfitAndLoss | undefined;
  isLoading?: boolean;
}) {
  const steps = report ? buildSteps(report) : [];
  const hasActivity =
    report !== undefined &&
    (Number(report.total_income) !== 0 || Number(report.total_expenses) !== 0);

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <TrendingDown className="text-content-muted h-4 w-4" aria-hidden />
            How income became profit
          </span>
        }
        description="Income first, then each cost taken off it. The last bar is what was left."
        action={
          <InfoTip label="the waterfall" align="right">
            <p>
              Read left to right. The green bar is everything earned; each red bar is a cost
              stepping the running total down; the final bar is what remained.
            </p>
            <p>
              <strong>That last bar is the same net profit the dashboard shows.</strong> It comes
              from the profit &amp; loss statement rather than being added up again here, so the two
              cannot disagree.
            </p>
            <p>A loss is drawn in red and hangs below the zero line.</p>
          </InfoTip>
        }
      />
      <CardBody>
        {isLoading ? (
          <div className="text-content-muted py-16 text-center text-[13px]">Loading…</div>
        ) : !hasActivity ? (
          <EmptyState
            icon={TrendingDown}
            title="Nothing to break down yet"
            description="Record income and spending and this shows how one became the other."
            className="py-12"
          />
        ) : (
          <div className="h-80">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={steps} margin={{ top: 24, right: 8, left: -12, bottom: 48 }}>
                <CartesianGrid stroke="var(--border)" vertical={false} />
                <XAxis
                  dataKey="label"
                  stroke="var(--content-muted)"
                  fontSize={11}
                  tickLine={false}
                  axisLine={false}
                  interval={0}
                  angle={-30}
                  textAnchor="end"
                  height={56}
                />
                <YAxis
                  stroke="var(--content-muted)"
                  fontSize={11}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(value: number) => formatCompact(value)}
                />
                {/* Zero is the reference a waterfall is read against, so it is drawn. */}
                <ReferenceLine y={0} stroke="var(--content-muted)" strokeWidth={1} />
                <Tooltip
                  cursor={{ fill: 'var(--surface-sunken)' }}
                  contentStyle={{
                    background: 'var(--surface-raised)',
                    border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-lg)',
                    fontSize: 12,
                    boxShadow: 'var(--shadow-lg)',
                  }}
                  formatter={(_value, name, item) => {
                    // The invisible lifter must not appear in the tooltip.
                    if (name === 'base') return [];
                    const step = item?.payload as Step | undefined;
                    if (!step) return [];
                    const prefix = step.sign === 'down' ? '−' : step.sign === 'up' ? '+' : '';
                    const suffix =
                      step.sign === 'total'
                        ? ''
                        : ` · running total ${formatMoney(step.runningTotal)}`;
                    return [`${prefix}${formatMoney(step.exact)}${suffix}`, step.label];
                  }}
                />
                {/* Invisible: its only job is to lift the visible bar to the running total. */}
                <Bar
                  dataKey="base"
                  stackId="waterfall"
                  fill="transparent"
                  isAnimationActive={false}
                />
                <Bar
                  dataKey="delta"
                  stackId="waterfall"
                  radius={[3, 3, 0, 0]}
                  maxBarSize={52}
                  isAnimationActive
                  animationDuration={600}
                  animationEasing="ease-out"
                >
                  {steps.map((step) => (
                    <Cell key={step.label} fill={stepColour(step)} />
                  ))}
                  <LabelList
                    dataKey="exact"
                    position="top"
                    className="fill-content-muted"
                    fontSize={10}
                    formatter={(value: unknown) => formatCompact(Number(value))}
                  />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardBody>
    </Card>
  );
}
