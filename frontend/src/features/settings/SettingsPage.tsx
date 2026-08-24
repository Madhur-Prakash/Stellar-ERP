import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Building2,
  Check,
  Copy,
  Laptop,
  Monitor,
  Moon,
  Shield,
  ShieldCheck,
  Smartphone,
  Sun,
  Trash2,
} from 'lucide-react';
import { useLocation, useNavigate } from '@tanstack/react-router';
import { useCallback, useState } from 'react';
import { toast } from 'sonner';

import { PageHeader } from '@/components/layout/AppShell';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { Skeleton } from '@/components/ui/Skeleton';
import { authApi } from '@/features/auth/api';
import {
  passwordPlaceholder,
  summarisePolicy,
  usePasswordPolicy,
} from '@/features/auth/passwordPolicy';
import { useAuth } from '@/features/auth/AuthProvider';
import { organizationsApi, usersApi } from '@/features/organizations/api';
import { useTheme, type Theme } from '@/features/theme/ThemeProvider';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';
import { formatRelative } from '@/lib/format';

export function SettingsPage() {
  const { user, refresh, can } = useAuth();

  // Arriving from the switcher's "Create an organization", or from the dashboard's
  // onboarding button. Both land here with `#create-organization`; without this the
  // page opens at the top with the form below the fold, which reads as the button
  // having gone to the wrong place.
  //
  // The hash is compared with any leading `#` stripped, because that is a detail of
  // the router's location shape rather than something to depend on.
  const hash = useLocation({ select: (location) => location.hash });
  const wantsCreateCard = hash.replace(/^#/, '') === 'create-organization';

  // A ref *callback*, not an effect over a ref object. The card renders only when
  // there is no active organization, so on the render the hash arrives in the node
  // may not exist yet - an effect would run against `null` and silently do nothing,
  // which is exactly what it did. This fires the moment the node attaches, and React
  // re-runs it whenever `wantsCreateCard` flips, covering the other direction too:
  // pressing create from the sidebar while already on this page.
  //
  // `requestAnimationFrame` because a node that attached this frame has no final
  // layout yet, and scrolling to a position that is about to change lands short.
  const createCard = useCallback(
    (node: HTMLDivElement | null) => {
      if (!node || !wantsCreateCard) return;
      requestAnimationFrame(() => node.scrollIntoView({ behavior: 'smooth', block: 'center' }));
    },
    [wantsCreateCard],
  );

  return (
    <div>
      <PageHeader title="Settings" description="Your profile, security, and organization." />

      <div className="grid gap-4 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          <ProfileCard />
          <TwoFactorCard />
          <PasswordCard />
          <SessionsCard />
          {can('organization:update') && user?.active_organization && <OrganizationCard />}
          {/* Always rendered, for two reasons that used to be two dead ends.

              The onboarding path lands here: the dashboard's "Create organization"
              button points at Settings, which rendered nothing at all for a user with
              no organization - so the one screen the button exists to reach had no
              form on it. That also stranded a *suspended* member, whose memberships
              are excluded from the switcher by design.

              And membership is many-to-many: the server puts no limit on how many
              organizations one person owns or belongs to, so gating this card on
              having none contradicted the switcher's "Create another organization". */}
          <div id="create-organization" ref={createCard} className="scroll-mt-20">
            <CreateOrganizationCard />
          </div>

          {/* Outside `OrganizationCard` on purpose. That card needs
              `organization:update`, which a plain member does not have - so leaving
              would have been offered only to people who cannot leave anyway. The
              owner is excluded because the server refuses them: they must hand over
              or delete. */}
          {user?.active_organization && !user.active_organization.is_owner && (
            <LeaveOrganizationCard organizationName={user.active_organization.name} />
          )}
        </div>

        <div className="space-y-4">
          <AppearanceCard />
          <AccountCard onRefresh={refresh} />
        </div>
      </div>
    </div>
  );
}

// =============================================================================
// Profile
// =============================================================================
function ProfileCard() {
  const { refresh } = useAuth();
  const queryClient = useQueryClient();

  // Fetched rather than read from `useAuth`: the signed-in user object carries no phone
  // number, so the field could never show a saved one. `/users/me` is the profile itself.
  const { data: profile } = useQuery({
    queryKey: ['users', 'me'],
    queryFn: usersApi.me,
  });

  // `undefined` until the field is touched, with the saved value read live underneath.
  //
  // These were `useState(user?.full_name ?? '')`, which copies the value on the first
  // render only - and on that render the user has not loaded, so the field initialised to
  // '' and stayed there. That is why the name box was empty while the email box beside it
  // was filled: email is read straight off the user every render. Saving then sent
  // `full_name: ''`, which the API rejects for being under one character, so nothing saved
  // and the reason was a generic toast away from the actual cause.
  const [fullNameEdit, setFullNameEdit] = useState<string>();
  const [phoneEdit, setPhoneEdit] = useState<string>();

  const fullName = fullNameEdit ?? profile?.full_name ?? '';
  const phone = phoneEdit ?? profile?.phone ?? '';

  const save = useMutation({
    mutationFn: () =>
      usersApi.updateProfile({
        full_name: fullName.trim(),
        // Sent even when cleared, so a number can be removed rather than only changed.
        phone: phone.trim(),
      }),
    onSuccess: async () => {
      toast.success('Profile updated');
      // Both caches: this query holds the phone, and the auth user holds the name shown in
      // the sidebar and on every avatar.
      void queryClient.invalidateQueries({ queryKey: ['users', 'me'] });
      await refresh();
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not save your profile'),
  });

  // Blocked here rather than left to a 422. "Save changes" failing on a rule the form never
  // mentioned is the worst version of this: nothing happens and nothing explains why.
  const canSave = fullName.trim() !== '';

  return (
    <Card>
      <CardHeader title="Profile" description="How you appear across the organization." />
      <CardBody className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Input
            label="Full name"
            required
            value={fullName}
            onChange={(event) => setFullNameEdit(event.target.value)}
            error={canSave ? undefined : 'A name is required'}
          />
          <Input
            label="Phone"
            value={phone}
            onChange={(event) => setPhoneEdit(event.target.value)}
            placeholder="+91 98765 43210"
          />
        </div>

        <Input
          label="Email"
          value={profile?.email ?? ''}
          disabled
          // Changing an email requires re-verification, which is its own flow -
          // Stage 9. Disabling with an explanation beats a field that silently
          // fails.
          hint="Email changes need re-verification and arrive in a later stage."
        />

        <div className="flex items-center gap-3">
          <Button loading={save.isPending} disabled={!canSave} onClick={() => save.mutate()}>
            Save changes
          </Button>
          {profile?.is_email_verified ? (
            <Badge tone="success" dot>
              Email verified
            </Badge>
          ) : (
            <Badge tone="warning" dot>
              Email not verified
            </Badge>
          )}
        </div>
      </CardBody>
    </Card>
  );
}

// =============================================================================
// Two-factor authentication
// =============================================================================
function TwoFactorCard() {
  const { user, refresh } = useAuth();
  const [setup, setSetup] = useState<{ secret: string; qr: string } | null>(null);
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<string>();

  const begin = useMutation({
    mutationFn: authApi.beginTwoFactorSetup,
    onSuccess: (data) => setSetup({ secret: data.secret, qr: data.qr_code }),
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : 'Could not start 2FA setup'),
  });

  const enable = useMutation({
    mutationFn: () => authApi.enableTwoFactor(code.trim()),
    onSuccess: async (data) => {
      setRecoveryCodes(data.recovery_codes);
      setSetup(null);
      setCode('');
      setError(undefined);
      toast.success('Two-factor authentication enabled');
      await refresh();
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : 'That code did not work'),
  });

  const disable = useMutation({
    mutationFn: () => authApi.disableTwoFactor(password),
    onSuccess: async () => {
      toast.success('Two-factor authentication disabled');
      setPassword('');
      await refresh();
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : 'Password is incorrect'),
  });

  // Recovery codes are returned exactly once. This screen is the only chance to
  // save them, so it blocks everything else until acknowledged.
  if (recoveryCodes) {
    return (
      <Card>
        <CardHeader
          title="Save your recovery codes"
          description="Each code works once. Store them somewhere safe - they are the only way in if you lose your authenticator."
        />
        <CardBody className="space-y-4">
          <div className="bg-surface-sunken border-border grid grid-cols-2 gap-2 rounded-lg border p-4 font-mono text-[13px] sm:grid-cols-5">
            {recoveryCodes.map((recoveryCode) => (
              <span key={recoveryCode} className="text-content">
                {recoveryCode}
              </span>
            ))}
          </div>

          <div className="flex flex-wrap gap-2">
            <Button
              variant="secondary"
              leftIcon={<Copy className="h-4 w-4" />}
              onClick={() => {
                void navigator.clipboard
                  .writeText(recoveryCodes.join('\n'))
                  .then(() => toast.success('Recovery codes copied'));
              }}
            >
              Copy all
            </Button>
            <Button onClick={() => setRecoveryCodes(null)}>I have saved them</Button>
          </div>
        </CardBody>
      </Card>
    );
  }

  if (setup) {
    return (
      <Card>
        <CardHeader
          title="Set up two-factor authentication"
          description="Scan the QR code with your authenticator app, then enter the code it shows."
        />
        <CardBody className="space-y-4">
          <div className="flex flex-wrap items-start gap-5">
            <img
              src={setup.qr}
              alt="Two-factor QR code"
              className="border-border h-40 w-40 rounded-lg border bg-white p-2"
            />
            <div className="min-w-[200px] flex-1 space-y-3">
              <div>
                <p className="text-content-secondary mb-1 text-[12px] font-medium">
                  Or enter this key manually
                </p>
                <code className="bg-surface-sunken text-content block rounded-md px-2.5 py-2 font-mono text-[12px] break-all">
                  {setup.secret}
                </code>
              </div>

              <Input
                label="Verification code"
                value={code}
                onChange={(event) => setCode(event.target.value.replace(/\D/g, ''))}
                error={error}
                inputMode="numeric"
                maxLength={6}
                placeholder="000000"
                className="font-mono tracking-[0.2em]"
              />

              <div className="flex gap-2">
                <Button
                  loading={enable.isPending}
                  disabled={code.length < 6}
                  onClick={() => enable.mutate()}
                >
                  Verify and enable
                </Button>
                <Button variant="ghost" onClick={() => setSetup(null)}>
                  Cancel
                </Button>
              </div>
            </div>
          </div>
        </CardBody>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader
        title="Two-factor authentication"
        description="Require a code from your phone in addition to your password."
        action={
          user?.is_two_factor_enabled ? (
            <Badge tone="success" dot>
              Enabled
            </Badge>
          ) : (
            <Badge tone="neutral" dot>
              Disabled
            </Badge>
          )
        }
      />
      <CardBody>
        {user?.is_two_factor_enabled ? (
          <div className="space-y-3">
            <p className="text-content-muted text-[13px]">
              Your account is protected. Disabling 2FA requires your password.
            </p>
            <div className="flex flex-wrap items-start gap-2">
              <div className="w-56">
                <Input
                  type="password"
                  placeholder="Confirm your password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  error={error}
                  autoComplete="current-password"
                  aria-label="Password"
                />
              </div>
              <Button
                variant="destructive"
                loading={disable.isPending}
                disabled={!password}
                onClick={() => disable.mutate()}
              >
                Disable 2FA
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex items-start gap-3">
            <div
              className="bg-primary/10 text-primary flex h-9 w-9 shrink-0 items-center justify-center rounded-lg"
              aria-hidden
            >
              <ShieldCheck className="h-4 w-4" />
            </div>
            <div className="flex-1">
              <p className="text-content-muted mb-3 text-[13px] leading-relaxed">
                Works with Google Authenticator, 1Password, Authy, and any other TOTP app.
              </p>
              <Button loading={begin.isPending} onClick={() => begin.mutate()}>
                Set up 2FA
              </Button>
            </div>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

// =============================================================================
// Password
// =============================================================================
function PasswordCard() {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [error, setError] = useState<string>();

  // Fetched, not hard-coded - the server owns the rules.
  const { data: policy } = usePasswordPolicy();

  const change = useMutation({
    mutationFn: () => authApi.changePassword({ current_password: current, new_password: next }),
    onSuccess: () => {
      toast.success('Password changed', {
        description: 'All sessions were signed out. Please sign in again.',
      });
      // No local cleanup needed: the server revoked every session including this
      // one, so the next request 401s and the auth provider signs us out.
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        setError(err.fieldErrors['password'] ?? err.message);
        return;
      }
      setError('Could not change your password');
    },
  });

  return (
    <Card>
      <CardHeader
        title="Password"
        description="Changing your password signs you out of every device."
      />
      <CardBody className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Input
            label="Current password"
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(event) => setCurrent(event.target.value)}
          />
          <Input
            label="New password"
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(event) => setNext(event.target.value)}
            placeholder={passwordPlaceholder(policy)}
            error={error}
            hint={error ? undefined : summarisePolicy(policy)}
          />
        </div>
        <Button
          loading={change.isPending}
          disabled={!current || !next}
          onClick={() => {
            setError(undefined);
            change.mutate();
          }}
        >
          Change password
        </Button>
      </CardBody>
    </Card>
  );
}

// =============================================================================
// Sessions / device history
// =============================================================================
function SessionsCard() {
  const queryClient = useQueryClient();
  const { data: sessions, isLoading } = useQuery({
    queryKey: ['sessions'],
    queryFn: authApi.listSessions,
  });

  const revoke = useMutation({
    mutationFn: authApi.revokeSession,
    onSuccess: () => {
      toast.success('Session revoked');
      void queryClient.invalidateQueries({ queryKey: ['sessions'] });
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not revoke the session'),
  });

  return (
    <Card>
      <CardHeader
        title="Active sessions"
        description="Devices currently signed in to your account."
      />
      <CardBody className="p-0">
        {isLoading ? (
          <div className="space-y-3 p-5">
            {Array.from({ length: 2 }).map((_, index) => (
              <Skeleton key={index} className="h-12 rounded-md" />
            ))}
          </div>
        ) : (
          <ul className="divide-border divide-y">
            {(sessions ?? []).map((session) => {
              const Icon =
                session.device_type === 'mobile' || session.device_type === 'tablet'
                  ? Smartphone
                  : session.device_type === 'api'
                    ? Monitor
                    : Laptop;

              return (
                <li key={session.id} className="flex items-center gap-3 px-5 py-3.5">
                  <div
                    className="bg-surface-sunken text-content-muted flex h-8 w-8 shrink-0 items-center justify-center rounded-lg"
                    aria-hidden
                  >
                    <Icon className="h-4 w-4" />
                  </div>

                  <div className="min-w-0 flex-1">
                    <p className="text-content flex items-center gap-2 text-[13px] font-medium">
                      {session.device_label ?? 'Unknown device'}
                      {session.is_current && <Badge tone="primary">This device</Badge>}
                    </p>
                    <p className="text-content-muted text-[11px]">
                      {session.ip_address ?? 'unknown IP'} · via {session.login_method} ·{' '}
                      {session.last_used_at
                        ? `active ${formatRelative(session.last_used_at)}`
                        : `started ${formatRelative(session.created_at)}`}
                    </p>
                  </div>

                  {/* The current session is not offered - signing out is the
                      dedicated action for that, and revoking yourself here
                      would look like a bug. */}
                  {!session.is_current && (
                    <Button
                      variant="ghost"
                      size="icon"
                      title="Revoke this session"
                      aria-label={`Revoke session on ${session.device_label ?? 'unknown device'}`}
                      onClick={() => revoke.mutate(session.id)}
                    >
                      <Trash2 className="text-danger h-3.5 w-3.5" />
                    </Button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}

/**
 * Currencies offered for an organization.
 *
 * A short list rather than all 180 ISO codes: this is self-hosted software for one small
 * business, which deals in one currency and picks it once. The API still accepts any
 * three-letter code, so this is what the dropdown offers, not what the system permits.
 */
const CURRENCY_OPTIONS = [
  { value: 'INR', label: 'INR - Indian rupee' },
  { value: 'USD', label: 'USD - US dollar' },
  { value: 'EUR', label: 'EUR - Euro' },
  { value: 'GBP', label: 'GBP - Pound sterling' },
  { value: 'AED', label: 'AED - UAE dirham' },
  { value: 'SGD', label: 'SGD - Singapore dollar' },
  { value: 'AUD', label: 'AUD - Australian dollar' },
  { value: 'CAD', label: 'CAD - Canadian dollar' },
  { value: 'JPY', label: 'JPY - Japanese yen' },
  { value: 'LKR', label: 'LKR - Sri Lankan rupee' },
  { value: 'NPR', label: 'NPR - Nepalese rupee' },
  { value: 'BDT', label: 'BDT - Bangladeshi taka' },
];

/**
 * Timezones, from the browser's own IANA database where it exposes one.
 *
 * `Intl.supportedValuesOf` means there is no list to maintain and no chance of offering a
 * zone the runtime cannot resolve. Older browsers lack it, hence the fallback - a handful
 * of zones rather than an empty dropdown, which would make the field unusable.
 */
const TIMEZONE_OPTIONS = (() => {
  let zones: string[] = ['Asia/Kolkata', 'Asia/Dubai', 'Asia/Singapore', 'Europe/London', 'UTC'];
  try {
    const supported = Intl.supportedValuesOf?.('timeZone');
    if (supported && supported.length > 0) zones = [...supported];
  } catch {
    // Keep the fallback.
  }
  return zones.map((zone) => ({ value: zone, label: zone }));
})();

/** Month names for the financial-year start, from `Intl` so there is no list to translate. */
const MONTH_OPTIONS = Array.from({ length: 12 }, (_, index) => ({
  value: String(index + 1),
  label: new Intl.DateTimeFormat('en', { month: 'long' }).format(new Date(2000, index, 1)),
}));

/**
 * Guarantee the value already saved is offered.
 *
 * Without this, a stored value the list happens not to contain leaves the `<select>` with
 * nothing matching - so the browser displays the *first* option, and pressing Save writes
 * that instead. A settings form that silently changes a setting you never touched.
 *
 * Not hypothetical: organizations here are seeded `Asia/Kolkata`, while
 * `Intl.supportedValuesOf` lists that same zone under its older canonical name
 * `Asia/Calcutta` - 417 zones, and `Asia/Kolkata` is not among them. Identical zone,
 * different string, and the field would have quietly rewritten it.
 */
function withCurrent(
  options: { value: string; label: string }[],
  current: string | undefined,
): { value: string; label: string }[] {
  if (!current || options.some((option) => option.value === current)) return options;
  return [{ value: current, label: current }, ...options];
}

// =============================================================================
// Organization
// =============================================================================
function OrganizationCard() {
  const queryClient = useQueryClient();
  const { can } = useAuth();
  const { data: organization, isLoading } = useQuery({
    queryKey: ['organization', 'current'],
    queryFn: organizationsApi.current,
  });

  const [name, setName] = useState('');
  const [gstin, setGstin] = useState('');
  // Undefined until touched, so an untouched field is left out of the PATCH entirely rather
  // than sent back as the value it already had.
  const [currency, setCurrency] = useState<string>();
  const [timezone, setTimezone] = useState<string>();
  const [fiscalStart, setFiscalStart] = useState<string>();
  const [error, setError] = useState<string>();

  const save = useMutation({
    mutationFn: () =>
      organizationsApi.update({
        ...(name.trim() ? { name: name.trim() } : {}),
        ...(gstin.trim() ? { gstin: gstin.trim() } : {}),
        ...(currency ? { currency } : {}),
        ...(timezone ? { timezone } : {}),
        ...(fiscalStart ? { fiscal_year_start_month: Number(fiscalStart) } : {}),
      }),
    onSuccess: () => {
      toast.success('Organization updated');
      setError(undefined);
      void queryClient.invalidateQueries({ queryKey: ['organization', 'current'] });
      void queryClient.invalidateQueries({ queryKey: ['organizations'] });
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        setError(err.fieldErrors['gstin'] ?? err.message);
        return;
      }
      setError('Could not save');
    },
  });

  if (isLoading) return <Skeleton className="h-56 rounded-xl" />;

  return (
    <Card>
      <CardHeader
        title="Organization"
        description={`${organization?.name ?? ''} · ${organization?.slug ?? ''}`}
        action={<Badge tone="neutral">{organization?.plan}</Badge>}
      />
      <CardBody className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Input
            label="Display name"
            defaultValue={organization?.name}
            onChange={(event) => setName(event.target.value)}
          />
          <Input
            label="GSTIN"
            defaultValue={organization?.gstin ?? ''}
            onChange={(event) => setGstin(event.target.value)}
            error={error}
            placeholder="29AABCU9603R1ZM"
            hint="15 characters. Validated on save."
          />
        </div>

        {/* These three were printed as plain text, which reads as "fixed, do not ask" - and
            the API had accepted all three as editable the whole time.

            `options`, not children: this Select renders the prop and drops children
            silently, so option elements would have produced three empty dropdowns that the
            type checker was perfectly happy with. */}
        <div className="grid gap-4 sm:grid-cols-3">
          <Select
            label="Currency"
            value={currency ?? organization?.currency ?? 'INR'}
            onChange={(event) => setCurrency(event.target.value)}
            options={withCurrent(CURRENCY_OPTIONS, organization?.currency)}
            hint="Used for every amount shown."
          />

          <Select
            label="Timezone"
            value={timezone ?? organization?.timezone ?? 'Asia/Kolkata'}
            onChange={(event) => setTimezone(event.target.value)}
            options={withCurrent(TIMEZONE_OPTIONS, organization?.timezone)}
            hint="Decides what counts as today."
          />

          <Select
            label="Financial year starts"
            value={fiscalStart ?? String(organization?.fiscal_year_start_month ?? 4)}
            onChange={(event) => setFiscalStart(event.target.value)}
            options={MONTH_OPTIONS}
            hint="April in India."
          />
        </div>

        {fiscalStart && Number(fiscalStart) !== organization?.fiscal_year_start_month && (
          // Stated rather than silently accepted: the years already created keep their own
          // dates, so a mid-year change leaves the report presets and the existing fiscal
          // year describing different windows until the next one opens.
          <p className="text-warning text-[12px]">
            Changing this does not move the financial years already created. Entries already posted
            keep the year they went into.
          </p>
        )}

        <Button loading={save.isPending} onClick={() => save.mutate()}>
          Save organization
        </Button>

        {can('organization:delete') && organization && (
          <DangerZone organizationName={organization.name} />
        )}
      </CardBody>
    </Card>
  );
}

/**
 * Leaving an organization you are a member of.
 *
 * Distinct from deleting it, and the distinction is the point: leaving removes *you*
 * and touches nothing else, while deleting removes the organization for everybody.
 * The server hard-deletes the membership row, so this is not reversible from either
 * side - getting back in needs a fresh invitation.
 */
function LeaveOrganizationCard({ organizationName }: { organizationName: string }) {
  const { refresh } = useAuth();
  const navigate = useNavigate();

  const leave = useMutation({
    mutationFn: organizationsApi.leave,
    onSuccess: async (result) => {
      toast.success(result.message);
      // The token still names the organization this user no longer belongs to.
      await refresh();
      void navigate({ to: '/', replace: true });
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : 'Could not leave the organization'),
  });

  return (
    <Card>
      <CardHeader
        title="Leave this organization"
        description={`You will lose access to ${organizationName} immediately.`}
      />
      <CardBody className="space-y-3">
        <p className="text-content-muted text-[12px] leading-relaxed">
          Nothing in {organizationName} is deleted - the entries you posted stay where they are, and
          the audit trail keeps your name against them. Only your membership is removed, and it is
          removed for good: an owner or admin has to invite you again to undo it.
        </p>
        <Button
          variant="destructive"
          loading={leave.isPending}
          onClick={() => {
            if (window.confirm(`Leave ${organizationName}? You will need a new invitation.`)) {
              leave.mutate();
            }
          }}
        >
          Leave organization
        </Button>
      </CardBody>
    </Card>
  );
}

/**
 * Deleting the organization.
 *
 * The API has always allowed this - owner only - and both clients simply never
 * offered it, so the documented capability existed nowhere a user could reach.
 *
 * **Type-to-confirm rather than a `window.confirm`.** This removes a company's
 * entire books: ledger, invoices, documents, members. A dialog people dismiss by
 * reflex is the wrong instrument for the single most destructive action in the
 * product, and the name has to be typed for the same reason GitHub asks for a
 * repository name - it forces the user to look at *which* organization they are
 * about to remove, which matters now that one person can be in several.
 */
function DangerZone({ organizationName }: { organizationName: string }) {
  const { refresh } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState('');

  const remove = useMutation({
    mutationFn: organizationsApi.remove,
    onSuccess: async (result) => {
      toast.success(result.message, {
        ...(result.detail ? { description: result.detail } : {}),
      });
      // The access token still names the organization that no longer exists, so the
      // session has to be rebuilt before anything else is fetched with it.
      await refresh();
      void navigate({ to: '/', replace: true });
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : 'Could not delete the organization'),
  });

  return (
    <div className="border-danger/30 mt-2 rounded-lg border border-dashed p-4">
      <p className="text-content text-[13px] font-semibold">Delete this organization</p>
      <p className="text-content-muted mt-1 text-[12px] leading-relaxed">
        <strong className="text-danger">This cannot be undone from the app.</strong>{' '}
        {organizationName} disappears for <em>everyone</em> in it, along with its ledger, invoices,
        documents and members. There is no restore button, no trash, and no support desk to call -
        this software runs on your server, so you are the only one who can put it back.
      </p>
      <p className="text-content-muted mt-2 text-[12px] leading-relaxed">
        The rows are not erased. The organization is flagged deleted by setting{' '}
        <code className="text-[11px]">deleted_at</code>, so recovering it means clearing that column
        directly in PostgreSQL -{' '}
        <code className="text-[11px]">
          UPDATE organization SET deleted_at = NULL WHERE id = ...
        </code>{' '}
        - and anyone without database access is simply locked out of it.
      </p>

      {open ? (
        <div className="mt-3 space-y-3">
          <Input
            label={`Type "${organizationName}" to confirm`}
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            placeholder={organizationName}
            autoFocus
          />
          <div className="flex items-center gap-2">
            <Button
              variant="destructive"
              loading={remove.isPending}
              disabled={typed.trim() !== organizationName}
              onClick={() => remove.mutate()}
            >
              Delete permanently
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setOpen(false);
                setTyped('');
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <Button variant="destructive" className="mt-3" onClick={() => setOpen(true)}>
          Delete organization
        </Button>
      )}
    </div>
  );
}

// =============================================================================
// Appearance
// =============================================================================
function AppearanceCard() {
  const { theme, setTheme } = useTheme();

  const options: { value: Theme; label: string; icon: typeof Sun }[] = [
    { value: 'light', label: 'Light', icon: Sun },
    { value: 'dark', label: 'Dark', icon: Moon },
    { value: 'system', label: 'System', icon: Monitor },
  ];

  return (
    <Card>
      <CardHeader title="Appearance" description="Applies to this browser." />
      <CardBody>
        <div className="grid grid-cols-3 gap-2" role="radiogroup" aria-label="Colour theme">
          {options.map((option) => (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={theme === option.value}
              onClick={() => setTheme(option.value)}
              className={cn(
                'flex flex-col items-center gap-1.5 rounded-lg border px-2 py-3 text-[12px] font-medium transition-colors',
                theme === option.value
                  ? 'border-primary bg-primary/10 text-primary'
                  : 'border-border text-content-secondary hover:bg-surface-hover',
              )}
            >
              <option.icon className="h-4 w-4" aria-hidden />
              {option.label}
              {theme === option.value && <Check className="h-3 w-3" aria-hidden />}
            </button>
          ))}
        </div>
      </CardBody>
    </Card>
  );
}

// =============================================================================
// Account summary
// =============================================================================
function AccountCard({ onRefresh }: { onRefresh: () => Promise<void> }) {
  const { user, signOut } = useAuth();
  const { data: stats } = useQuery({ queryKey: ['user-stats'], queryFn: usersApi.stats });

  return (
    <Card>
      <CardHeader title="Account" />
      <CardBody className="space-y-4">
        <dl className="space-y-2.5 text-[13px]">
          <Row label="Organizations" value={stats ? String(stats.organizations) : '-'} />
          <Row label="Active sessions" value={stats ? String(stats.active_sessions) : '-'} />
          {user?.is_two_factor_enabled && (
            <Row
              label="Recovery codes left"
              value={stats ? String(stats.recovery_codes_remaining) : '-'}
            />
          )}
          <Row
            label="Last sign-in"
            value={user?.last_login_at ? formatRelative(user.last_login_at) : '-'}
          />
        </dl>

        <div className="border-border space-y-2 border-t pt-4">
          <Button variant="secondary" fullWidth onClick={() => void onRefresh()}>
            Refresh permissions
          </Button>
          <Button
            variant="ghost"
            fullWidth
            leftIcon={<Shield className="h-4 w-4" />}
            onClick={() => {
              if (window.confirm('Sign out of every device?')) {
                void signOut(true);
              }
            }}
          >
            Sign out everywhere
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <dt className="text-content-muted">{label}</dt>
      <dd className="text-content font-medium">{value}</dd>
    </div>
  );
}

/**
 * Create an organization - the onboarding path, and the "one more" path.
 *
 * One person can own or belong to any number of organizations; the server sets no
 * limit, and the switcher in the sidebar is how you move between them. So this card
 * is not onboarding-only, and its copy says which case you are in.
 */
export function CreateOrganizationCard() {
  const { user, refresh, switchOrganization } = useAuth();
  const [name, setName] = useState('');
  const [error, setError] = useState<string>();
  const hasOrganization = Boolean(user?.active_organization);

  const create = useMutation({
    mutationFn: () => organizationsApi.create({ name: name.trim() }),
    onSuccess: async (organization) => {
      toast.success(`${organization.name} created`, { description: 'You are its owner.' });
      setName('');
      // Switch into it rather than only refreshing. The server does set
      // `last_organization_id`, but the access token still carries the old
      // organization - so a plain refresh leaves you looking at the previous set of
      // books having just been told a new one exists.
      try {
        await switchOrganization(organization.id);
      } catch {
        await refresh();
      }
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : 'Could not create it'),
  });

  return (
    <Card>
      <CardHeader
        title={hasOrganization ? 'Create another organization' : 'Create an organization'}
        description={
          hasOrganization
            ? 'You will be its owner. Your existing organizations stay exactly as they are, and the sidebar switcher moves between them.'
            : 'You will be its owner, with full access.'
        }
      />
      <CardBody className="space-y-4">
        <Input
          label="Company name"
          placeholder="Acme Trading Co"
          leftIcon={<Building2 />}
          value={name}
          onChange={(event) => setName(event.target.value)}
          error={error}
        />
        <Button loading={create.isPending} disabled={!name.trim()} onClick={() => create.mutate()}>
          Create organization
        </Button>
      </CardBody>
    </Card>
  );
}
