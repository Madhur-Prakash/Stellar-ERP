/**
 * Reducing typed or pasted text to a decimal number.
 *
 * In its own module rather than beside `NumberInput`, because a file that exports both a
 * component and a plain function breaks React fast refresh - the whole module reloads
 * instead of the component, losing form state on every save during development.
 */

export interface SanitiseOptions {
  /**
   * Digits allowed after the point.
   *
   * Defaults to 4, matching the `NUMERIC(18,4)` the backend stores amounts and
   * quantities in. Restricting further here would reject figures the database accepts -
   * a price of 0.0125 per unit, say - and the rejection would be invisible, since the
   * character simply fails to appear.
   */
  decimals?: number;
  /** Allow a leading minus. Off by default: a direction is chosen, not typed. */
  allowNegative?: boolean;
}

/**
 * Reduce arbitrary input to a decimal number, or to `''`.
 *
 * Pure, so its behaviour can be checked directly rather than through a rendered input.
 *
 * A partially typed number must survive: `"12."` is returned as-is, because rejecting it
 * would make the decimal point impossible to type. `"."` becomes `"0."` for the same
 * reason - someone typing `.5` means nought point five.
 *
 * Returns a **string**, never a `number`. Money crosses the wire as a decimal string
 * precisely so it never passes through a float, and parsing it here to satisfy an input
 * would reintroduce exactly that.
 */
export function sanitiseDecimal(raw: string, options: SanitiseOptions = {}): string {
  const { decimals = 4, allowNegative = false } = options;

  const negative = allowNegative && raw.trimStart().startsWith('-');
  // Everything that is not a digit or a point goes, which covers letters, currency
  // symbols, spaces, and the thousands separators in a pasted "1,23,456.78".
  const digitsAndPoints = raw.replace(/[^\d.]/g, '');

  const [whole = '', ...rest] = digitsAndPoints.split('.');
  const hasPoint = rest.length > 0;
  const fraction = rest.join('').slice(0, decimals);

  if (whole === '' && !hasPoint) return negative ? '-' : '';

  const sign = negative ? '-' : '';
  if (!hasPoint) return `${sign}${whole}`;
  // `whole || '0'` so a leading point reads as a number rather than as ".5".
  if (decimals === 0) return `${sign}${whole || '0'}`;
  return `${sign}${whole || '0'}.${fraction}`;
}
