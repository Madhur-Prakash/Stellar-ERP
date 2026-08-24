import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { Link } from '@tanstack/react-router';
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  Boxes,
  Building2,
  FileText,
  Landmark,
  Plus,
  Receipt,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  Users,
  Wallet,
} from 'lucide-react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { PageHeader } from '@/components/layout/AppShell';
import { Badge } from '@/components/ui/Badge';
import { Button, buttonClasses } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { InfoTip } from '@/components/ui/InfoTip';
import { Skeleton } from '@/components/ui/Skeleton';
import { type Movement, type Period, type Trend, analyticsApi } from '@/features/analytics/api';
import { useAuth } from '@/features/auth/AuthProvider';
import { organizationsApi } from '@/features/organizations/api';
import { cn } from '@/lib/cn';
import { formatCompact, formatDate, formatMoney, formatRelative, isZeroMoney } from '@/lib/format';
import { localeSettings } from '@/lib/locale';

/**
 * The dashboard.
 *
 * **Every financial figure here is real.** It was not always: until the ledger
 * existed these tiles showed illustrative numbers labelled "Sample", because an
 * unlabelled fake figure in an accounting product is the most damaging thing a page
 * can do. They now come from `/analytics/dashboard`, which is computed by the same
 * `ReportingService` that renders the P&L - so a tile cannot disagree with the
 * statement behind it.
 *
 * Two presentation rules follow, and both are about not overclaiming:
 *
 * - **A percentage change with no basis is not shown as a number.** Going from ₹0 to
 *   ₹50,000 is not "+100%". The server sends `null` and the tile says "no prior
 *   data".
 * - **The comparison window is stated.** "Up 12%" is unverifiable without it, and for
 *   a month-to-date figure the server deliberately compares against the same number
 *   of days in the previous month rather than the whole of it.
 */

const CHART_PERIOD: Period = 'last_12_months';

export function DashboardPage() {
  const { user, can } = useAuth();
  const canSeeMoney = can('report:read');
  const hasOrg = Boolean(user?.active_organization);

  const { data: organizations, isLoading: orgsLoading } = useQuery({
    queryKey: ['organizations'],
    queryFn: organizationsApi.list,
  });

  const { data: members } = useQuery({
    queryKey: ['members'],
    queryFn: organizationsApi.listMembers,
    enabled: can('member:read') && hasOrg,
  });

  const { data: auditPage } = useQuery({
    queryKey: ['audit', { limit: 6 }],
    queryFn: () => organizationsApi.listAudit({ limit: 6 }),
    enabled: can('audit:read') && hasOrg,
  });

  const { data: dashboard } = useQuery({
    queryKey: ['analytics-dashboard', 'this_month'],
    queryFn: () => analyticsApi.dashboard('this_month'),
    enabled: canSeeMoney && hasOrg,
  });

  const { data: trend } = useQuery({
    queryKey: ['analytics-trend', CHART_PERIOD],
    queryFn: () => analyticsApi.trend(CHART_PERIOD),
    enabled: canSeeMoney && hasOrg,
  });

  const { data: checks } = useQuery({
    queryKey: ['analytics-control-checks'],
    queryFn: () => analyticsApi.controlChecks(),
    enabled: canSeeMoney && hasOrg,
  });

  const firstName = user?.full_name.split(' ')[0] ?? 'there';

  // No organization yet: onboarding, not a dashboard.
  if (!user?.active_organization) {
    return (
      <div>
        <PageHeader title={`Welcome, ${firstName}`} />
        <Card>
          <EmptyState
            icon={Building2}
            title="Create your organization"
            description="An organization holds your books, your team, and your data. Create one to get started, or ask a colleague to invite you to theirs."
            action={
              <Link
                to="/settings"
                hash="create-organization"
                className={buttonClasses('primary', 'md')}
              >
                <Plus className="mr-2 h-4 w-4" aria-hidden />
                Create organization
              </Link>
            }
          />
        </Card>
      </div>
    );
  }

  const currency = dashboard?.currency ?? localeSettings().currency;

  return (
    <div>
      <PageHeader
        title={`Good ${greeting()}, ${firstName}`}
        description={
          dashboard
            ? `${dashboard.period_label} at ${user.active_organization.name} - ${formatDate(dashboard.span.start)} to ${formatDate(dashboard.span.end)}.`
            : `Here is what is happening at ${user.active_organization.name}.`
        }
        action={
          <div className="flex items-center gap-2">
            {/* The primary action on the home screen, because recording money is the
                thing people open this software to do. */}
            {can('journal:write') && (
              <Link to="/billing" className={buttonClasses('primary', 'md')}>
                <Plus className="mr-1.5 h-4 w-4" aria-hidden />
                Record money
              </Link>
            )}
            <Button variant="secondary" leftIcon={<Sparkles className="h-4 w-4" />} disabled>
              Ask AI
            </Button>
          </div>
        }
      />

      {/* A control account that disagrees with its documents is the one problem on
          this page worth interrupting for: every figure below is derived from the
          ledger, so if the ledger has drifted, they are all suspect. */}
      {checks && !checks.all_agree && (
        <Card className="border-danger/30 bg-danger-bg mb-4">
          <CardBody className="flex gap-3 pt-5 text-[13px]">
            <AlertTriangle className="text-danger h-4 w-4 shrink-0" aria-hidden />
            <div className="min-w-0">
              <p className="text-content font-medium">
                The ledger does not agree with your documents
              </p>
              <ul className="text-content-secondary mt-1 space-y-0.5">
                {checks.checks
                  .filter((check) => !check.agrees)
                  .map((check) => (
                    <li key={check.name}>
                      <strong>{check.name}</strong>: ledger {formatMoney(check.ledger, currency)},
                      documents {formatMoney(check.subledger, currency)} - a difference of{' '}
                      {formatMoney(check.difference, currency)}
                    </li>
                  ))}
              </ul>
              <p className="text-content-muted mt-1">
                Something was recorded in one place and not the other. The figures below are derived
                from the ledger, so treat them as unconfirmed until this is resolved.
              </p>
            </div>
          </CardBody>
        </Card>
      )}

      {/* Day one is a wall of zeroes. Without a next step that reads as "this
          software is broken" rather than "you have not entered anything yet". */}
      {canSeeMoney &&
        dashboard &&
        isZeroMoney(dashboard.revenue.current) &&
        isZeroMoney(dashboard.expenses.current) &&
        dashboard.invoices_issued === 0 && (
          <Card className="border-primary/25 bg-primary/5 mb-4">
            <CardBody className="flex flex-wrap items-center justify-between gap-3 pt-5">
              <div>
                <p className="text-content text-[14px] font-medium">
                  Nothing recorded for {dashboard.period_label.toLowerCase()} yet
                </p>
                <p className="text-content-muted mt-0.5 text-[13px]">
                  Record what you have received and spent, and these figures fill in.
                </p>
              </div>
              {can('journal:write') && (
                <Link to="/billing" className={buttonClasses('primary', 'md')}>
                  <Plus className="mr-1.5 h-4 w-4" aria-hidden />
                  Record money in or out
                </Link>
              )}
            </CardBody>
          </Card>
        )}

      {/* ---- Performance ---- */}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {canSeeMoney ? (
          <>
            <MovementCard
              label="Revenue"
              movement={dashboard?.revenue}
              currency={currency}
              icon={TrendingUp}
              info={
                <>
                  <p>
                    Everything you earned this period - money recorded as coming in, plus any
                    invoices you posted.
                  </p>
                  <p>
                    <strong>GST is excluded.</strong> Tax you collect belongs to the government, so
                    counting it as revenue would flatter the business.
                  </p>
                </>
              }
            />
            <MovementCard
              label="Expenses"
              movement={dashboard?.expenses}
              currency={currency}
              icon={Wallet}
              risingIsGood={false}
              info={
                <p>
                  Everything you spent this period. Includes household categories if you use them,
                  which is why this can look higher than a purely business figure.
                </p>
              }
            />
            <MovementCard
              label="Net profit"
              movement={dashboard?.net_profit}
              currency={currency}
              icon={TrendingUp}
              info={
                <>
                  <p>Revenue less expenses. Negative means you spent more than you earned.</p>
                  <p>
                    This is not the same as cash: an unpaid invoice counts as revenue before the
                    money arrives.
                  </p>
                </>
              }
            />
            <StatCard
              label="Cash and bank"
              value={dashboard ? formatMoney(dashboard.cash, currency) : undefined}
              icon={Landmark}
              hint={dashboard ? `as at ${formatDate(dashboard.span.end)}` : undefined}
              info={
                <>
                  <p>
                    What you actually hold across every cash and bank account, right now - not for
                    the period.
                  </p>
                  <p>
                    <strong>A negative figure means an entry is wrong</strong>, since you cannot pay
                    out cash you never had. Usually a payment recorded against the wrong account.
                  </p>
                </>
              }
            />
          </>
        ) : (
          <Card className="p-4 sm:col-span-2 xl:col-span-4">
            <p className="text-content-muted text-[13px]">
              You do not have permission to view financial reports.
            </p>
          </Card>
        )}
      </div>

      {/* ---- Position ---- */}
      {canSeeMoney && (
        <div className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard
            label="Owed to you"
            info={
              <>
                <p>
                  Money customers still owe on <strong>invoices you posted</strong> and they have
                  not fully paid.
                </p>
                <p>
                  It stays ₹0 if you only use the Billing screen - recording money in means the cash
                  already arrived, so nobody owes you anything. This fills up only when you raise an
                  invoice under Sales and wait to be paid.
                </p>
              </>
            }
            value={dashboard ? formatMoney(dashboard.receivables, currency) : undefined}
            icon={Receipt}
            hint={
              dashboard && !isZeroMoney(dashboard.overdue_receivables)
                ? `${formatMoney(dashboard.overdue_receivables, currency)} overdue`
                : undefined
            }
            hintTone={
              dashboard && !isZeroMoney(dashboard.overdue_receivables) ? 'danger' : undefined
            }
          />
          <StatCard
            label="You owe"
            info={
              <>
                <p>
                  Money you still owe on <strong>supplier bills you entered</strong> and have not
                  paid yet.
                </p>
                <p>
                  Also ₹0 while you only use Billing: recording money out means you have already
                  paid, so there is no debt left to track. Entering a bill under Inventory without
                  paying it is what fills this in.
                </p>
              </>
            }
            value={dashboard ? formatMoney(dashboard.payables, currency) : undefined}
            icon={FileText}
            hint={
              dashboard && !isZeroMoney(dashboard.overdue_payables)
                ? `${formatMoney(dashboard.overdue_payables, currency)} overdue`
                : undefined
            }
            hintTone={dashboard && !isZeroMoney(dashboard.overdue_payables) ? 'danger' : undefined}
          />
          <StatCard
            label="Stock value"
            info={
              <p>
                What your unsold stock cost you, valued at weighted average. Only fills in if you
                track products under Inventory.
              </p>
            }
            value={dashboard ? formatMoney(dashboard.inventory_value, currency) : undefined}
            icon={Boxes}
          />
          <StatCard
            label="Team members"
            value={members ? String(members.length) : undefined}
            icon={Users}
            hint={
              members ? `${members.filter((m) => m.status === 'active').length} active` : undefined
            }
          />
        </div>
      )}

      {/* ---- Chart + activity ---- */}
      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader
            title="Revenue and expenses"
            description="Last twelve months, from posted ledger entries"
            action={
              trend ? (
                <span className="text-content-muted text-[12px] tabular-nums">
                  {formatMoney(trend.total_profit, currency)} profit
                </span>
              ) : undefined
            }
          />
          <CardBody>
            {!canSeeMoney ? (
              <p className="text-content-muted py-16 text-center text-[13px]">
                You do not have permission to view financial reports.
              </p>
            ) : trend === undefined ? (
              <Skeleton className="h-[280px] w-full" />
            ) : (
              <TrendChart trend={trend} currency={currency} />
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Recent activity"
            description="From the audit trail"
            action={
              can('audit:read') ? (
                <Link to="/audit" className="text-primary text-[13px] hover:underline">
                  View all
                </Link>
              ) : undefined
            }
          />
          <CardBody>
            {!can('audit:read') ? (
              <p className="text-content-muted py-8 text-center text-[13px]">
                You do not have permission to view the audit trail.
              </p>
            ) : auditPage === undefined ? (
              <div className="space-y-3">
                {Array.from({ length: 5 }).map((_, index) => (
                  <div key={index} className="flex items-start gap-3">
                    <Skeleton className="h-7 w-7 rounded-full" />
                    <div className="flex-1 space-y-1.5">
                      <Skeleton className="h-3 w-full" />
                      <Skeleton className="h-2.5 w-20" />
                    </div>
                  </div>
                ))}
              </div>
            ) : auditPage.items.length === 0 ? (
              <EmptyState
                icon={FileText}
                title="Nothing yet"
                description="Actions across your organization will appear here."
                className="py-8"
              />
            ) : (
              <ul className="space-y-3.5">
                {auditPage.items.map((entry) => (
                  <li key={entry.id} className="flex items-start gap-3">
                    <span
                      className={cn(
                        'mt-1 h-1.5 w-1.5 shrink-0 rounded-full',
                        entry.severity === 'critical'
                          ? 'bg-danger'
                          : entry.severity === 'warning'
                            ? 'bg-warning'
                            : 'bg-success',
                      )}
                      aria-hidden
                    />
                    <div className="min-w-0 flex-1">
                      <p className="text-content text-[13px] leading-snug">
                        {entry.summary ?? entry.action}
                      </p>
                      <p className="text-content-muted mt-0.5 text-[11px]">
                        {entry.actor.name ?? entry.actor.email ?? 'System'} ·{' '}
                        {formatRelative(entry.created_at)}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      </div>

      {/* Reconciliation is stated even when it passes: a control that is only visible
          when it fails gives no confidence when it does not. */}
      {canSeeMoney && checks?.all_agree && (
        <p className="text-content-muted mt-4 flex items-center gap-1.5 text-[12px]">
          <ShieldCheck className="text-success h-3.5 w-3.5" aria-hidden />
          Receivables, payables, and stock all reconcile to the ledger as at{' '}
          {formatDate(checks.as_of)}.
        </p>
      )}

      {/* ---- Organizations ---- */}
      {organizations && organizations.length > 1 && (
        <Card className="mt-4">
          <CardHeader title="Your organizations" description="Switch with ⌘K" />
          <CardBody>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {organizations.map((organization) => (
                <div
                  key={organization.id}
                  className="border-border bg-surface-sunken/40 flex items-center gap-3 rounded-lg border p-3"
                >
                  <span
                    className="bg-primary/12 text-primary flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[11px] font-bold"
                    aria-hidden
                  >
                    {organization.name.slice(0, 2).toUpperCase()}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-content truncate text-[13px] font-medium">
                      {organization.name}
                    </p>
                    <p className="text-content-muted text-[11px]">
                      {organization.role_name} · {organization.member_count} member
                      {organization.member_count === 1 ? '' : 's'}
                    </p>
                  </div>
                  {organization.id === user.active_organization?.id && (
                    <Badge tone="primary">Current</Badge>
                  )}
                </div>
              ))}
            </div>
          </CardBody>
        </Card>
      )}

      {orgsLoading && <Skeleton className="mt-4 h-28 rounded-xl" />}
    </div>
  );
}

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return 'morning';
  if (hour < 17) return 'afternoon';
  return 'evening';
}

/**
 * The chart.
 *
 * **Recharts needs numbers, so `Number()` appears here - and only here.** A pixel
 * position does not need exact decimal arithmetic; a figure a user reads does. So the
 * plotted value is converted and the original decimal string is carried on the same
 * datum, with the tooltip formatting from the string. The geometry is approximate,
 * every number on screen is exact.
 */
function TrendChart({ trend, currency }: { trend: Trend; currency: string }) {
  const data = trend.points.map((point) => ({
    label: point.label,
    revenue: Number(point.income),
    expenses: Number(point.expenses),
    revenueText: point.income,
    expensesText: point.expenses,
  }));

  if (data.every((point) => point.revenue === 0 && point.expenses === 0)) {
    return (
      <EmptyState
        icon={TrendingUp}
        title="Nothing posted yet"
        description="Once you post an invoice or a bill, twelve months of revenue and expenses appear here."
        className="py-16"
      />
    );
  }

  return (
    <div className="h-[280px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 4, right: 4, left: -18, bottom: 0 }}>
          <defs>
            <linearGradient id="revenueFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--primary)" stopOpacity={0.28} />
              <stop offset="100%" stopColor="var(--primary)" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="expenseFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--warning)" stopOpacity={0.2} />
              <stop offset="100%" stopColor="var(--warning)" stopOpacity={0} />
            </linearGradient>
          </defs>

          {/* Horizontal rules only: vertical grid lines add clutter without helping
              anyone read a value off a time axis. */}
          <CartesianGrid stroke="var(--border)" vertical={false} />
          <XAxis
            dataKey="label"
            stroke="var(--content-muted)"
            fontSize={11}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            stroke="var(--content-muted)"
            fontSize={11}
            tickLine={false}
            axisLine={false}
            tickFormatter={(value: number) => formatCompact(value)}
          />
          <Tooltip
            contentStyle={{
              background: 'var(--surface-raised)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-lg)',
              fontSize: 12,
              boxShadow: 'var(--shadow-lg)',
            }}
            labelStyle={{ color: 'var(--content)', fontWeight: 600 }}
            formatter={(_value, name, item) => {
              // The exact decimal string off the datum, not the float that was
              // plotted with.
              const datum = item?.payload as (typeof data)[number] | undefined;
              const exact = name === 'revenue' ? datum?.revenueText : datum?.expensesText;
              return [
                formatMoney(exact ?? '0', currency),
                name === 'revenue' ? 'Revenue' : 'Expenses',
              ];
            }}
          />
          <Area
            type="monotone"
            dataKey="revenue"
            stroke="var(--primary)"
            strokeWidth={2}
            fill="url(#revenueFill)"
          />
          <Area
            type="monotone"
            dataKey="expenses"
            stroke="var(--warning)"
            strokeWidth={2}
            fill="url(#expenseFill)"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/** A figure with its period-on-period change. */
function MovementCard({
  label,
  movement,
  currency,
  icon,
  risingIsGood = true,
  info,
}: {
  label: string;
  movement: Movement | undefined;
  currency: string;
  icon: typeof TrendingUp;
  /** Whether an increase is good news. Expenses going up is not. */
  risingIsGood?: boolean;
  info?: ReactNode;
}) {
  // No percentage is possible, so say why rather than printing a misleading number.
  // Skipped when the current figure is also zero - "no prior data" on an empty set
  // of books is noise, not information.
  const noBasis =
    movement !== undefined && movement.change_percent === null && !isZeroMoney(movement.current);

  return (
    <StatCard
      label={label}
      value={movement ? formatMoney(movement.current, currency) : undefined}
      icon={icon}
      delta={movement?.change_percent ?? null}
      deltaGood={risingIsGood}
      hint={noBasis ? 'no prior data' : undefined}
      info={info}
    />
  );
}

function StatCard({
  label,
  value,
  delta,
  deltaGood = true,
  icon: Icon,
  hint,
  hintTone,
  info,
}: {
  label: string;
  value: string | undefined;
  /** A decimal string from the API, or null when there is no basis. */
  delta?: string | null;
  deltaGood?: boolean;
  icon: typeof TrendingUp;
  hint?: string | undefined;
  hintTone?: 'danger';
  /** Explains the figure. Worth writing for anything an owner might misread. */
  info?: ReactNode;
}) {
  // Safe to convert: this picks an arrow direction and a rounded label, not a figure
  // anyone acts on, and the server already rounded it to one decimal place.
  const numeric = delta === null || delta === undefined ? null : Number(delta);
  const positive = (numeric ?? 0) >= 0;
  const good = positive === deltaGood;

  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-2">
        <span className="text-content-muted flex items-center gap-1.5 text-[12px] font-medium">
          {label}
          {info && (
            <InfoTip label={label} align="left">
              {info}
            </InfoTip>
          )}
        </span>
        <Icon className="text-content-muted h-4 w-4 shrink-0" aria-hidden />
      </div>

      <div className="mt-2 flex items-baseline gap-2">
        {value === undefined ? (
          <Skeleton className="h-7 w-24" />
        ) : (
          <span className="text-content text-[24px] leading-none font-semibold tracking-[-0.02em] tabular-nums">
            {value}
          </span>
        )}
      </div>

      <div className="mt-2 flex min-h-[18px] items-center gap-2">
        {numeric !== null && (
          <span
            className={cn(
              'inline-flex items-center gap-0.5 text-[12px] font-medium tabular-nums',
              good ? 'text-success' : 'text-danger',
            )}
          >
            {positive ? (
              <ArrowUpRight className="h-3 w-3" aria-hidden />
            ) : (
              <ArrowDownRight className="h-3 w-3" aria-hidden />
            )}
            {Math.abs(numeric).toFixed(1)}%
          </span>
        )}
        {hint && (
          <span
            className={cn(
              'text-[12px]',
              hintTone === 'danger' ? 'text-danger font-medium' : 'text-content-muted',
            )}
          >
            {hint}
          </span>
        )}
      </div>
    </Card>
  );
}
