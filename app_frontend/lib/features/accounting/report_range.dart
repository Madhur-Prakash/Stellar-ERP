import 'package:flutter/material.dart';

import '../../core/format.dart';
import '../../models/accounting.dart';
import '../../widgets/app_input.dart';
import '../../widgets/primitives.dart';

/// Date-range control for the financial statements.
///
/// Presets for the windows people actually ask for, plus two date fields for anything else
/// - an accountant reconciling one week, or a landlord checking a single day.
///
/// **The fiscal-year start comes from the server.** Hardcoding April is right for India
/// and wrong for an organization set to a January year - and it would duplicate a rule the
/// backend already owns, so the two could disagree with nothing to catch it.
/// `/analytics/periods` reports the organization's own start month and its own idea of
/// today, in its own timezone.
enum RangePreset {
  thisMonth,
  lastMonth,
  quarter,
  yearToDate,
  fiscalYear,
  previousFiscalYear,
  custom,
}

/// Resolve a preset against a reference date and the organization's fiscal-year start.
DateRange resolveRange(
  RangePreset preset,
  DateTime today,
  int fiscalStartMonth,
) {
  final int year = today.year;
  final int month = today.month;

  // The fiscal year containing today. `fiscalStartMonth` is 1-based from the server.
  final int fiscalYear = month >= fiscalStartMonth ? year : year - 1;

  switch (preset) {
    case RangePreset.thisMonth:
      return DateRange(
        fromDate: isoDate(DateTime(year, month, 1)),
        toDate: isoDate(today),
      );
    case RangePreset.lastMonth:
      return DateRange(
        fromDate: isoDate(DateTime(year, month - 1, 1)),
        // Day 0 of this month is the last day of the previous one, which sidesteps month
        // lengths and leap years entirely.
        toDate: isoDate(DateTime(year, month, 0)),
      );
    case RangePreset.quarter:
      final int monthsIn =
          (year - fiscalYear) * 12 + (month - fiscalStartMonth);
      final int quarterStart = fiscalStartMonth + (monthsIn ~/ 3) * 3;
      return DateRange(
        fromDate: isoDate(DateTime(fiscalYear, quarterStart, 1)),
        toDate: isoDate(today),
      );
    case RangePreset.yearToDate:
      // The financial year so far. Ends today, because the rest has not happened.
      return DateRange(
        fromDate: isoDate(DateTime(fiscalYear, fiscalStartMonth, 1)),
        toDate: isoDate(today),
      );
    case RangePreset.fiscalYear:
      // **The whole financial year**, which in India is 1 April to 31 March of the
      // following year. Labelling year-to-date as "the financial year" is a different
      // figure: on 29 July it reads "1 Apr to 29 Jul" under a heading that claims to be
      // the year. Day 0 of the start month gives the last day of the month before it, so
      // the end is 31 March without hardcoding a length.
      return DateRange(
        fromDate: isoDate(DateTime(fiscalYear, fiscalStartMonth, 1)),
        toDate: isoDate(DateTime(fiscalYear + 1, fiscalStartMonth, 0)),
      );
    case RangePreset.previousFiscalYear:
      return DateRange(
        fromDate: isoDate(DateTime(fiscalYear - 1, fiscalStartMonth, 1)),
        toDate: isoDate(DateTime(fiscalYear, fiscalStartMonth, 0)),
      );
    case RangePreset.custom:
      // Resolved by the caller, which holds the two typed dates.
      return DateRange(fromDate: isoDate(today), toDate: isoDate(today));
  }
}

/// `FY 2026-27` - how an Indian financial year is actually written and spoken.
///
/// A year that spans two calendar years cannot be labelled with one of them without being
/// ambiguous, which is exactly what makes a bare "Financial year" button unclear.
String fiscalLabel(int startYear, int startMonth) {
  if (startMonth == 1) return 'FY $startYear';
  final String next = ((startYear + 1) % 100).toString().padLeft(2, '0');
  return 'FY $startYear-$next';
}

/// The preset buttons, plus the two date fields when Custom is active.
class ReportRangeSelector extends StatelessWidget {
  const ReportRangeSelector({
    super.key,
    required this.preset,
    required this.custom,
    required this.today,
    required this.fiscalStartMonth,
    required this.onPresetChanged,
    required this.onCustomChanged,
  });

  final RangePreset preset;
  final DateRange custom;
  final DateTime today;
  final int fiscalStartMonth;
  final ValueChanged<RangePreset> onPresetChanged;
  final ValueChanged<DateRange> onCustomChanged;

  @override
  Widget build(BuildContext context) {
    final int currentFiscalYear = today.month >= fiscalStartMonth
        ? today.year
        : today.year - 1;
    final bool invalid = custom.toDate.compareTo(custom.fromDate) < 0;

    return Wrap(
      spacing: 8,
      runSpacing: 8,
      crossAxisAlignment: WrapCrossAlignment.end,
      alignment: WrapAlignment.end,
      children: <Widget>[
        Segmented(
          active: preset.name,
          onChanged: (String next) => onPresetChanged(
            RangePreset.values.firstWhere((RangePreset p) => p.name == next),
          ),
          segments: <(String, String, String?)>[
            ('thisMonth', 'This month', null),
            ('lastMonth', 'Last month', null),
            ('quarter', 'Quarter', null),
            ('yearToDate', 'Year to date', 'This financial year so far'),
            (
              'fiscalYear',
              fiscalLabel(currentFiscalYear, fiscalStartMonth),
              'The whole financial year',
            ),
            (
              'previousFiscalYear',
              fiscalLabel(currentFiscalYear - 1, fiscalStartMonth),
              'The previous financial year',
            ),
            ('custom', 'Custom', null),
          ],
        ),
        if (preset == RangePreset.custom)
          Row(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.end,
            spacing: 8,
            children: <Widget>[
              AppDateInput(
                label: 'From',
                value: custom.fromDate,
                maximum: custom.toDate,
                width: 160,
                onChanged: (String next) => onCustomChanged(
                  DateRange(fromDate: next, toDate: custom.toDate),
                ),
              ),
              AppDateInput(
                label: 'To',
                value: custom.toDate,
                minimum: custom.fromDate,
                width: 160,
                error: invalid ? 'Must be on or after the start date' : null,
                onChanged: (String next) => onCustomChanged(
                  DateRange(fromDate: custom.fromDate, toDate: next),
                ),
              ),
            ],
          ),
      ],
    );
  }
}
