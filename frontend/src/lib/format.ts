/**
 * Formatting helpers.
 *
 * **Nothing here hardcodes a currency, a locale, or a timezone.** Every default comes from
 * `localeSettings()`, which holds the signed-in organization's own settings - so changing
 * the currency in Settings changes every amount on every screen, with no call site touched.
 * The values were literal `'INR'` and `'en-IN'` defaults, which meant an organization
 * keeping books in dollars still saw rupees everywhere.
 *
 * Read at call time, never captured at module load: the settings arrive with the session,
 * which is after this module is imported.
 *
 * `Intl` formatters are cached: constructing one costs roughly as much as formatting a
 * hundred values, and a data table calls these per cell per render.
 */
import { localeSettings } from '@/lib/locale';

const currencyCache = new Map<string, Intl.NumberFormat>();

function currencyFormatter(currency: string, locale: string): Intl.NumberFormat {
  const key = `${locale}:${currency}`;
  let formatter = currencyCache.get(key);
  if (!formatter) {
    formatter = new Intl.NumberFormat(locale, {
      style: 'currency',
      currency,
      maximumFractionDigits: 2,
    });
    currencyCache.set(key, formatter);
  }
  return formatter;
}

/** Format money in the organization's currency. */
export function formatCurrency(amount: number, currency?: string, locale?: string): string {
  const settings = localeSettings();
  return currencyFormatter(currency ?? settings.currency, locale ?? settings.locale).format(amount);
}

/** Abbreviate a large number for a KPI tile: 1.2K, 3.4M, 1.1Cr. */
export function formatCompact(value: number, locale?: string): string {
  return new Intl.NumberFormat(locale ?? localeSettings().locale, {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(value);
}

export function formatNumber(value: number, locale?: string): string {
  return new Intl.NumberFormat(locale ?? localeSettings().locale).format(value);
}

export function formatPercent(value: number, fractionDigits = 1): string {
  return `${value >= 0 ? '+' : ''}${value.toFixed(fractionDigits)}%`;
}

/** Matches `YYYY-MM-DD` with no time component. */
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

/**
 * Format a date.
 *
 * **A date-only value is not converted between timezones, and that is the whole subtlety
 * here.** `"2026-07-30"` is a calendar date - the day an entry was posted - not an instant.
 * `new Date()` parses it as midnight UTC, so rendering it in a zone behind UTC would show
 * the 29th: every entry dated the 1st of a month would appear to fall in the previous one,
 * and a report filtered by month would disagree with the rows it listed. So a date-only
 * string is pinned to UTC, which returns the same calendar day to every viewer.
 *
 * A full timestamp *is* an instant, and is shown in the organization's zone - because
 * "which day did this happen on" is a question about the organization's clock, not the
 * clock of whoever is looking.
 */
export function formatDate(value: string | Date, locale?: string): string {
  const settings = localeSettings();
  const dateOnly = typeof value === 'string' && DATE_ONLY.test(value);
  const date = typeof value === 'string' ? new Date(value) : value;

  return new Intl.DateTimeFormat(locale ?? settings.locale, {
    dateStyle: 'medium',
    timeZone: dateOnly ? 'UTC' : settings.timeZone,
  }).format(date);
}

export function formatDateTime(value: string | Date, locale?: string): string {
  const settings = localeSettings();
  const date = typeof value === 'string' ? new Date(value) : value;
  return new Intl.DateTimeFormat(locale ?? settings.locale, {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: settings.timeZone,
  }).format(date);
}

/**
 * Relative time ("3 minutes ago"). Used in audit trails and session lists,
 * where the elapsed interval matters more than the absolute timestamp.
 */
export function formatRelative(value: string | Date, locale = 'en'): string {
  const date = typeof value === 'string' ? new Date(value) : value;
  const seconds = Math.round((date.getTime() - Date.now()) / 1000);
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' });

  const divisions: [number, Intl.RelativeTimeFormatUnit][] = [
    [60, 'second'],
    [60, 'minute'],
    [24, 'hour'],
    [7, 'day'],
    [4.34524, 'week'],
    [12, 'month'],
    [Number.POSITIVE_INFINITY, 'year'],
  ];

  let value_ = seconds;
  for (const [amount, unit] of divisions) {
    if (Math.abs(value_) < amount) return rtf.format(Math.round(value_), unit);
    value_ /= amount;
  }
  return rtf.format(Math.round(value_), 'year');
}

/** Two-letter initials for an avatar fallback. */
export function initials(name: string, fallback = '?'): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return fallback;
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase();
  return (parts[0]![0]! + parts[parts.length - 1]![0]!).toUpperCase();
}

/**
 * Format a money value that arrived from the API as a decimal **string**.
 *
 * The backend serialises money as a string on purpose: a JSON number is an
 * IEEE-754 double in JavaScript, so `1234567.89` would arrive as
 * `1234567.8899999999`. Passing that string straight to `formatCurrency` would
 * undo the whole point by calling `Number()` on it.
 *
 * `Intl.NumberFormat` accepts a string directly and formats it exactly, with no
 * float conversion anywhere in the path.
 */
export function formatMoney(
  value: string | null | undefined,
  currency?: string,
  locale?: string,
): string {
  const settings = localeSettings();
  const formatter = currencyFormatter(currency ?? settings.currency, locale ?? settings.locale);
  if (value === null || value === undefined || value === '') return formatter.format(0);
  return formatter.format(value as unknown as number);
}

/**
 * Compare two API money strings without converting to `number`.
 *
 * Returns a negative number, zero, or a positive number, like a comparator.
 * Used for sorting and for sign checks (is this balance negative?).
 */
export function compareMoney(a: string, b: string): number {
  const left = BigInt(scaleToInteger(a));
  const right = BigInt(scaleToInteger(b));
  return left < right ? -1 : left > right ? 1 : 0;
}

/** True when an API money string represents zero, whatever its scale. */
export function isZeroMoney(value: string | null | undefined): boolean {
  if (!value) return true;
  return /^-?0*(\.0*)?$/.test(value.trim());
}

/** True when an API money string is negative. */
export function isNegativeMoney(value: string | null | undefined): boolean {
  return !!value && value.trim().startsWith('-');
}

/**
 * Money for a dense report table: grouped, two decimals, no currency symbol.
 *
 * A statement with six money columns repeats `₹` six times per row for no information -
 * it is the same currency throughout, so the symbol belongs once in the heading. That is
 * how printed statements have always done it, and it is also what lets the columns fit
 * without scrolling sideways to compare two totals that must agree.
 *
 * Exact, like `formatMoney`: the string goes to `Intl` untouched, never through `Number`.
 */
export function formatAmount(value: string | null | undefined, locale?: string): string {
  if (value === null || value === undefined || value === '') return '0.00';
  return new Intl.NumberFormat(locale ?? localeSettings().locale, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value as unknown as number);
}

/**
 * Add API money strings exactly, returning a money string.
 *
 * Not `values.reduce((sum, v) => sum + Number(v), 0)`. A total on the trial balance is
 * the one figure whose whole purpose is to prove nothing has drifted, so computing it
 * through binary floating point - where 0.1 + 0.2 is not 0.3 - would undermine the report
 * it appears on. Scales to integers and adds with `BigInt`, the same way `compareMoney`
 * compares them.
 */
export function sumMoney(values: readonly string[], scale = 6): string {
  const total = values.reduce((sum, value) => sum + BigInt(scaleToInteger(value, scale)), 0n);

  const negative = total < 0n;
  const digits = (negative ? -total : total).toString().padStart(scale + 1, '0');
  const whole = digits.slice(0, digits.length - scale);
  const fraction = digits.slice(digits.length - scale);
  return `${negative ? '-' : ''}${whole}.${fraction}`;
}

/**
 * Normalise a decimal string to a fixed-scale integer string, so two values of
 * differing scale ("0" and "0.0000") compare equal.
 */
function scaleToInteger(value: string, scale = 6): string {
  const trimmed = value.trim();
  const negative = trimmed.startsWith('-');
  const [whole, fraction = ''] = trimmed.replace(/^[-+]/, '').split('.');
  const padded = (fraction + '0'.repeat(scale)).slice(0, scale);
  return `${negative ? '-' : ''}${whole}${padded}`;
}
