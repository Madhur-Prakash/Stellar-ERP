/**
 * Card presentation and the one check worth doing client-side.
 *
 * A plain module, not a component file: shared by the panel on Billing and by the Accounts
 * page, and a file that exports both components and helpers breaks fast refresh for
 * everything in it.
 */
import type { CardNetwork } from '@/features/billing/api';

export const NETWORK_LABELS: Record<CardNetwork, string> = {
  visa: 'Visa',
  mastercard: 'Mastercard',
  rupay: 'RuPay',
  amex: 'Amex',
  discover: 'Discover',
  diners: 'Diners Club',
  jcb: 'JCB',
  maestro: 'Maestro',
  // Not "Unknown": the card works perfectly well, the software just does not claim to
  // recognise the scheme from its leading digits.
  other: 'Card',
};

/** The range real card numbers fall in - ISO/IEC 7812, and the shortest Maestro. */
export const MIN_DIGITS = 12;
export const MAX_DIGITS = 19;

/** Strip the separators a person types or pastes. */
export function normaliseCardNumber(raw: string): string {
  return raw.replace(/[\s-]/g, '');
}

/**
 * Length and character check, kept separate from {@link passesLuhn}.
 *
 * The split lets a form tell "that is not a card number" from "that is a card number with a
 * typo in it", which are different things to say to someone - and stops it complaining
 * about a check digit before they have finished typing.
 */
export function isPlausibleCardNumber(digits: string): boolean {
  return digits.length >= MIN_DIGITS && digits.length <= MAX_DIGITS && /^\d+$/.test(digits);
}

/**
 * Why the card number is not acceptable yet, or `null` when it is.
 *
 * **This exists because the "Add card" button used to disable itself in silence.** Type
 * eleven digits and nothing happened: no message, no error, a greyed-out button, and no way
 * to tell that one more digit was needed. The rules were only ever expressed as a boolean
 * for the button's `disabled` prop, which is not something a person can read.
 *
 * Returns a message for every state that blocks saving, and counts digits out loud while
 * the number is too short - "11 of at least 12 digits" answers the question the greyed-out
 * button raises, where "Invalid" would not.
 */
export function cardNumberProblem(digits: string): string | null {
  if (digits === '') return null;
  if (!/^\d+$/.test(digits)) return 'Digits only.';
  if (digits.length < MIN_DIGITS) {
    // Counted out loud while the number is short, rather than "invalid": one digit missing
    // and one digit wrong need different things from the user, and until the length is right
    // there is nothing meaningful to say about the digits themselves.
    return `${digits.length} of at least ${MIN_DIGITS} digits.`;
  }
  if (digits.length > MAX_DIGITS) {
    return `A card number is at most ${MAX_DIGITS} digits.`;
  }
  // The Luhn check digit, and it **blocks the save**. Every real card number satisfies it,
  // so failing means a typo - and the last four digits are how this card gets recognised on
  // an entry months later, so a wrong label defeats the point of keeping one. Only asked
  // once the length is plausible, or it would fire on every keystroke while typing.
  if (!passesLuhn(digits)) return 'That is not a valid card number - check the digits.';
  return null;
}

/**
 * The Luhn check digit, so a typo is caught before a round trip.
 *
 * A duplicate of the server's check, deliberately: the server stays the authority, this
 * only saves someone a request to be told they mistyped one digit. Safe to duplicate
 * because Luhn is a fixed algorithm that cannot drift - unlike the issuer-range table that
 * names the scheme, which is left entirely to the server.
 */
export function passesLuhn(digits: string): boolean {
  if (!/^\d+$/.test(digits)) return false;

  let total = 0;
  // Doubling every second digit from the right, so the parity depends on the length.
  const parity = digits.length % 2;
  for (let index = 0; index < digits.length; index += 1) {
    let value = Number(digits[index]);
    if (index % 2 === parity) {
      value *= 2;
      if (value > 9) value -= 9;
    }
    total += value;
  }
  return total % 10 === 0;
}
