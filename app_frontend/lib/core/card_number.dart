/// Reading a card number, without keeping it.
///
/// Its own module for the same reason the backend keeps `billing/cards.py` apart from
/// everything else: the code that touches a full card number should be small enough to
/// read in one sitting, import nothing, and be testable without a widget tree.
///
/// **There is nothing here that stores, returns, or logs the digits it was given.**
/// Every function takes a number and answers a yes/no question about it.
library;

/// Separators people type or paste.
final RegExp _separators = RegExp(r'[\s-]');

/// The range real card numbers fall in. ISO/IEC 7812 allows up to 19 digits; the
/// shortest scheme still in circulation is 12 (some Maestro).
const int minCardDigits = 12;
const int maxCardDigits = 19;

/// Strip spaces and dashes. Returns digits, or whatever else was in there.
String normaliseCardNumber(String raw) => raw.replaceAll(_separators, '');

/// Length and character check, before the arithmetic.
///
/// Separate from [passesLuhn] so the form can tell "that is not a card number" from
/// "that is a card number with a typo in it", which are different things to say.
bool isPlausibleCardNumber(String digits) =>
    digits.length >= minCardDigits &&
    digits.length <= maxCardDigits &&
    RegExp(r'^\d+$').hasMatch(digits);

/// The check digit, as every issuer computes it.
///
/// A duplicate of the server's check, and deliberately so: the server remains the
/// authority, this only saves someone a round trip to be told they mistyped one digit.
/// Safe to duplicate because Luhn is a fixed algorithm that cannot drift - unlike the
/// network detection, which is a table of issuer ranges and is left entirely to the
/// server. It says nothing about whether the card exists; that is not knowable here and
/// not needed, because nothing is ever charged.
bool passesLuhn(String digits) {
  if (digits.isEmpty || !RegExp(r'^\d+$').hasMatch(digits)) return false;

  int total = 0;
  // Doubling every second digit from the right, so the parity depends on the length.
  final int parity = digits.length % 2;
  for (int index = 0; index < digits.length; index += 1) {
    int value = digits.codeUnitAt(index) - 0x30;
    if (index % 2 == parity) {
      value *= 2;
      if (value > 9) value -= 9;
    }
    total += value;
  }
  return total % 10 == 0;
}

/// Why the number is not acceptable yet, or null when it is.
///
/// **This exists because the "Add card" button used to disable itself in silence.** Eleven
/// digits produced no message, no error, and a greyed-out button with no way to tell that
/// one more digit was needed - the rules lived only as a boolean for `onPressed`, which is
/// not something a person can read.
///
/// Counts digits out loud while the number is short: "11 of at least 12 digits" answers the
/// question a greyed-out button raises, where "Invalid" would not.
String? cardNumberProblem(String digits) {
  if (digits.isEmpty) return null;
  if (!RegExp(r'^\d+$').hasMatch(digits)) return 'Digits only.';
  if (digits.length < minCardDigits) {
    return '${digits.length} of at least $minCardDigits digits.';
  }
  if (digits.length > maxCardDigits) {
    return 'A card number is at most $maxCardDigits digits.';
  }
  // The Luhn check digit, and it **blocks the save**. Every real card number satisfies it,
  // so failing means a typo - and the last four digits are how this card gets recognised on
  // an entry months later, so a wrong label defeats the point of keeping one. Asked only
  // once the length is plausible, or it would fire on every keystroke while typing.
  if (!passesLuhn(digits)) {
    return 'That is not a valid card number - check the digits.';
  }
  return null;
}
