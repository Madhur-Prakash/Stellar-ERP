/**
 * Accounting, sales, and purchasing API client.
 *
 * **Money is a `string` in every type here, never a `number`.** The backend
 * serialises `Decimal` as a decimal string because a JSON number is an IEEE-754
 * double in JavaScript - `1234567.89` would arrive as `1234567.8899999999`.
 * Typing these as `number` would make TypeScript happily let `Number()` creep in.
 * Format with `formatMoney`, compare with `compareMoney`.
 */
import { api } from '@/lib/api';

// ---------------------------------------------------------------------------
// Shared
// ---------------------------------------------------------------------------
export interface Page<T> {
  items: T[];
  meta: {
    page: number;
    page_size: number;
    total_items: number;
    total_pages: number;
    has_next: boolean;
    has_previous: boolean;
  };
}

/** A money amount as it crosses the wire. Never widen this to `number`. */
export type Money = string;

export interface PageQuery {
  page?: number;
  page_size?: number;
}

// ---------------------------------------------------------------------------
// Accounting - chart of accounts
// ---------------------------------------------------------------------------
export type AccountType = 'asset' | 'liability' | 'equity' | 'income' | 'expense';

export interface Account {
  id: string;
  code: string;
  name: string;
  account_type: AccountType;
  subtype: string;
  parent_id: string | null;
  depth: number;
  is_group: boolean;
  is_active: boolean;
  is_system: boolean;
  system_key: string | null;
  description: string | null;
  normal_balance: 'debit' | 'credit';
  is_postable: boolean;
  total_debit: Money;
  total_credit: Money;
  balance: Money;
}

export interface Journal {
  id: string;
  code: string;
  name: string;
  journal_type: string;
  number_prefix: string;
  is_active: boolean;
}

export interface AccountingPeriod {
  id: string;
  name: string;
  start_date: string;
  end_date: string;
  status: 'open' | 'closed' | 'locked';
  accepts_postings: boolean;
}

export interface FiscalYear {
  id: string;
  name: string;
  start_date: string;
  end_date: string;
  status: string;
  periods: AccountingPeriod[];
}

// ---------------------------------------------------------------------------
// Accounting - journal entries
// ---------------------------------------------------------------------------
export type EntryStatus = 'draft' | 'posted' | 'reversed';

export interface JournalEntryLine {
  id: string;
  line_number: number;
  account_id: string;
  account_code: string;
  account_name: string;
  debit: Money;
  credit: Money;
  description: string | null;
}

export interface JournalEntry {
  /**
   * Whether cash actually moved, and which way.
   *
   * An entry always has both a debit and a credit, so "debited or credited" has no
   * single answer - the useful question is whether money came in or went out, decided
   * by which side the cash account sits on. Null when no cash was involved, or when the
   * entry only moved money between your own accounts.
   */
  cash_direction: 'in' | 'out' | null;
  cash_amount: Money;
  id: string;
  journal_id: string;
  journal_code: string;
  entry_number: string | null;
  entry_date: string;
  narration: string;
  reference: string | null;
  /** Who the money came from or went to. Null on entries made before it was required. */
  counterparty: string | null;
  status: EntryStatus;
  total_debit: Money;
  total_credit: Money;
  currency: string;
  posted_at: string | null;
  reversed_at: string | null;
  reverses_id: string | null;
  source_type: string | null;
  lines: JournalEntryLine[];
}

export interface JournalEntryLineInput {
  account_id: string;
  debit?: Money;
  credit?: Money;
  description?: string | null;
}

export interface JournalEntryCreate {
  journal_id: string;
  entry_date: string;
  narration: string;
  reference?: string | null;
  lines: JournalEntryLineInput[];
  post?: boolean;
}

// ---------------------------------------------------------------------------
// Accounting - reports
// ---------------------------------------------------------------------------
export interface TrialBalanceRow {
  /** Movement through the account before netting. Non-zero with a nil net means the
   *  activity cancelled out - usually a reversal. */
  gross_debit: Money;
  gross_credit: Money;
  /** The parties this account has dealt with, as typed. Empty when its entries named
   *  nobody. Not split by direction — that belongs to a transaction, not a balance. */
  parties: string[];
  account_id: string;
  code: string;
  name: string;
  account_type: AccountType;
  debit: Money;
  credit: Money;
}

export interface TrialBalance {
  /** Entries cancelled by a reversal in this window. A reversal leaves no trace in the
   *  net figures, so without this the report cannot be reconciled against the journal. */
  reversed_entry_count: number;
  as_of: string;
  rows: TrialBalanceRow[];
  total_debit: Money;
  total_credit: Money;
  is_balanced: boolean;
}

export interface ReportLine {
  label: string;
  amount: Money;
  level: number;
  is_total: boolean;
  account_code: string | null;
}

export interface ProfitAndLoss {
  from_date: string;
  to_date: string;
  income: ReportLine[];
  expenses: ReportLine[];
  total_income: Money;
  total_expenses: Money;
  cost_of_goods_sold: Money;
  gross_profit: Money;
  net_profit: Money;
}

export interface BalanceSheet {
  as_of: string;
  assets: ReportLine[];
  liabilities: ReportLine[];
  equity: ReportLine[];
  total_assets: Money;
  total_liabilities: Money;
  total_equity: Money;
  current_period_earnings: Money;
  is_balanced: boolean;
}

export interface LedgerLine {
  entry_id: string;
  entry_number: string | null;
  entry_date: string;
  narration: string;
  journal_code: string;
  debit: Money;
  credit: Money;
  running_balance: Money;
}

export interface AccountLedger {
  account: Account;
  from_date: string;
  to_date: string;
  opening_balance: Money;
  closing_balance: Money;
  total_debit: Money;
  total_credit: Money;
  lines: LedgerLine[];
}

export const accountingApi = {
  /** `as_of` reports balances at a past date, so a chart can match a filtered report. */
  accounts: (params?: { account_type?: AccountType; postable_only?: boolean; as_of?: string }) =>
    api.get<Account[]>('/accounts', { params }),

  journals: () => api.get<Journal[]>('/journals'),

  fiscalYears: () => api.get<FiscalYear[]>('/fiscal-years'),

  closePeriod: (periodId: string, lock = false) =>
    api.post<{ message: string }>(`/fiscal-years/periods/${periodId}/close`, null, {
      params: { lock },
    }),

  entries: (params?: PageQuery & { status?: EntryStatus; account_id?: string }) =>
    api.get<Page<JournalEntry>>('/journal-entries', { params }),

  entry: (id: string) => api.get<JournalEntry>(`/journal-entries/${id}`),

  createEntry: (body: JournalEntryCreate) => api.post<JournalEntry>('/journal-entries', body),

  postEntry: (id: string) => api.post<JournalEntry>(`/journal-entries/${id}/post`),

  reverseEntry: (id: string, body: { reversal_date?: string; narration?: string }) =>
    api.post<JournalEntry>(`/journal-entries/${id}/reverse`, body),

  trialBalance: (params?: { as_of?: string }) =>
    api.get<TrialBalance>('/reports/trial-balance', { params }),

  profitAndLoss: (params: { from_date: string; to_date: string }) =>
    api.get<ProfitAndLoss>('/reports/profit-and-loss', { params }),

  /**
   * The balance sheet for a period, with the position it opened from.
   *
   * **A balance sheet is a position at a date, not a total over a window**, so a period
   * resolves to a single `as_of` - the last day of it - and the period's value is the second
   * column: the closing position of the day before it opened. Resolved server-side so this
   * client and the desktop one cannot disagree about what "this quarter" means for an
   * organization whose year starts in April.
   */
  balanceSheetView: (params?: {
    period?: StatementPeriod;
    as_of?: string;
    compare_to?: string;
    comparative?: boolean;
  }) => api.get<BalanceSheetView>('/reports/balance-sheet/view', { params }),

  /** Download the same statement as a spreadsheet or a PDF. */
  /**
   * Save the balance sheet as a file, at whatever location the user picks.
   *
   * `stamp` is the date the sheet is drawn at, and it belongs in the suggested name: these
   * get filed, and four exports of four quarters all called `balance-sheet.xlsx` overwrite
   * each other. The caller passes it because only it knows the *resolved* date - for a named
   * period like "this quarter" the server chooses that, not the query.
   */
  exportBalanceSheet: (
    format: 'xlsx' | 'pdf',
    params?: {
      period?: StatementPeriod;
      as_of?: string;
      compare_to?: string;
      comparative?: boolean;
    },
    stamp?: string,
  ) =>
    api.download(
      '/reports/balance-sheet/export',
      stamp ? `balance-sheet-${stamp}.${format}` : `balance-sheet.${format}`,
      { params: { ...params, format } },
    ),

  balanceSheet: (params?: { as_of?: string }) =>
    api.get<BalanceSheet>('/reports/balance-sheet', { params }),

  ledger: (accountId: string, params: { from_date: string; to_date: string }) =>
    api.get<AccountLedger>(`/accounts/${accountId}/ledger`, { params }),
};

/** The named windows a statement can be asked for - see `statement_periods.py`. */
export type StatementPeriod =
  'to_date' | 'this_quarter' | 'last_quarter' | 'this_fiscal_year' | 'last_fiscal_year' | 'custom';

export interface BalanceSheetView {
  period: StatementPeriod;
  /** What to call the window on screen, decided server-side so both clients agree. */
  period_label: string;
  sheet: BalanceSheet;
  /** The position the period opened from, or null when none was asked for. A whole sheet
   *  rather than a second amount per line: the two dates can hold different accounts. */
  comparative: BalanceSheet | null;
  currency: string;
}
