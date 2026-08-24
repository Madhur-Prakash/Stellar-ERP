/**
 * The organization's currency, timezone, and financial year, in one place.
 *
 * Every amount and every date in this app has to be rendered the organization's way, and
 * they are rendered from about a hundred call sites. Threading three arguments through all
 * of them would put the same decision in a hundred places, and the first one anybody forgot
 * would quietly print rupees to a business keeping books in dollars.
 *
 * So the formatters read from here instead. It is a module-level holder set once from the
 * session payload, the same shape as `setAccessToken` in `lib/api.ts` - deliberately, since
 * this is the same kind of value: process-wide, arriving after boot, needed everywhere.
 *
 * **The defaults are a last resort, not a policy.** They apply only before a session exists
 * - the login screen, and the instant before `/auth/me` returns. Once a user is signed in,
 * every value here comes from their organization row.
 */

export interface LocaleSettings {
  /** ISO 4217, e.g. `INR`. */
  currency: string;
  /** BCP 47 tag, which decides digit grouping - `en-IN` groups as 1,00,000. */
  locale: string;
  /** IANA zone, e.g. `Asia/Kolkata`. Decides which calendar day an instant falls on. */
  timeZone: string;
  /** 1-12. April is 4, which is the Indian financial year. */
  fiscalYearStartMonth: number;
}

/**
 * Grouping style follows the currency, not the browser.
 *
 * A rupee figure reads `1,00,000` and a dollar figure `100,000`, and which is right is a
 * property of the books rather than of the machine looking at them - an accountant in London
 * reviewing Indian accounts should see the Indian grouping. Only currencies whose
 * conventional grouping differs from the Western default need an entry.
 */
const LOCALE_BY_CURRENCY: Record<string, string> = {
  INR: 'en-IN',
  LKR: 'en-LK',
  NPR: 'ne-NP',
  BDT: 'bn-BD',
};

const FALLBACK: LocaleSettings = {
  currency: 'INR',
  locale: 'en-IN',
  timeZone: 'Asia/Kolkata',
  fiscalYearStartMonth: 4,
};

let current: LocaleSettings = FALLBACK;

/**
 * Adopt an organization's settings.
 *
 * Called from the auth provider whenever the signed-in organization changes, including on
 * sign-out - where passing nothing restores the defaults, so the next user does not inherit
 * the last one's currency.
 */
export function setLocaleSettings(
  organization: { currency?: string; timezone?: string; fiscal_year_start_month?: number } | null,
): void {
  if (organization === null) {
    current = FALLBACK;
    return;
  }

  const currency = organization.currency ?? FALLBACK.currency;
  current = {
    currency,
    locale: LOCALE_BY_CURRENCY[currency] ?? 'en-US',
    timeZone: organization.timezone ?? FALLBACK.timeZone,
    fiscalYearStartMonth: organization.fiscal_year_start_month ?? FALLBACK.fiscalYearStartMonth,
  };
}

/** The settings in force. Read at format time, never captured at module load. */
export function localeSettings(): LocaleSettings {
  return current;
}
