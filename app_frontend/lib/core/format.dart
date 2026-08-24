/// Formatting helpers.
///
/// **Nothing here hardcodes a currency, a locale, or a timezone.** Every default
/// comes from [localeSettings], which holds the signed-in organization's own
/// settings - so changing the currency in Settings changes every amount on every
/// screen, with no call site touched.
///
/// **Money never passes through a `double`, and that is the single most important
/// property of this file.** The backend serialises `Decimal` as a decimal string
/// on purpose: `1234567.89` as an IEEE-754 double is `1234567.8899999999`. The web
/// app hands the string straight to `Intl.NumberFormat`, which accepts one and
/// formats it exactly. Dart's `NumberFormat.format` takes a `num`, so calling it
/// would reintroduce precisely the float conversion the string exists to avoid.
///
/// So the money formatters here do their own grouping from the digits of the
/// string, and the arithmetic ones ([sumMoney], [compareMoney]) scale to `BigInt`.
/// A trial balance's totals are the one figure whose entire purpose is to prove
/// nothing has drifted; computing them in binary floating point - where 0.1 + 0.2
/// is not 0.3 - would undermine the report they appear on.
///
/// `double` is used for chart geometry and for confidence thresholds, and both are
/// commented where they happen: a pixel position does not need exact decimal
/// arithmetic, a figure someone reads does.
library;

import 'package:intl/date_symbol_data_local.dart';
import 'package:intl/intl.dart';
import 'package:timezone/timezone.dart' as tz;

import 'locale_settings.dart';

// =============================================================================
// Locale data
// =============================================================================
/// Whether `intl`'s date symbols have been loaded.
///
/// **`DateFormat` throws for any locale but `en_US` until `initializeDateFormatting` has
/// run**, and that is a trap with a nasty shape: the default organization here keeps books
/// in rupees, so its locale is `en_IN`, so *every date on every screen* throws - but only
/// once a session exists, because the sign-in screen renders no dates. An app that starts
/// fine and then fails the moment it has something to show is the worst version of this.
///
/// So it is done lazily, here, rather than in `main`. A one-line call in `main` is easy to
/// forget, easy to drop in a refactor, and impossible for a test to hold to account -
/// whereas a formatter that loads what it needs cannot be called wrongly. The call is
/// idempotent and synchronous, and the flag keeps it to once per process.
bool _localeDataLoaded = false;

void _ensureLocaleData() {
  if (_localeDataLoaded) return;
  initializeDateFormatting();
  _localeDataLoaded = true;
}

// =============================================================================
// Currency symbols
// =============================================================================
/// Symbols for the currencies the Settings screen offers.
///
/// A table rather than `NumberFormat.currency`'s own lookup, because the grouping
/// is done by hand here (see the library note) and the symbol has to come from
/// somewhere. Anything not listed falls back to the ISO code, which is what a
/// statement would print anyway.
///
/// Raw strings for the dollar variants: a bare `$` in a Dart literal starts an
/// interpolation, so the four entries that contain one are prefixed with `r`.
const Map<String, String> _currencySymbols = <String, String>{
  'INR': '₹',
  'USD': r'$',
  'EUR': '€',
  'GBP': '£',
  'JPY': '¥',
  'AED': 'AED ',
  'SGD': r'S$',
  'AUD': r'A$',
  'CAD': r'C$',
  'LKR': 'Rs ',
  'NPR': 'रू ',
  'BDT': '৳',
};

String currencySymbol([String? currency]) {
  final String code = currency ?? localeSettings().currency;
  return _currencySymbols[code] ?? '$code ';
}

// =============================================================================
// Exact decimal handling
// =============================================================================
/// Scale a decimal string to a signed integer of [decimals] places, rounding
/// half-up.
///
/// Half-up rather than banker's rounding, matching `Intl.NumberFormat`'s default
/// and what an invoice printed by hand would do.
BigInt _scaled(String raw, int decimals) {
  final String trimmed = raw.trim();
  if (trimmed.isEmpty) return BigInt.zero;

  final bool negative = trimmed.startsWith('-');
  final String unsigned = trimmed.replaceFirst(RegExp(r'^[-+]'), '');

  final int point = unsigned.indexOf('.');
  final String whole = point == -1 ? unsigned : unsigned.substring(0, point);
  final String fraction = point == -1 ? '' : unsigned.substring(point + 1);

  // One extra digit, which is the one the rounding decision is made on.
  final String padded = fraction.padRight(decimals + 1, '0');
  final String kept = padded.substring(0, decimals);
  final String next = padded.substring(decimals, decimals + 1);

  final BigInt digits = BigInt.parse('${whole.isEmpty ? '0' : whole}$kept');
  final BigInt rounded = int.parse(next) >= 5 ? digits + BigInt.one : digits;

  return negative ? -rounded : rounded;
}

/// Group an unsigned digit string, Western or Indian.
///
/// Western is 3 at a time; Indian is 3 then 2 at a time, which is what makes a
/// lakh read as `1,00,000`.
String _group(String digits) {
  if (digits.length <= 3) return digits;

  if (!usesIndianGrouping) {
    final StringBuffer out = StringBuffer();
    final int lead = digits.length % 3 == 0 ? 3 : digits.length % 3;
    out.write(digits.substring(0, lead));
    for (int i = lead; i < digits.length; i += 3) {
      out.write(',');
      out.write(digits.substring(i, i + 3));
    }
    return out.toString();
  }

  // The last three digits stand alone; everything before them pairs up.
  final String last3 = digits.substring(digits.length - 3);
  String rest = digits.substring(0, digits.length - 3);

  final List<String> pairs = <String>[];
  while (rest.length > 2) {
    pairs.insert(0, rest.substring(rest.length - 2));
    rest = rest.substring(0, rest.length - 2);
  }
  if (rest.isNotEmpty) pairs.insert(0, rest);

  return '${pairs.join(',')},$last3';
}

/// Render a scaled integer back to a grouped decimal string.
String _render(BigInt scaled, int decimals) {
  final bool negative = scaled.isNegative;
  final String digits = scaled.abs().toString().padLeft(decimals + 1, '0');
  final String whole = digits.substring(0, digits.length - decimals);
  final String fraction = decimals == 0
      ? ''
      : digits.substring(digits.length - decimals);

  final String body = decimals == 0
      ? _group(whole)
      : '${_group(whole)}.$fraction';
  return negative ? '-$body' : body;
}

// =============================================================================
// Money
// =============================================================================
/// Format a money value that arrived from the API as a decimal **string**.
///
/// Exact: the digits of the string are grouped directly, with no float anywhere in
/// the path. A null or empty value formats as zero rather than as a blank, so a
/// column of figures never has a hole in it.
String formatMoney(String? value, {String? currency}) {
  final String symbol = currencySymbol(currency);
  final BigInt scaled = _scaled(value ?? '0', 2);
  final String rendered = _render(scaled, 2);
  // The sign goes outside the symbol - "-₹500", not "₹-500" - which is how both
  // `Intl` and a printed statement do it.
  return rendered.startsWith('-')
      ? '-$symbol${rendered.substring(1)}'
      : '$symbol$rendered';
}

/// Money for a dense report table: grouped, two decimals, no currency symbol.
///
/// A statement with six money columns repeats `₹` six times per row for no
/// information - it is the same currency throughout, so the symbol belongs once in
/// the heading. That is how printed statements have always done it, and it is also
/// what lets the columns fit without scrolling sideways to compare two totals that
/// must agree.
String formatAmount(String? value) => _render(_scaled(value ?? '0', 2), 2);

/// A quantity or a rate - grouped, up to four decimals, trailing zeros dropped.
///
/// Four because that is the `NUMERIC(18,4)` the backend stores quantities in.
/// Trailing zeros go because "12 pcs" reads better than "12.0000 pcs", but a real
/// fractional quantity keeps its digits: half a kilo is 0.5, not 1.
///
/// The web app renders these as `formatMoney(q).replace('₹', '')`, which is right
/// for rupees and leaves a stray `$` for an organization keeping books in dollars.
/// Formatting without a symbol in the first place avoids having to strip one.
String formatQuantity(String? value) {
  final String rendered = _render(_scaled(value ?? '0', 4), 4);
  if (!rendered.contains('.')) return rendered;
  final String trimmed = rendered.replaceFirst(RegExp(r'0+$'), '');
  return trimmed.endsWith('.')
      ? trimmed.substring(0, trimmed.length - 1)
      : trimmed;
}

/// Add API money strings exactly, returning a money string.
///
/// Not `values.fold(0.0, (sum, v) => sum + double.parse(v))`. Scales to integers
/// and adds with `BigInt`, the same way [compareMoney] compares them.
String sumMoney(Iterable<String> values, {int scale = 6}) {
  final BigInt total = values.fold<BigInt>(
    BigInt.zero,
    (BigInt sum, String value) => sum + _scaled(value, scale),
  );

  final bool negative = total.isNegative;
  final String digits = total.abs().toString().padLeft(scale + 1, '0');
  final String whole = digits.substring(0, digits.length - scale);
  final String fraction = digits.substring(digits.length - scale);
  return '${negative ? '-' : ''}$whole.$fraction';
}

/// Compare two API money strings without converting to `double`.
///
/// Returns a negative number, zero, or a positive number, like a comparator.
int compareMoney(String a, String b) => _scaled(a, 6).compareTo(_scaled(b, 6));

/// True when an API money string represents zero, whatever its scale.
bool isZeroMoney(String? value) {
  if (value == null || value.trim().isEmpty) return true;
  return _scaled(value, 6) == BigInt.zero;
}

/// True when an API money string is negative.
bool isNegativeMoney(String? value) =>
    value != null && value.trim().startsWith('-');

/// A money string as a `double`, for chart geometry only.
///
/// Named to be conspicuous at the call site: anything that reaches a pixel may use
/// this, anything that reaches a label must not. Every chart in this app plots with
/// it and formats its tooltip from the original string.
double chartValue(String? value) => double.tryParse(value?.trim() ?? '') ?? 0;

// =============================================================================
// Plain numbers
// =============================================================================
String formatNumber(num value) => _render(_scaled(value.toString(), 0), 0);

/// A signed percentage - `+12.4%`, `-3.0%`.
String formatPercent(double value, {int fractionDigits = 1}) =>
    '${value >= 0 ? '+' : ''}${value.toStringAsFixed(fractionDigits)}%';

/// Abbreviate a large number for a KPI tile or a chart axis: 1.2K, 3.4M, 1.1Cr.
///
/// The scale words follow the grouping convention, because they are the same
/// convention: a business keeping books in rupees reads 12,00,000 as 12 lakh, and
/// labelling that "1.2M" is a translation nobody asked for.
///
/// A `double` here is correct rather than tolerated - this is an axis tick, and it
/// says so by rounding to one decimal place.
String formatCompact(num value) {
  final double magnitude = value.abs().toDouble();
  final String sign = value < 0 ? '-' : '';

  String trim(double scaled) {
    final String text = scaled.toStringAsFixed(scaled.abs() < 10 ? 1 : 0);
    return text.endsWith('.0') ? text.substring(0, text.length - 2) : text;
  }

  if (usesIndianGrouping) {
    if (magnitude >= 10000000) return '$sign${trim(magnitude / 10000000)}Cr';
    if (magnitude >= 100000) return '$sign${trim(magnitude / 100000)}L';
    if (magnitude >= 1000) return '$sign${trim(magnitude / 1000)}K';
  } else {
    if (magnitude >= 1000000000) return '$sign${trim(magnitude / 1000000000)}B';
    if (magnitude >= 1000000) return '$sign${trim(magnitude / 1000000)}M';
    if (magnitude >= 1000) return '$sign${trim(magnitude / 1000)}K';
  }

  return '$sign${trim(magnitude)}';
}

// =============================================================================
// Dates
// =============================================================================
/// Matches `YYYY-MM-DD` with no time component.
final RegExp _dateOnly = RegExp(r'^\d{4}-\d{2}-\d{2}$');

/// Format a date.
///
/// **A date-only value is not converted between timezones, and that is the whole
/// subtlety here.** `"2026-07-30"` is a calendar date - the day an entry was
/// posted - not an instant. Parsing it as midnight UTC and rendering it in a zone
/// behind UTC would show the 29th: every entry dated the 1st of a month would
/// appear to fall in the previous one, and a report filtered by month would
/// disagree with the rows it listed. So a date-only string is read as literal
/// calendar parts and never shifted, which returns the same day to every viewer.
///
/// A full timestamp *is* an instant, and is shown in the organization's zone -
/// because "which day did this happen on" is a question about the organization's
/// clock, not the clock of whoever is looking. That is what the `timezone` package
/// is here for; Dart's own `DateTime` can only offer UTC or the machine's local
/// zone, and neither is the right answer.
String formatDate(String value) {
  if (_dateOnly.hasMatch(value)) {
    final List<String> parts = value.split('-');
    return _mediumDate(
      DateTime(int.parse(parts[0]), int.parse(parts[1]), int.parse(parts[2])),
    );
  }
  return _mediumDate(_inOrganizationZone(value));
}

String formatDateTime(String value) {
  final DateTime local = _inOrganizationZone(value);
  return '${_mediumDate(local)}, ${DateFormat.jm(_intlLocale).format(local)}';
}

/// Relative time ("3 minutes ago").
///
/// Used in audit trails and session lists, where the elapsed interval matters more
/// than the absolute timestamp. Absolute values sit in the tooltip beside them.
String formatRelative(String value) {
  final DateTime instant =
      DateTime.tryParse(value)?.toUtc() ?? DateTime.now().toUtc();
  final int seconds = DateTime.now().toUtc().difference(instant).inSeconds;
  final int magnitude = seconds.abs();
  final bool past = seconds >= 0;

  String phrase(int amount, String unit) {
    final String plural = amount == 1 ? unit : '${unit}s';
    return past ? '$amount $plural ago' : 'in $amount $plural';
  }

  if (magnitude < 45) return past ? 'just now' : 'in a moment';
  if (magnitude < 3600) return phrase(magnitude ~/ 60, 'minute');
  if (magnitude < 86400) return phrase(magnitude ~/ 3600, 'hour');
  if (magnitude < 604800) return phrase(magnitude ~/ 86400, 'day');
  if (magnitude < 2629800) return phrase(magnitude ~/ 604800, 'week');
  if (magnitude < 31557600) return phrase(magnitude ~/ 2629800, 'month');
  return phrase(magnitude ~/ 31557600, 'year');
}

/// Today, in the organization's zone, as `YYYY-MM-DD`.
///
/// Used to seed a date field. The machine's own date would be a day out for
/// anyone working across midnight in a different zone from the books.
String todayIso() {
  final DateTime now = _nowInOrganizationZone();
  return isoDate(now);
}

/// A `DateTime` as `YYYY-MM-DD`, from its calendar parts.
///
/// Local parts, never `toIso8601String()`: that converts to UTC first, so a date
/// built in IST comes back as the previous day for the first five and a half hours.
String isoDate(DateTime date) {
  final String month = date.month.toString().padLeft(2, '0');
  final String day = date.day.toString().padLeft(2, '0');
  return '${date.year}-$month-$day';
}

/// Two-letter initials for an avatar fallback.
String initialsOf(String name, {String fallback = '?'}) {
  final List<String> parts = name
      .trim()
      .split(RegExp(r'\s+'))
      .where((String p) => p.isNotEmpty)
      .toList();
  if (parts.isEmpty) return fallback;
  if (parts.length == 1) {
    return parts.first
        .substring(0, parts.first.length >= 2 ? 2 : 1)
        .toUpperCase();
  }
  return '${parts.first[0]}${parts.last[0]}'.toUpperCase();
}

/// A month's name, from the locale rather than a table to maintain.
String monthName(int month) =>
    DateFormat.MMMM(_intlLocale).format(DateTime(2000, month));

// -----------------------------------------------------------------------------
// Internals
// -----------------------------------------------------------------------------
/// `Intl` wants `en_IN`; the settings hold the same tag.
String get _intlLocale {
  _ensureLocaleData();
  return localeSettings().locale;
}

String _mediumDate(DateTime date) => DateFormat.yMMMd(_intlLocale).format(date);

/// An ISO instant, moved into the organization's zone.
///
/// Falls back to the machine's local zone if the stored IANA name is one the
/// bundled database does not know. That is better than throwing: a settings value
/// this app cannot resolve should degrade to a plausible time, not blank the
/// screen it appears on.
DateTime _inOrganizationZone(String value) {
  final DateTime? parsed = DateTime.tryParse(value);
  if (parsed == null) return DateTime.now();
  return _shift(parsed.toUtc());
}

DateTime _nowInOrganizationZone() => _shift(DateTime.now().toUtc());

DateTime _shift(DateTime utc) {
  try {
    final tz.Location location = tz.getLocation(localeSettings().timeZone);
    final tz.TZDateTime zoned = tz.TZDateTime.from(utc, location);
    // Rebuilt as a plain `DateTime` from the zone's calendar parts, so
    // `DateFormat` prints those parts rather than re-applying an offset.
    return DateTime(
      zoned.year,
      zoned.month,
      zoned.day,
      zoned.hour,
      zoned.minute,
      zoned.second,
    );
  } catch (_) {
    return utc.toLocal();
  }
}
