import 'package:flutter_test/flutter_test.dart';
import 'package:personalerp_desktop/core/format.dart';
import 'package:personalerp_desktop/core/locale_settings.dart';

/// The money path, which is the one thing in this app that must not be approximately right.
///
/// The backend serialises `Decimal` as a decimal string precisely so no float is involved;
/// these tests are what prove the client honours that. The float-comparison cases are the
/// point - each one is a value that a `double` round-trip would visibly corrupt.
void main() {
  setUp(() => setLocaleSettings(currency: 'INR', timezone: 'Asia/Kolkata'));

  group('formatMoney', () {
    test('formats a value a double would corrupt', () {
      // 1234567.89 as an IEEE-754 double is 1234567.8899999999.
      expect(formatMoney('1234567.89'), '₹12,34,567.89');
    });

    test('groups the Indian way for a rupee currency', () {
      expect(formatMoney('100000'), '₹1,00,000.00');
      expect(formatMoney('10000000'), '₹1,00,00,000.00');
    });

    test('groups the Western way for a dollar currency', () {
      setLocaleSettings(currency: 'USD');
      expect(formatMoney('100000'), r'$100,000.00');
      expect(formatMoney('1234567.89'), r'$1,234,567.89');
    });

    test('puts the sign outside the symbol', () {
      expect(formatMoney('-500'), '-₹500.00');
    });

    test('treats a null or empty value as zero rather than blank', () {
      expect(formatMoney(null), '₹0.00');
      expect(formatMoney(''), '₹0.00');
    });

    test('rounds half-up at the second decimal', () {
      expect(formatMoney('0.005'), '₹0.01');
      expect(formatMoney('0.004'), '₹0.00');
    });

    test('carries a scale far beyond a double s precision', () {
      // 20 significant digits: a double holds about 15.
      expect(
        formatMoney('99999999999999999.99'),
        '₹99,99,99,99,99,99,99,999.99',
      );
    });
  });

  group('formatAmount', () {
    test('omits the currency symbol for a dense report column', () {
      expect(formatAmount('1234567.89'), '12,34,567.89');
    });
  });

  group('formatQuantity', () {
    test('drops trailing zeros but keeps a real fraction', () {
      expect(formatQuantity('12.0000'), '12');
      expect(formatQuantity('0.5000'), '0.5');
      expect(formatQuantity('0.0125'), '0.0125');
    });

    test('never emits a currency symbol', () {
      setLocaleSettings(currency: 'USD');
      expect(formatQuantity('1500'), '1,500');
    });
  });

  group('sumMoney', () {
    test('adds without float drift', () {
      // 0.1 + 0.2 != 0.3 in binary floating point.
      expect(sumMoney(<String>['0.1', '0.2']), '0.300000');
    });

    test('handles a mixed-sign set', () {
      expect(sumMoney(<String>['1000.00', '-250.50', '-749.50']), '0.000000');
    });

    test('adds values of differing scale', () {
      expect(sumMoney(<String>['1', '1.5', '1.25']), '3.750000');
    });
  });

  group('compareMoney', () {
    test('treats differing scales as equal', () {
      expect(compareMoney('0', '0.0000'), 0);
      expect(compareMoney('1.50', '1.5'), 0);
    });

    test('orders correctly across the sign', () {
      expect(compareMoney('-1', '1'), lessThan(0));
      expect(compareMoney('2', '1.999999'), greaterThan(0));
    });
  });

  group('isZeroMoney', () {
    test('recognises zero at any scale', () {
      expect(isZeroMoney('0'), isTrue);
      expect(isZeroMoney('0.000000'), isTrue);
      expect(isZeroMoney('-0.00'), isTrue);
      expect(isZeroMoney(null), isTrue);
      expect(isZeroMoney('0.01'), isFalse);
    });
  });

  group('formatCompact', () {
    test('uses lakh and crore for a rupee currency', () {
      expect(formatCompact(1200), '1.2K');
      expect(formatCompact(1200000), '12L');
      expect(formatCompact(11000000), '1.1Cr');
    });

    test('uses million and billion for a dollar currency', () {
      setLocaleSettings(currency: 'USD');
      expect(formatCompact(3400000), '3.4M');
      expect(formatCompact(2100000000), '2.1B');
    });
  });

  group('formatDate', () {
    // These four run under the *default* configuration - INR, so locale `en_IN`. That
    // matters more than it looks: `intl` bundles date symbols for `en_US` only, and
    // `DateFormat` throws for anything else until `initializeDateFormatting` has run. A
    // suite that only ever formatted dates under USD would pass while every date in the
    // shipped app threw - which is exactly what happened, and only showed up once a
    // session existed, because the sign-in screen renders no dates.
    test('formats under the default rupee locale, with no set-up', () {
      expect(formatDate('2026-07-30'), '30 Jul 2026');
    });

    test('formats a full timestamp in the organization s zone', () {
      // 20:00Z is 01:30 the next day in Asia/Kolkata - the half-hour offset is why this
      // case is worth pinning rather than one of the whole-hour zones.
      //
      // Matched on parts rather than on one literal. CLDR separates the time from the
      // meridiem with a narrow no-break space, and lowercases `am` for `en_IN` - both
      // correct, and the first is invisible in a source file. Pinning the exact string
      // would mean an unreadable character in the test and a failure the next time CLDR
      // adjusts its spacing, neither of which says anything about this code.
      final String formatted = formatDateTime('2026-07-30T20:00:00Z');
      expect(formatted, startsWith('31 Jul 2026, 1:30'));
      expect(formatted.toLowerCase(), endsWith('am'));
    });

    test('names a month under the default locale', () {
      expect(monthName(4), 'April');
      expect(monthName(12), 'December');
    });

    test('does not shift a date-only value between timezones', () {
      // The bug this guards: parsed as midnight UTC and rendered in a zone behind UTC,
      // 2026-07-01 becomes 30 June - so every entry dated the 1st appears in the previous
      // month.
      setLocaleSettings(currency: 'USD', timezone: 'America/Los_Angeles');
      expect(formatDate('2026-07-01'), contains('1'));
      expect(formatDate('2026-07-01'), contains('Jul'));
      expect(formatDate('2026-07-01'), isNot(contains('Jun')));
    });
  });

  group('initialsOf', () {
    test('takes the first and last initials', () {
      expect(initialsOf('Jhon Doe'), 'JD');
      // Three names still yields two letters: the first and the *last*, not the first two.
      expect(initialsOf('Priya Anand Sharma'), 'PS');
    });

    test('takes two letters from a single name', () {
      expect(initialsOf('Priya'), 'PR');
    });

    test('falls back when there is no name', () {
      expect(initialsOf('   '), '?');
    });
  });
}
