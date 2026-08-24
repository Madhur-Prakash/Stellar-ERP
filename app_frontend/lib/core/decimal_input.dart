import 'package:flutter/services.dart';

/// Reducing typed or pasted text to a decimal number.
///
/// In its own file rather than beside `AppNumberInput`, matching the web app's
/// split - and here it earns it twice over, because a `TextInputFormatter` is the
/// idiomatic Flutter place for this and it is worth being able to test the pure
/// function underneath it directly.
///
/// Returns a **string**, never a `num`. Money crosses the wire as a decimal string
/// precisely so it never passes through a float, and parsing it here to satisfy an
/// input would reintroduce exactly that.

/// Reduce arbitrary input to a decimal number, or to `''`.
///
/// A partially typed number must survive: `"12."` is returned as-is, because
/// rejecting it would make the decimal point impossible to type. `"."` becomes
/// `"0."` for the same reason - someone typing `.5` means nought point five.
///
/// [decimals] defaults to 4, matching the `NUMERIC(18,4)` the backend stores
/// amounts and quantities in. Restricting further would reject figures the
/// database accepts - a price of 0.0125 per unit, say - and the rejection would be
/// invisible, since the character simply fails to appear.
///
/// [allowNegative] is off by default: a direction is chosen, not typed.
String sanitiseDecimal(
  String raw, {
  int decimals = 4,
  bool allowNegative = false,
}) {
  final bool negative = allowNegative && raw.trimLeft().startsWith('-');

  // Everything that is not a digit or a point goes, which covers letters,
  // currency symbols, spaces, and the thousands separators in a pasted
  // "1,23,456.78".
  final String digitsAndPoints = raw.replaceAll(RegExp(r'[^\d.]'), '');

  final List<String> segments = digitsAndPoints.split('.');
  final String whole = segments.first;
  final bool hasPoint = segments.length > 1;
  String fraction = hasPoint ? segments.sublist(1).join() : '';
  if (fraction.length > decimals) fraction = fraction.substring(0, decimals);

  if (whole.isEmpty && !hasPoint) return negative ? '-' : '';

  final String sign = negative ? '-' : '';
  if (!hasPoint) return '$sign$whole';
  // `whole.isEmpty ? '0'` so a leading point reads as a number rather than ".5".
  if (decimals == 0) return '$sign${whole.isEmpty ? '0' : whole}';
  return '$sign${whole.isEmpty ? '0' : whole}.$fraction';
}

/// The formatter that puts [sanitiseDecimal] in front of a `TextField`.
///
/// **Not `keyboardType: TextInputType.number` alone.** That only changes which
/// keyboard appears; on a desktop with a physical keyboard it changes nothing at
/// all, and a letter typed into an amount field would be accepted and then
/// rejected by the server. Filtering at the input means a letter simply never
/// appears - there is no error message to dismiss, because nothing invalid was
/// ever accepted.
///
/// The cursor is placed relative to the *end* of the text rather than left where
/// it was. Characters can be removed by sanitising, so a raw offset would land
/// mid-number; measuring from the end keeps the caret where the typist expects it
/// for the append-heavy way amounts are actually entered.
class DecimalInputFormatter extends TextInputFormatter {
  const DecimalInputFormatter({this.decimals = 4, this.allowNegative = false});

  final int decimals;
  final bool allowNegative;

  @override
  TextEditingValue formatEditUpdate(
    TextEditingValue oldValue,
    TextEditingValue newValue,
  ) {
    final String cleaned = sanitiseDecimal(
      newValue.text,
      decimals: decimals,
      allowNegative: allowNegative,
    );

    if (cleaned == newValue.text) return newValue;

    final int fromEnd = newValue.text.length - newValue.selection.baseOffset;
    final int offset = (cleaned.length - fromEnd).clamp(0, cleaned.length);

    return TextEditingValue(
      text: cleaned,
      selection: TextSelection.collapsed(offset: offset),
      composing: TextRange.empty,
    );
  }
}
