/**
 * Analytics API client.
 *
 * Money is a `string` throughout - see `features/accounting/api.ts`. Confidence-style
 * numbers (percentage change) are strings for the same reason: they are `Decimal`
 * server-side, and JSON's only numeric type is a float.
 *
 * **The period vocabulary is not duplicated here as logic.** `Period` mirrors the
 * server's enum so calls type-check, but what "this financial year" *means* in dates
 * is resolved server-side against the organization's fiscal-year start. Re-deriving
 * an April-start year in TypeScript would eventually disagree with the ledger, and
 * the disagreement would be silent.
 */
import { api } from '@/lib/api';
import type { Money } from '@/features/accounting/api';

export type Period =
  | 'this_month'
  | 'last_month'
  | 'this_quarter'
  | 'this_fiscal_year'
  | 'last_30_days'
  | 'last_12_months';

export interface DateSpan {
  start: string;
  end: string;
  days: number;
}

export interface Movement {
  current: Money;
  previous: Money;
  /**
   * Null when the previous period gives no basis for a percentage - going from
   * zero is an infinite increase, not "+100%". Render "no prior data", never a
   * number.
   */
  change_percent: string | null;
}

export interface Dashboard {
  period: Period;
  period_label: string;
  span: DateSpan;
  /** What the percentages are measured against. Shown so "up 12%" is checkable. */
  comparison: DateSpan;
  currency: string;

  revenue: Movement;
  expenses: Movement;
  gross_profit: Movement;
  net_profit: Movement;

  /** Balances as at the end of the window, not movement within it. */
  cash: Money;
  receivables: Money;
  payables: Money;
  inventory_value: Money;

  overdue_receivables: Money;
  overdue_payables: Money;

  invoices_issued: number;
  bills_received: number;
}

export interface TrendPoint {
  label: string;
  start: string;
  end: string;
  income: Money;
  expenses: Money;
  profit: Money;
}

export interface Trend {
  span: DateSpan;
  points: TrendPoint[];
  total_income: Money;
  total_expenses: Money;
  total_profit: Money;
}

export interface RankedRow {
  id: string | null;
  label: string;
  amount: Money;
  count: number;
}

export interface Ranking {
  span: DateSpan;
  rows: RankedRow[];
  /** Across all rows, not just the returned top N - so "these five are 62%" is true. */
  total: Money;
}

export interface ControlCheck {
  name: string;
  ledger: Money;
  subledger: Money;
  difference: Money;
  agrees: boolean;
}

export interface ControlChecks {
  as_of: string;
  checks: ControlCheck[];
  all_agree: boolean;
}

export interface PeriodOptions {
  options: { value: Period; label: string }[];
  fiscal_year_start_month: number;
  today: string;
}

export const analyticsApi = {
  periods: () => api.get<PeriodOptions>('/analytics/periods'),
  dashboard: (period: Period = 'this_month') =>
    api.get<Dashboard>('/analytics/dashboard', { params: { period } }),
  /** Explicit dates override the preset, so a chart can match a filtered report. */
  trend: (period: Period = 'last_12_months', range?: { from_date: string; to_date: string }) =>
    api.get<Trend>('/analytics/trend', { params: range ? { ...range } : { period } }),
  topCustomers: (period: Period = 'this_fiscal_year', limit = 5) =>
    api.get<Ranking>('/analytics/top-customers', { params: { period, limit } }),
  topProducts: (period: Period = 'this_fiscal_year', limit = 5) =>
    api.get<Ranking>('/analytics/top-products', { params: { period, limit } }),
  controlChecks: (asOf?: string) =>
    api.get<ControlChecks>('/analytics/control-checks', {
      params: asOf ? { as_of: asOf } : undefined,
    }),
};
