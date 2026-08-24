/// The organization's currency, timezone, and financial year, in one place.
///
/// Every amount and every date in this app has to be rendered the organization's
/// way, and they are rendered from about a hundred call sites. Threading three
/// arguments through all of them would put the same decision in a hundred places,
/// and the first one anybody forgot would quietly print rupees to a business
/// keeping books in dollars.
///
/// So the formatters read from here instead. It is a library-level holder set once
/// from the session payload, the same shape as the access token in
/// `api_client.dart` - deliberately, since this is the same kind of value:
/// process-wide, arriving after boot, needed everywhere.
///
/// **The defaults are a last resort, not a policy.** They apply only before a
/// session exists - the sign-in screen, and the instant before `/auth/me` returns.
/// Once a user is signed in, every value here comes from their organization row.
library;

class LocaleSettings {
  const LocaleSettings({
    required this.currency,
    required this.locale,
    required this.timeZone,
    required this.fiscalYearStartMonth,
  });

  /// ISO 4217, e.g. `INR`.
  final String currency;

  /// BCP 47 tag, which decides digit grouping - `en-IN` groups as 1,00,000.
  final String locale;

  /// IANA zone, e.g. `Asia/Kolkata`. Decides which calendar day an instant falls
  /// on.
  final String timeZone;

  /// 1-12. April is 4, which is the Indian financial year.
  final int fiscalYearStartMonth;
}

/// Grouping style follows the currency, not the machine.
///
/// A rupee figure reads `1,00,000` and a dollar figure `100,000`, and which is
/// right is a property of the books rather than of the computer looking at them -
/// an accountant in London reviewing Indian accounts should see the Indian
/// grouping. Only currencies whose conventional grouping differs from the Western
/// default need an entry.
const Map<String, String> _localeByCurrency = <String, String>{
  'INR': 'en_IN',
  'LKR': 'en_LK',
  'NPR': 'ne_NP',
  'BDT': 'bn_BD',
};

const LocaleSettings _fallback = LocaleSettings(
  currency: 'INR',
  locale: 'en_IN',
  timeZone: 'Asia/Kolkata',
  fiscalYearStartMonth: 4,
);

LocaleSettings _current = _fallback;

/// The settings in force. Read at format time, never captured at library load.
LocaleSettings localeSettings() => _current;

/// Adopt an organization's settings.
///
/// Called from the auth controller whenever the signed-in organization changes,
/// including on sign-out - where passing null restores the defaults, so the next
/// user does not inherit the last one's currency.
void setLocaleSettings({
  String? currency,
  String? timezone,
  int? fiscalYearStartMonth,
  bool reset = false,
}) {
  if (reset) {
    _current = _fallback;
    return;
  }

  final String resolved = currency ?? _fallback.currency;
  _current = LocaleSettings(
    currency: resolved,
    locale: _localeByCurrency[resolved] ?? 'en_US',
    timeZone: timezone ?? _fallback.timeZone,
    fiscalYearStartMonth:
        fiscalYearStartMonth ?? _fallback.fiscalYearStartMonth,
  );
}

/// True when this currency groups digits the Indian way - 1,00,000 rather than
/// 100,000.
///
/// Derived from the locale tag rather than listed again, so adding a currency to
/// [_localeByCurrency] is the only edit needed.
bool get usesIndianGrouping {
  const Set<String> indian = <String>{'en_IN', 'en_LK', 'ne_NP', 'bn_BD'};
  return indian.contains(_current.locale);
}
