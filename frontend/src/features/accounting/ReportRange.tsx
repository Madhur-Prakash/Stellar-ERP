/**
 * Date-range control for the financial statements.
 *
 * Presets for the windows people actually ask for, plus two date inputs for anything
 * else - an accountant reconciling one week, or a landlord checking a single day.
 *
 * **The fiscal-year start comes from the server.** The previous version hardcoded
 * `today.getMonth() >= 3` for April, which is right for India and wrong for an
 * organization set to a January year - and it duplicated a rule the backend already
 * owns, so the two could disagree with nothing to catch it. `/analytics/periods`
 * reports the organization's own start month and its own idea of today, in its own
 * timezone.
 */
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { Input } from '@/components/ui/Input';
import { analyticsApi } from '@/features/analytics/api';
import { cn } from '@/lib/cn';
import { localeSettings } from '@/lib/locale';

export interface DateRange {
  from_date: string;
  to_date: string;
}

type PresetKey =
  'this_month' | 'last_month' | 'quarter' | 'year_to_date' | 'fiscal_year' | 'previous_fiscal_year';

function iso(date: Date): string {
  // Local parts, not `toISOString()`: that converts to UTC first, so a date built in
  // IST comes back as the previous day for the first five and a half hours.
  const month = `${date.getMonth() + 1}`.padStart(2, '0');
  const day = `${date.getDate()}`.padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
}

function resolve(preset: PresetKey, today: Date, fiscalStartMonth: number): DateRange {
  const year = today.getFullYear();
  const month = today.getMonth();

  // The fiscal year containing today. `fiscalStartMonth` is 1-based from the server.
  const startIndex = fiscalStartMonth - 1;
  const fiscalYear = month >= startIndex ? year : year - 1;

  switch (preset) {
    case 'this_month':
      return { from_date: iso(new Date(year, month, 1)), to_date: iso(today) };
    case 'last_month':
      return {
        from_date: iso(new Date(year, month - 1, 1)),
        // Day 0 of this month is the last day of the previous one, which sidesteps
        // month lengths and leap years entirely.
        to_date: iso(new Date(year, month, 0)),
      };
    case 'quarter': {
      const monthsIn = (year - fiscalYear) * 12 + (month - startIndex);
      const quarterStart = startIndex + Math.floor(monthsIn / 3) * 3;
      return { from_date: iso(new Date(fiscalYear, quarterStart, 1)), to_date: iso(today) };
    }
    case 'year_to_date':
      // The financial year so far. Ends today, because the rest has not happened.
      return { from_date: iso(new Date(fiscalYear, startIndex, 1)), to_date: iso(today) };
    case 'fiscal_year':
      // **The whole financial year**, which in India is 1 April to 31 March of the
      // following year. Previously this label showed year-to-date, which is a different
      // figure - and on 29 July it read "1 Apr to 29 Jul" under a heading that claims to
      // be the financial year. Day 0 of the start month gives the last day of the month
      // before it, so the end is 31 March without hardcoding the length.
      return {
        from_date: iso(new Date(fiscalYear, startIndex, 1)),
        to_date: iso(new Date(fiscalYear + 1, startIndex, 0)),
      };
    case 'previous_fiscal_year':
      return {
        from_date: iso(new Date(fiscalYear - 1, startIndex, 1)),
        to_date: iso(new Date(fiscalYear, startIndex, 0)),
      };
  }
}

/**
 * ``FY 2026-27`` - how an Indian financial year is actually written and spoken.
 *
 * A year that spans two calendar years cannot be labelled with one of them without
 * being ambiguous, which is exactly what made the old "Financial year" button unclear.
 */
function fiscalLabel(startYear: number, startMonth: number): string {
  if (startMonth === 1) return `FY ${startYear}`;
  return `FY ${startYear}-${`${(startYear + 1) % 100}`.padStart(2, '0')}`;
}

export function useReportRange(): {
  range: DateRange;
  control: React.ReactNode;
} {
  const { data: periods } = useQuery({
    queryKey: ['analytics-periods'],
    staleTime: Number.POSITIVE_INFINITY,
    queryFn: () => analyticsApi.periods(),
  });

  // Falls back to the session's own value rather than a hardcoded April, so the presets
  // match the organization's year even in the instant before `/analytics/periods` lands.
  const fiscalStartMonth =
    periods?.fiscal_year_start_month ?? localeSettings().fiscalYearStartMonth;
  // The server's today, in the organization's timezone - not the browser's.
  const today = periods?.today ? new Date(`${periods.today}T00:00:00`) : new Date();

  // Year-to-date is the default: it is the figure someone checking on the business
  // wants, and the full year would sit mostly in the future.
  const [preset, setPreset] = useState<PresetKey | 'custom'>('year_to_date');
  const [custom, setCustom] = useState<DateRange>(() => resolve('year_to_date', today, 4));

  const startIndex = fiscalStartMonth - 1;
  const currentFiscalYear =
    today.getMonth() >= startIndex ? today.getFullYear() : today.getFullYear() - 1;

  const presets: { key: PresetKey; label: string; title?: string }[] = [
    { key: 'this_month', label: 'This month' },
    { key: 'last_month', label: 'Last month' },
    { key: 'quarter', label: 'Quarter' },
    { key: 'year_to_date', label: 'Year to date', title: 'This financial year so far' },
    {
      key: 'fiscal_year',
      label: fiscalLabel(currentFiscalYear, fiscalStartMonth),
      title: 'The whole financial year',
    },
    {
      key: 'previous_fiscal_year',
      label: fiscalLabel(currentFiscalYear - 1, fiscalStartMonth),
      title: 'The previous financial year',
    },
  ];

  const range = preset === 'custom' ? custom : resolve(preset, today, fiscalStartMonth);
  const invalid = range.to_date < range.from_date;

  const control = (
    // Full width on a phone, hugging the right on a desktop. Seven presets is about
    // 600 pixels of segmented control - more than a phone has - so below `sm` the
    // group scrolls sideways *within the card* rather than widening the page.
    <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row sm:flex-wrap sm:items-end sm:justify-end">
      <div className="border-border flex max-w-full overflow-x-auto overflow-y-hidden rounded-lg border">
        {presets.map((option) => (
          <button
            key={option.key}
            type="button"
            title={option.title}
            aria-pressed={preset === option.key}
            onClick={() => setPreset(option.key)}
            className={cn(
              'shrink-0 px-2.5 py-1.5 text-[12px] font-medium whitespace-nowrap',
              preset === option.key
                ? 'bg-primary text-white'
                : 'text-content-muted hover:bg-surface-sunken',
            )}
          >
            {option.label}
          </button>
        ))}
        <button
          type="button"
          aria-pressed={preset === 'custom'}
          onClick={() => {
            // Seed the custom fields from whatever is on screen, so switching to
            // "Custom" does not blank the report.
            setCustom(range);
            setPreset('custom');
          }}
          className={cn(
            'shrink-0 px-2.5 py-1.5 text-[12px] font-medium',
            preset === 'custom'
              ? 'bg-primary text-white'
              : 'text-content-muted hover:bg-surface-sunken',
          )}
        >
          Custom
        </button>
      </div>

      {preset === 'custom' && (
        <div className="grid grid-cols-2 items-end gap-2 sm:flex">
          <Input
            type="date"
            label="From"
            value={custom.from_date}
            max={custom.to_date}
            onChange={(event) => setCustom({ ...custom, from_date: event.target.value })}
            className="w-full sm:w-40"
          />
          <Input
            type="date"
            label="To"
            value={custom.to_date}
            min={custom.from_date}
            onChange={(event) => setCustom({ ...custom, to_date: event.target.value })}
            error={invalid ? 'Must be on or after the start date' : undefined}
            className="w-full sm:w-40"
          />
        </div>
      )}
    </div>
  );

  // A reversed range would be rejected by the server anyway; holding the last valid one
  // keeps the report on screen while the user is mid-edit rather than flashing an error.
  return { range: invalid ? { from_date: range.to_date, to_date: range.to_date } : range, control };
}
