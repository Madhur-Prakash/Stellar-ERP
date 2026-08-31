/**
 * The application footer.
 *
 * An application footer is not a marketing site's footer - nobody arrives here to browse -
 * so every line has to answer a question someone actually has while using the product:
 *
 * * **Do my books still balance?** The only place in the app that says so on *every*
 *   screen. A control that stays invisible until it fails offers no reassurance while it
 *   holds, and this is the one fact an accounting product should never make you go and
 *   look for.
 * * **What period am I in?** Indian financial years run April to March, entries get
 *   backdated, and "which year does this land in" is a question a footer can answer for
 *   free. The month comes from the server, because the organization owns that rule.
 * * **Where else can I go?** The same navigation as the sidebar, from the same list, so
 *   the two cannot disagree - and permission-filtered, so it never offers a screen the
 *   user would be redirected away from.
 * * **Whose data is this?** Self-hosted is the whole premise, and the disclaimer is the
 *   one piece of legal text an accounting product genuinely needs.
 *
 * Deliberately absent: a version number, a support address, and any external link. There
 * is no endpoint that reports a version (`/health` withholds it on purpose) and no support
 * channel to point at, so inventing either would be a footer that lies.
 */
import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { AlertTriangle, Command, ShieldCheck } from 'lucide-react';

import { NAV_SECTIONS } from '@/components/layout/nav';
import { analyticsApi } from '@/features/analytics/api';
import { useAuth } from '@/features/auth/AuthProvider';
import { env } from '@/lib/env';
import { formatDate } from '@/lib/format';
import { localeSettings } from '@/lib/locale';

/**
 * "1 April to 31 March", from the month the year opens in.
 *
 * Derived rather than hardcoded to April: the backend lets an organization start its year
 * in any month, and a footer confidently stating the wrong period is worse than no footer.
 * Month names come from `Intl`, so this needs no table to maintain.
 */
function fiscalYearSpan(startMonth: number): string {
  const monthName = (month: number) =>
    new Intl.DateTimeFormat(localeSettings().locale, { month: 'long' }).format(
      new Date(2000, month - 1, 1),
    );
  // The last month is the one before the start, wrapping December → January.
  const endMonth = startMonth === 1 ? 12 : startMonth - 1;
  // February is 28 days in three years out of four, so naming a day would be wrong in the
  // fourth. Every other month has a fixed length and reads better with one.
  const closing =
    endMonth === 2
      ? `the end of ${monthName(2)}`
      : `${new Date(2001, endMonth, 0).getDate()} ${monthName(endMonth)}`;
  return `1 ${monthName(startMonth)} to ${closing}`;
}

/**
 * The modifier this machine actually uses for the command palette.
 *
 * The shortcut handler accepts `metaKey || ctrlKey`, so both work everywhere - but a hint
 * has to name one, and telling a Mac user to press Ctrl when everything else on their
 * system is ⌘ makes the hint read as untrustworthy.
 */
const SHORTCUT_MODIFIER =
  typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.userAgent) ? '⌘' : 'Ctrl';

export function Footer() {
  const { user, can } = useAuth();
  const organization = user?.active_organization;
  const canSeeMoney = can('report:read');

  const { data: checks } = useQuery({
    queryKey: ['analytics-control-checks'],
    queryFn: () => analyticsApi.controlChecks(),
    enabled: canSeeMoney && Boolean(organization),
    // Shared with the dashboard and the analytics page, so this costs no extra request.
    staleTime: 60_000,
  });

  const { data: periods } = useQuery({
    queryKey: ['analytics-periods'],
    queryFn: () => analyticsApi.periods(),
    enabled: Boolean(organization),
    // The fiscal calendar does not change while someone is looking at it, and the report
    // range controls already hold this in cache.
    staleTime: Number.POSITIVE_INFINITY,
  });

  // Only sections with something the user may actually open. An empty column is worse
  // than a missing one: it reads as a screen that failed to load.
  const columns = NAV_SECTIONS.map((section) => ({
    title: section.title,
    items: section.items.filter(
      (item) => !item.stage && (!item.permission || can(item.permission)),
    ),
  })).filter((section) => section.items.length > 0);

  return (
    <footer className="border-border text-content-muted mt-2 border-t px-4 pt-7 pb-6 text-[12px] sm:px-6 lg:px-8">
      <div className="flex flex-col gap-8 lg:flex-row lg:justify-between">
        {/* Identity and premise. */}
        <div className="max-w-xs">
          <p className="text-content-secondary text-[13px] font-semibold">{env.appName}</p>
          <p className="mt-1 leading-relaxed">
            Self-hosted double-entry accounting. Your data stays in your own database, on your own
            machine.
          </p>
          {organization && (
            <p className="text-content-secondary mt-2.5">
              {organization.name}
              <span className="text-content-muted"> · you are {organization.role_name}</span>
            </p>
          )}
        </div>

        {/* Navigation, from the sidebar's own list. */}
        <nav aria-label="Footer" className="flex flex-wrap gap-x-8 gap-y-6 sm:gap-x-12">
          {columns.map((section) => (
            <div key={section.title}>
              <p className="text-content-secondary mb-2 text-[11px] font-semibold tracking-wide uppercase">
                {section.title}
              </p>
              <ul className="space-y-1.5">
                {section.items.map((item) => (
                  <li key={item.to}>
                    <Link to={item.to} className="hover:text-content transition-colors">
                      {item.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}

          {/* Facts about the books, which is what makes this a footer for an accounting
              product rather than a list of links. */}
          <div className="max-w-60">
            <p className="text-content-secondary mb-2 text-[11px] font-semibold tracking-wide uppercase">
              Your books
            </p>
            <ul className="space-y-1.5">
              {checks && (
                <li>
                  {checks.all_agree ? (
                    <span className="flex items-center gap-1.5">
                      <ShieldCheck className="text-success h-3.5 w-3.5 shrink-0" aria-hidden />
                      Ledger and records agree
                    </span>
                  ) : (
                    // Linked, not merely flagged: the point of surfacing a discrepancy on
                    // every screen is that it is one click from the page explaining it.
                    <Link
                      to="/analytics"
                      className="text-danger flex items-center gap-1.5 font-medium hover:underline"
                    >
                      <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />
                      They do not agree - review
                    </Link>
                  )}
                </li>
              )}
              {periods && (
                <>
                  <li>Financial year: {fiscalYearSpan(periods.fiscal_year_start_month)}</li>
                  <li>
                    Books dated {formatDate(periods.today)}
                    <span className="text-content-muted"> in your own timezone</span>
                  </li>
                </>
              )}
              <li>Amounts kept as exact decimals, never rounded in transit</li>
            </ul>
          </div>
        </nav>
      </div>

      <div className="border-border/60 mt-7 flex flex-col gap-3 border-t pt-5 sm:flex-row sm:items-start sm:justify-between">
        <p className="max-w-3xl leading-relaxed">
          This software is self-hosted ERP and accounting platform
        </p>

        {/* A keyboard shortcut is not an affordance on a touch device, and the line
            reads as an instruction the reader cannot follow. */}
        <p className="hidden shrink-0 items-center gap-1.5 whitespace-nowrap sm:flex">
          <Command className="h-3.5 w-3.5" aria-hidden />
          Press
          <kbd className="border-border bg-surface-sunken text-content-secondary rounded border px-1.5 py-0.5 font-sans text-[11px]">
            {SHORTCUT_MODIFIER} K
          </kbd>
          to jump anywhere
        </p>
      </div>
    </footer>
  );
}
