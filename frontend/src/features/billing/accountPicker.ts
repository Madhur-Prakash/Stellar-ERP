/**
 * Turning the account list into dropdown options.
 *
 * Its own module, and not because of file size: these are plain functions, and a file that
 * exports both components and helpers breaks fast refresh for everything in it. The same
 * reason `passwordPolicy.ts` sits beside the auth pages.
 *
 * All four exist to handle one awkward fact about the API's account list - **a debit card
 * arrives with the same `id` as the bank account it draws on**, because it is not a
 * separate place money lives, it is another way of touching the same one. Every function
 * here is about keeping that from turning into a broken picker.
 */
import type { SelectGroup } from '@/components/ui/Select';
import type { MoneyAccount } from '@/features/billing/api';

/**
 * A stable, unique value for one option in an account dropdown.
 *
 * **Not simply `account.id`, and that is load-bearing.** "HDFC Current" and "HDFC Debit
 * ··4242" share an `id`. Two `<option>`s with one value is a select that cannot represent
 * the user's choice - picking the card would silently snap back to the bank account.
 * Keying on the card id where there is one keeps them distinct on screen;
 * {@link accountForKey} maps the choice back to the account id the API wants.
 */
export function moneyAccountKey(account: MoneyAccount): string {
  return account.card_id ?? account.id;
}

/** The account a {@link moneyAccountKey} came from. */
export function accountForKey(accounts: MoneyAccount[], key: string): MoneyAccount | undefined {
  return accounts.find((account) => moneyAccountKey(account) === key);
}

/**
 * Accounts split into "Cash & bank" and "Cards".
 *
 * Grouped rather than flat because the two are not interchangeable: one is money you have,
 * the other can be money you owe, and a heading says so without needing a sentence of
 * explanation on a dropdown.
 */
export function moneyAccountGroups(accounts: MoneyAccount[]): SelectGroup[] {
  const groups: SelectGroup[] = [];
  for (const account of accounts) {
    const label = account.card_id ? 'Cards' : 'Cash & bank';
    const option = { value: moneyAccountKey(account), label: account.name };
    const existing = groups.find((group) => group.label === label);
    if (existing) existing.options.push(option);
    else groups.push({ label, options: [option] });
  }
  return groups;
}

/**
 * The accounts a transfer can move between, one entry per real account.
 *
 * Deduplicated by `id`, which drops debit cards and keeps everything else: a debit card
 * *is* the bank account it draws on, so offering both would let someone pick a "from" and a
 * "to" that are the same account under two names. Credit cards survive, because they own a
 * distinct account - and paying a card bill off a bank account is the transfer people most
 * want to record.
 *
 * Cash and bank accounts come first from the API, so the surviving entry of a duplicated
 * pair is the bank account rather than the card. That ordering is relied on here.
 */
export function transferableAccounts(accounts: MoneyAccount[]): MoneyAccount[] {
  const seen = new Set<string>();
  const unique: MoneyAccount[] = [];
  for (const account of accounts) {
    if (seen.has(account.id)) continue;
    seen.add(account.id);
    unique.push(account);
  }
  return unique;
}
