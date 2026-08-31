/**
 * Billing API client - money in and money out.
 *
 * Amounts are `string`, as everywhere else: a JSON number is an IEEE-754 double in
 * JavaScript, and these figures post to the ledger.
 */
import { api } from '@/lib/api';
import type { Money, Page, PageQuery } from '@/features/accounting/api';

export type Direction = 'in' | 'out';

/** How a money account reconciles: cash against a count, a bank against a statement. */
export type MoneyKind = 'cash' | 'bank';

/**
 * What a place money sits in actually *is*.
 *
 * `credit_card` is the one that matters: it is a **liability**, not cash. Spending on a
 * credit card increases what you owe rather than reducing what you hold, so the UI must
 * never total it alongside a bank balance. Distinct from {@link MoneyKind}, which is only
 * the two options offered when *adding* an account by hand - a card account is created by
 * registering a card, never by picking it from that menu.
 */
export type MoneyAccountKind = 'cash' | 'bank' | 'credit_card';

export type CardKind = 'credit' | 'debit';

export type CardNetwork =
  'visa' | 'mastercard' | 'rupay' | 'amex' | 'discover' | 'diners' | 'jcb' | 'maestro' | 'other';

export interface Category {
  id: string;
  code: string;
  name: string;
  /** Income categories cannot take money out, and vice versa. */
  direction: Direction;
  /** The parent group's name, used for `optgroup` - nearly eighty flat options is
   *  a list nobody reads to the end of. */
  group: string;
  is_default: boolean;
}

export interface MoneyAccount {
  id: string;
  code: string;
  name: string;
  is_default: boolean;
  /** Cash, a bank, or a credit card - see {@link MoneyAccountKind}. */
  kind: MoneyAccountKind;
  /**
   * Set when a card is what identifies this option.
   *
   * A debit card shares its `id` with the bank account it draws on, because it *is* that
   * account - so `id` alone cannot tell "HDFC Current" from "HDFC Debit ··4242". This is
   * the field that distinguishes them, and the reason the picker keys on `card_id ?? id`.
   */
  card_id: string | null;
  card_last4: string | null;
  card_network: string | null;

  /** False once archived. Archived accounts never appear in the payment pickers. */
  is_active: boolean;
  /**
   * Whether archiving is permitted at all.
   *
   * A capability flag from the server, not a rule to re-derive here: a seeded account
   * ("Cash on Hand", "Primary Bank Account") cannot be deactivated, because later
   * modules post to it by role. Offering the button anyway would produce a request that
   * always fails.
   */
  can_archive: boolean;
  /** Whether deleting is permitted: nothing posted to it, not seeded, and no card
   *  drawing on it. Archive when this is false - the history has to stay. */
  can_delete: boolean;
  /** Why deleting is refused, or null. Phrased for a person: shown as the tooltip on a
   *  disabled Delete, so the control explains itself instead of going missing. */
  delete_blocked_reason: string | null;

  /** Which bank the account is at, and whose it is. Absent for cash in hand. */
  bank_name: string | null;
  holder_name: string | null;
  /** The tail only. The **full** number is never on this payload - it fills a picker, and
   *  a client that just needs to tell two accounts apart has no use for the whole thing.
   *  Fetch {@link BankDetails} when it is actually needed. */
  account_number_last4: string | null;
}

/**
 * One account's details, **including the full account number.**
 *
 * Its own request rather than a field on {@link MoneyAccount}, so decrypting an account
 * number is a deliberate act behind its own permission check instead of something every
 * load of the billing screen does for every account.
 */
export interface BankDetails {
  account_id: string;
  /** The account's own name, so a rename is reflected on the same response. */
  name: string;
  bank_name: string | null;
  holder_name: string | null;
  account_number: string | null;
  account_number_last4: string | null;
}

/**
 * The facts about a bank account the ledger has no use for.
 *
 * All optional: cash in hand has none of them, and a first account should not be blocked
 * on paperwork. Ignored entirely for a cash account.
 */
export interface BankDetailsBody {
  /** A new name for the account. Omitting it leaves the name alone.
   *
   *  **Renaming a seeded account is allowed**, unlike archiving or deleting one: the
   *  software finds it by its role, not by its name, so "Primary Bank Account" is just a
   *  placeholder nobody chose. */
  name?: string;
  bank_name?: string;
  holder_name?: string;
  /** As typed - spaces and dashes are stripped server-side. Stored **encrypted**, and
   *  stored in full, unlike a card number: it is what you quote to be paid and match
   *  against a statement, so keeping only four digits would make it useless. */
  account_number?: string;
}

export interface Card {
  id: string;
  label: string;
  kind: CardKind;
  network: CardNetwork;
  /** Four digits as a string - a card ending 0042 is not the number 42. */
  last4: string;
  /** Its own liability account for a credit card; the bank account for a debit card. */
  account_id: string;
  account_name: string;
  is_active: boolean;
  /** The name embossed on the card, if it was given. Kept in the clear - PCI DSS permits
   *  retaining a cardholder name; it is the number and the CVV that may not be kept. */
  holder_name: string | null;
  /** Whether deleting is permitted - false once anything has been recorded on the card's
   *  account. Archive it instead; the entries name it. */
  can_delete: boolean;
  /** Why deleting is refused, or null. See {@link MoneyAccount.delete_blocked_reason}. */
  delete_blocked_reason: string | null;
}

export interface BillingOptions {
  categories: Category[];
  money_accounts: MoneyAccount[];
  /** Cards on file. Separate from `money_accounts` because the two answer different
   *  questions: that list is "where can this payment go", this one is "what have I
   *  registered" - and an archived card belongs in neither. */
  cards: Card[];
  /** Today in the organization's timezone, not the server's UTC date. */
  today: string;
  currency: string;
}

export interface AddCardBody {
  label: string;
  kind: CardKind;
  /** As typed or pasted; spaces and dashes are fine. **Never stored** - the server keeps
   *  the network and the last four digits and discards the rest. */
  card_number: string;
  /** The name on the card. Optional. */
  holder_name?: string;
  /** Required for a debit card, ignored for a credit card. */
  bank_account_id?: string;
}

export interface TransferBody {
  from_account_id: string;
  to_account_id: string;
  amount: Money;
  entry_date?: string;
  description?: string;
  reference?: string;
}

export interface Transfer {
  entry_id: string;
  entry_number: string | null;
  date: string;
  amount: Money;
  description: string;
  from_account_id: string;
  from_account_name: string;
  to_account_id: string;
  to_account_name: string;
}

export interface BillingEntry {
  id: string;
  /** The ledger's own number, so the entry can be found in the journal. */
  entry_number: string | null;
  date: string;
  direction: Direction;
  amount: Money;
  description: string;
  reference: string | null;
  /** Who it came from (money in) or went to (money out). Free text, not a record. */
  party: string | null;

  category_id: string;
  category_name: string;
  money_account_id: string;
  money_account_name: string;

  created_at: string;
  /** Cancelled by a reversal. Still listed - the cancellation is part of the record. */
  is_reversed: boolean;
}

export interface BillingSummary {
  from_date: string;
  to_date: string;
  money_in: Money;
  money_out: Money;
  net: Money;
  entry_count: number;
}

export interface RecordEntryBody {
  direction: Direction;
  amount: Money;
  description: string;
  entry_date?: string;
  category_id?: string;
  money_account_id?: string;
  reference?: string;
  /** Who the money came from or went to. Required - the API rejects a blank. */
  party: string;
}

export const billingApi = {
  options: () => api.get<BillingOptions>('/billing/options'),

  list: (
    params?: PageQuery & {
      direction?: Direction;
      from_date?: string;
      to_date?: string;
      q?: string;
    },
  ) => api.get<Page<BillingEntry>>('/billing', { params }),

  summary: (params?: { from_date?: string; to_date?: string }) =>
    api.get<BillingSummary>('/billing/summary', { params }),

  record: (body: RecordEntryBody) => api.post<BillingEntry>('/billing', body),

  /**
   * Add a category from a name alone. The account code, parent group, and subtype are
   * derived server-side - nobody should need to understand the chart of accounts to
   * file a payment under a name the built-in list does not have.
   */
  createCategory: (name: string, direction: Direction) =>
    api.post<Category>('/billing/categories', { name, direction }),

  /**
   * Add a place money can sit - a second bank, a UPI wallet, a partner's petty cash.
   * The seeded chart only has one till and one current account.
   *
   * `details` are ignored for a cash account, which has no bank, number, or holder.
   */
  createMoneyAccount: (name: string, kind: MoneyKind, details?: BankDetailsBody) =>
    api.post<MoneyAccount>('/billing/money-accounts', { name, kind, ...details }),

  /** One account's details, with the account number in full. */
  bankDetails: (accountId: string) =>
    api.get<BankDetails>(`/billing/money-accounts/${accountId}/details`),

  /**
   * Set which bank an account is at, whose it is, and its number.
   *
   * A `PUT` because it replaces the whole set - sending a blank field clears it, which is
   * how someone removes a number they entered by mistake. This is also the only way the
   * seeded "Primary Bank Account" ever gets its details, since it exists before anyone has
   * said which bank it is.
   */
  saveBankDetails: (accountId: string, body: BankDetailsBody) =>
    api.put<BankDetails>(`/billing/money-accounts/${accountId}/details`, body),

  cards: (params?: { include_archived?: boolean }) => api.get<Card[]>('/billing/cards', { params }),

  /**
   * Every place money can sit, optionally including archived ones.
   *
   * Separate from `options()` because that payload feeds the recording form's pickers and
   * must never carry an archived account - a picker that offers a closed bank account is
   * a picker that posts to it.
   */
  moneyAccounts: (params?: { include_archived?: boolean }) =>
    api.get<MoneyAccount[]>('/billing/money-accounts', { params }),

  /** Stop offering an account without deleting it - entries still name it. */
  archiveMoneyAccount: (id: string) =>
    api.post<MoneyAccount>(`/billing/money-accounts/${id}/archive`, {}),

  restoreMoneyAccount: (id: string) =>
    api.post<MoneyAccount>(`/billing/money-accounts/${id}/restore`, {}),

  /**
   * Register a card from its number.
   *
   * **The number goes up once and is never stored.** The response has no field for it, so
   * there is nothing to hold in client state either - which is why this takes the number
   * as an argument rather than the form keeping it anywhere longer-lived.
   */
  addCard: (body: AddCardBody) => api.post<Card>('/billing/cards', body),

  /**
   * Correct a card's name, its holder, or its number.
   *
   * A `PATCH`, so an omitted field is left alone. **`kind` is not editable** - a credit card
   * owns a liability account and a debit card points at a bank account, so switching would
   * either orphan an account with postings or start filing card spending as money leaving a
   * bank account that never lost it.
   *
   * A corrected number is read and discarded exactly as on create, and can change the
   * derived network as well as the last four digits.
   */
  updateCard: (id: string, body: { label?: string; holder_name?: string; card_number?: string }) =>
    api.patch<Card>(`/billing/cards/${id}`, body),

  /**
   * Remove a card entirely. **Refused once anything has been recorded on it** - archive it
   * instead, because an entry names the card it was made on. `Card.can_delete` says which.
   */
  deleteCard: (id: string) => api.delete<void>(`/billing/cards/${id}`),

  /**
   * Remove an account entirely. Refused if it has postings, is seeded, or has a card drawing
   * on it - `MoneyAccount.can_delete` covers all three.
   */
  deleteMoneyAccount: (id: string) => api.delete<void>(`/billing/money-accounts/${id}`),

  /** Stop offering a card without deleting it - past entries still name it. */
  archiveCard: (id: string) => api.post<Card>(`/billing/cards/${id}/archive`, {}),

  restoreCard: (id: string) => api.post<Card>(`/billing/cards/${id}/restore`, {}),

  /**
   * Move money between two of your own accounts.
   *
   * No category, and that is not an omission: moving your own money is neither earning nor
   * spending it, so there is no income or expense line for it to go against. It is tagged
   * so the money-in/money-out totals ignore it - counting a transfer would show income
   * that never arrived from anywhere.
   */
  transfer: (body: TransferBody) => api.post<Transfer>('/billing/transfers', body),

  /**
   * Cancel an entry by posting its mirror image. There is no delete and no edit -
   * a posted ledger entry is immutable, so an opposite entry is the only honest undo.
   */
  reverse: (id: string, reason?: string) =>
    api.post<BillingEntry>(`/billing/${id}/reverse`, reason ? { reason } : {}),
};
