/**
 * Accounts - every account and card in one place, with every detail editable.
 *
 * A screen of its own rather than only the panel at the foot of Billing, because the two
 * are asked at different times. Billing is "record this payment, quickly"; this is "which
 * account is this, and what is its number" - the question someone has open beside a bank
 * statement, or when a new card arrives, or when the accountant asks.
 *
 * Everything here is editable, including the account number. That is the point: the seeded
 * chart creates "Primary Bank Account" before anyone has said which bank it is, so a screen
 * that could only *show* details would leave the account most organizations actually use
 * permanently blank.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Archive, CreditCard, Landmark, Plus, RotateCcw, Trash2, Wallet } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { PageHeader } from '@/components/ui/DataTable';
import { EmptyState } from '@/components/ui/EmptyState';
import { Hint } from '@/components/ui/Hint';
import { InfoTip } from '@/components/ui/InfoTip';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { Skeleton } from '@/components/ui/Skeleton';
import {
  MAX_DIGITS,
  MIN_DIGITS,
  NETWORK_LABELS,
  cardNumberProblem,
  normaliseCardNumber,
} from '@/features/billing/cards';
import {
  type Card as PaymentCard,
  type CardKind,
  type MoneyAccount,
  type MoneyKind,
  billingApi,
} from '@/features/billing/api';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';

export function AccountsPage() {
  const [adding, setAdding] = useState<'account' | 'card' | null>(null);
  const [showArchived, setShowArchived] = useState(false);

  const { data: options } = useQuery({
    queryKey: ['billing-options'],
    queryFn: () => billingApi.options(),
  });

  // The dedicated list, not `options.money_accounts`, because only this one can be asked
  // for archived accounts. `options` deliberately never carries them.
  const { data: allAccounts, isLoading } = useQuery({
    queryKey: ['money-accounts', showArchived],
    queryFn: () => billingApi.moneyAccounts(showArchived ? { include_archived: true } : undefined),
  });

  const { data: cards } = useQuery({
    queryKey: ['billing-cards', showArchived],
    queryFn: () => billingApi.cards(showArchived ? { include_archived: true } : undefined),
  });

  // A debit card resolves to the bank account it draws on, so the raw list holds that
  // account twice. Deduplicated to the real accounts here; the cards get their own
  // section below, which is where someone looks for them anyway.
  const seen = new Set<string>();
  const accounts = (allAccounts ?? options?.money_accounts ?? []).filter((account) => {
    if (account.card_id || seen.has(account.id)) return false;
    seen.add(account.id);
    return true;
  });
  // Only an active account can back a new debit card.
  const banks = accounts.filter((account) => account.kind === 'bank' && account.is_active);
  const listed = cards ?? options?.cards ?? [];

  // Archived things go in their own group rather than sitting among the live ones with a
  // badge. Interleaved, "Archived" reads as a property of one row you have to notice; in its
  // own section it reads as a state, which is what it is.
  const activeAccounts = accounts.filter((account) => account.is_active);
  const archivedAccounts = accounts.filter((account) => !account.is_active);
  const activeCards = listed.filter((card) => card.is_active);
  const archivedCards = listed.filter((card) => !card.is_active);

  return (
    <div>
      <PageHeader
        title="Banks & cards"
        description="Where your money sits and the cards you spend on. These are the choices offered when recording a payment."
      />

      <div className="mb-4 flex flex-wrap gap-2">
        <Button
          variant={adding === 'account' ? 'secondary' : 'primary'}
          onClick={() => setAdding(adding === 'account' ? null : 'account')}
        >
          <Plus className="h-4 w-4" aria-hidden />
          {adding === 'account' ? 'Close' : 'Add an account'}
        </Button>
        <Button variant="secondary" onClick={() => setAdding(adding === 'card' ? null : 'card')}>
          <CreditCard className="h-4 w-4" aria-hidden />
          {adding === 'card' ? 'Close' : 'Add a card'}
        </Button>
      </div>

      {adding === 'account' && <NewAccountCard onDone={() => setAdding(null)} />}
      {adding === 'card' && <NewCardCard banks={banks} onDone={() => setAdding(null)} />}

      <Card className="mb-4">
        <CardHeader
          title="Cash & bank"
          description="Money you have. Open an account to fill in or correct its details."
          action={
            <div className="flex items-center gap-3">
              {/* One toggle for both sections - archived accounts and archived cards are
                  the same question asked once. */}
              <button
                type="button"
                onClick={() => setShowArchived((shown) => !shown)}
                className="text-content-muted hover:text-content text-[12px]"
              >
                {showArchived ? 'Hide archived' : 'Show archived'}
              </button>
              {/* `right`, because this sits in the card header's action slot - hard against
                  the right edge - and a panel opening rightwards from there is clipped by
                  the window. Same reason the charts on the accounting screen do it. */}
              <InfoTip label="Account numbers" align="right">
                <p>
                  An account number is stored <strong>encrypted</strong>, and stored in full -
                  unlike a card number, which is never kept at all. You need it to be paid and to
                  match a statement, so keeping only four digits would make it useless.
                </p>
                <p className="mt-2">Lists show the last four digits only.</p>
                <p className="mt-2">
                  <strong>Archive</strong> stops an account being offered when recording a payment,
                  and keeps the entries that already used it.
                </p>
                <p className="mt-2">
                  <strong>Delete</strong> is greyed out where it is not possible - hover it and it
                  says why. An account with entries against it cannot be removed without orphaning
                  them, and the accounts created with your books are posted to by role. Archive
                  those instead.
                </p>
              </InfoTip>
            </div>
          }
        />
        <CardBody>
          {isLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-14" />
              <Skeleton className="h-14" />
            </div>
          ) : accounts.length === 0 ? (
            <EmptyState
              title="No accounts yet"
              description="Add a bank account or a cash box to start recording payments."
            />
          ) : (
            <>
              <ul className="divide-border divide-y">
                {activeAccounts.map((account) => (
                  <AccountRow key={account.id} account={account} />
                ))}
              </ul>
              <ArchivedGroup
                count={archivedAccounts.length}
                note="No longer offered when recording a payment. Entries that used them keep their names."
              >
                {archivedAccounts.map((account) => (
                  <AccountRow key={account.id} account={account} />
                ))}
              </ArchivedGroup>
            </>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Cards"
          description="A credit card is money you owe, not money you have. A debit card spends from the bank account it draws on."
          action={
            <button
              type="button"
              onClick={() => setShowArchived((shown) => !shown)}
              className="text-content-muted hover:text-content text-[12px]"
            >
              {showArchived ? 'Hide archived' : 'Show archived'}
            </button>
          }
        />
        <CardBody>
          {listed.length === 0 ? (
            <EmptyState
              title="No cards yet"
              description="Add one to record what you spend on it. Only the network and last four digits are stored."
            />
          ) : (
            <>
              <ul className="divide-border divide-y">
                {activeCards.map((card) => (
                  <CardRow key={card.id} card={card} />
                ))}
              </ul>
              <ArchivedGroup
                count={archivedCards.length}
                note="No longer offered when recording a payment. Past entries still name them."
              >
                {archivedCards.map((card) => (
                  <CardRow key={card.id} card={card} />
                ))}
              </ArchivedGroup>
            </>
          )}
        </CardBody>
      </Card>
    </div>
  );
}

/**
 * A labelled group for archived rows, or nothing at all when there are none.
 *
 * Its own section rather than an "Archived" badge on rows mixed in with the live ones.
 * Interleaved, the badge is a property of one row you have to notice; under a heading it is
 * a state, which is what archiving actually is - and it stops a closed account sitting in the
 * middle of the list you are reading down.
 *
 * Renders nothing when empty, so the caller does not have to guard as well as pass the count.
 */
function ArchivedGroup({
  count,
  note,
  children,
}: {
  count: number;
  note: string;
  children: ReactNode;
}) {
  if (count === 0) return null;

  return (
    <div className="border-border mt-4 border-t pt-4">
      <div className="mb-1 flex items-center gap-2">
        <Archive className="text-content-muted h-3.5 w-3.5" aria-hidden />
        <h4 className="text-content-secondary text-[12px] font-medium">Archived ({count})</h4>
      </div>
      <p className="text-content-muted mb-1 text-[11px]">{note}</p>
      <ul className="divide-border divide-y">{children}</ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// One account, expandable into its details
// ---------------------------------------------------------------------------
function AccountRow({ account }: { account: MoneyAccount }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const subtitle = [account.bank_name, account.holder_name].filter(Boolean).join(' · ');

  const toggle = useMutation({
    mutationFn: () =>
      account.is_active
        ? billingApi.archiveMoneyAccount(account.id)
        : billingApi.restoreMoneyAccount(account.id),
    onSuccess: (updated) => {
      void queryClient.invalidateQueries({ queryKey: ['money-accounts'] });
      // The pickers on the recording form come from this payload, and an archived account
      // must leave them.
      void queryClient.invalidateQueries({ queryKey: ['billing-options'] });
      void queryClient.invalidateQueries({ queryKey: ['accounts'] });
      toast.success(updated.is_active ? `Restored ${updated.name}` : `Archived ${updated.name}`, {
        description: updated.is_active
          ? 'It can be chosen when recording a payment again.'
          : 'Past entries still name it; it is no longer offered.',
      });
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not update the account'),
  });

  const remove = useMutation({
    mutationFn: () => billingApi.deleteMoneyAccount(account.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['money-accounts'] });
      void queryClient.invalidateQueries({ queryKey: ['billing-options'] });
      void queryClient.invalidateQueries({ queryKey: ['accounts'] });
      void queryClient.invalidateQueries({ queryKey: ['trial-balance'] });
      toast.success(`Deleted ${account.name}`);
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not delete the account'),
  });

  return (
    <li className={cn('py-3', !account.is_active && 'opacity-60')}>
      <div className="flex items-center gap-3">
        <span className="text-content-muted shrink-0" aria-hidden>
          {account.kind === 'cash' ? (
            <Wallet className="h-4 w-4" />
          ) : (
            <Landmark className="h-4 w-4" />
          )}
        </span>
        <span className="min-w-0 flex-1">
          <span className="text-content block truncate text-[14px]">
            {account.name}
            {account.account_number_last4 && (
              <span className="text-content-muted tabular-nums">
                {' '}
                ··{account.account_number_last4}
              </span>
            )}
          </span>
          <span className="text-content-muted block truncate text-[12px]">
            {subtitle || account.code}
          </span>
        </span>
        {account.is_default && <Badge tone="primary">Default</Badge>}
        {/* No "Archived" badge - the section heading above these rows already says it, and
            saying it twice on the same row is noise. */}
        <Badge tone="neutral">{account.kind === 'cash' ? 'Cash' : 'Bank'}</Badge>
        {/* Cash in hand has no bank, no number and no holder, so there is nothing to open. */}
        {account.kind !== 'cash' && (
          <Button variant="ghost" onClick={() => setOpen((shown) => !shown)}>
            {open ? 'Close' : account.bank_name ? 'Edit details' : 'Add details'}
          </Button>
        )}
        {/* Only where the server says it is allowed. A seeded account cannot be
            deactivated - later modules post to it by role - so `can_archive` is false and
            no button appears, rather than one that always fails. */}
        {account.can_archive && (
          <Button
            variant="ghost"
            onClick={() => toggle.mutate()}
            disabled={toggle.isPending}
            title={
              account.is_active
                ? 'Stop offering this account. Entries that used it keep its name.'
                : 'Offer this account again when recording a payment.'
            }
          >
            {account.is_active ? (
              <Archive className="h-3.5 w-3.5" aria-hidden />
            ) : (
              <RotateCcw className="h-3.5 w-3.5" aria-hidden />
            )}
            {account.is_active ? 'Archive' : 'Restore'}
            <span className="sr-only"> {account.name}</span>
          </Button>
        )}
        {/* **Always rendered, disabled when it would fail**, with the server's reason as
            the hover tooltip. Kept visible on purpose: the greyed control plus its
            explanation answers "can I delete this, and if not why" in place, where hiding it
            left the question unanswered and made the feature look absent. */}
        <Hint
          text={
            account.delete_blocked_reason ??
            'Delete this account. Nothing has been recorded against it.'
          }
        >
          <Button
            variant="ghost"
            onClick={() => {
              if (
                window.confirm(
                  `Delete ${account.name}? This cannot be undone. Nothing has been ` +
                    'recorded against it, so no entries are affected.',
                )
              ) {
                remove.mutate();
              }
            }}
            disabled={!account.can_delete || remove.isPending}
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden />
            Delete
            <span className="sr-only"> {account.name}</span>
          </Button>
        </Hint>
      </div>
      {open && <BankDetailsForm account={account} onDone={() => setOpen(false)} />}
    </li>
  );
}

/**
 * Which bank, whose name, which number.
 *
 * Fetches the existing values first, **including the full account number**, because the
 * alternative is an edit form that silently wipes a number the user cannot see. Saving
 * replaces the whole set, so clearing a field clears it on the server.
 */
function BankDetailsForm({ account, onDone }: { account: MoneyAccount; onDone: () => void }) {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['bank-details', account.id],
    queryFn: () => billingApi.bankDetails(account.id),
  });

  // `null` means "not touched yet", so the fetched value shows through without an effect
  // copying server state into local state - the pattern that goes wrong the moment the
  // query resolves a second time.
  const [name, setName] = useState<string | null>(null);
  const [bankName, setBankName] = useState<string | null>(null);
  const [holderName, setHolderName] = useState<string | null>(null);
  const [accountNumber, setAccountNumber] = useState<string | null>(null);

  // Falls back to the row's own name while the request is in flight, so the field is never
  // briefly empty on a screen where an empty name would look like data loss.
  const accountName = name ?? data?.name ?? account.name;
  const bank = bankName ?? data?.bank_name ?? '';
  const holder = holderName ?? data?.holder_name ?? '';
  const number = accountNumber ?? data?.account_number ?? '';

  const save = useMutation({
    mutationFn: () =>
      // Empty fields are omitted, not sent as `""`. Both mean "cleared" to a `PUT`, but
      // the account number has a minimum length, so an empty string would come back as a
      // validation error instead of removing the number.
      billingApi.saveBankDetails(account.id, {
        ...(accountName.trim() ? { name: accountName.trim() } : {}),
        ...(bank.trim() ? { bank_name: bank.trim() } : {}),
        ...(holder.trim() ? { holder_name: holder.trim() } : {}),
        ...(number.trim() ? { account_number: number.trim() } : {}),
      }),
    onSuccess: (saved) => {
      void queryClient.invalidateQueries({ queryKey: ['bank-details', account.id] });
      void queryClient.invalidateQueries({ queryKey: ['money-accounts'] });
      void queryClient.invalidateQueries({ queryKey: ['billing-options'] });
      void queryClient.invalidateQueries({ queryKey: ['accounts'] });
      toast.success(`Saved ${saved.name}`, {
        description: saved.bank_name ?? undefined,
      });
      onDone();
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not save the details'),
  });

  const digits = number.replace(/[\s-]/g, '');
  const numberLooksWrong = digits !== '' && !/^\d+$/.test(digits);

  return (
    <div className="border-border bg-surface-sunken/50 mt-3 space-y-3 rounded-lg border border-dashed p-3">
      <Input
        label="Account name"
        autoFocus
        required
        placeholder="HDFC Current"
        value={accountName}
        onChange={(event) => setName(event.target.value)}
        disabled={isLoading}
        hint="What this account is called everywhere in the app. Rename it freely - the seeded name is only a placeholder."
      />
      <div className="grid gap-3 sm:grid-cols-3">
        <Input
          label="Bank name"
          placeholder="HDFC Bank"
          value={bank}
          onChange={(event) => setBankName(event.target.value)}
          disabled={isLoading}
        />
        <Input
          label="Account holder"
          placeholder="Jhon Doe"
          value={holder}
          onChange={(event) => setHolderName(event.target.value)}
          disabled={isLoading}
        />
        <Input
          label="Account number"
          /* Off, like the card field. An account number is not something to invite the
             browser to remember, even though the server does keep it. */
          autoComplete="off"
          inputMode="numeric"
          placeholder="50100123454321"
          value={number}
          onChange={(event) => setAccountNumber(event.target.value)}
          disabled={isLoading}
          className="tabular-nums"
          error={numberLooksWrong ? 'Digits only.' : undefined}
          hint={isLoading ? 'Loading…' : 'Stored encrypted. Clear it to remove it.'}
        />
      </div>
      <div className="flex flex-wrap items-center justify-end gap-2">
        <Button variant="ghost" onClick={onDone} disabled={save.isPending}>
          Cancel
        </Button>
        <Button
          onClick={() => save.mutate()}
          disabled={isLoading || accountName.trim() === '' || numberLooksWrong || save.isPending}
        >
          {save.isPending ? 'Saving…' : 'Save details'}
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// One card
// ---------------------------------------------------------------------------
function CardRow({ card }: { card: PaymentCard }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);

  const remove = useMutation({
    mutationFn: () => billingApi.deleteCard(card.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['billing-cards'] });
      void queryClient.invalidateQueries({ queryKey: ['money-accounts'] });
      void queryClient.invalidateQueries({ queryKey: ['billing-options'] });
      // A credit card's liability account goes with it, so the chart has changed.
      void queryClient.invalidateQueries({ queryKey: ['accounts'] });
      void queryClient.invalidateQueries({ queryKey: ['trial-balance'] });
      toast.success(`Deleted ${card.label}`);
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not delete the card'),
  });

  const toggle = useMutation({
    mutationFn: () =>
      card.is_active ? billingApi.archiveCard(card.id) : billingApi.restoreCard(card.id),
    onSuccess: (updated) => {
      void queryClient.invalidateQueries({ queryKey: ['billing-cards'] });
      void queryClient.invalidateQueries({ queryKey: ['billing-options'] });
      toast.success(updated.is_active ? `Restored ${updated.label}` : `Archived ${updated.label}`, {
        description: updated.is_active
          ? 'It can be chosen when recording a payment again.'
          : 'Past entries still name it; it is no longer offered.',
      });
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not update the card'),
  });

  return (
    <li className={cn('py-3', !card.is_active && 'opacity-60')}>
      <div className="flex items-center gap-3">
        <span className="text-content-muted shrink-0" aria-hidden>
          <CreditCard className="h-4 w-4" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="text-content block truncate text-[14px]">
            {card.label} <span className="text-content-muted tabular-nums">··{card.last4}</span>
          </span>
          <span className="text-content-muted block truncate text-[12px]">
            {[NETWORK_LABELS[card.network], card.holder_name, card.account_name]
              .filter(Boolean)
              .join(' · ')}
          </span>
        </span>
        <Badge tone={card.kind === 'credit' ? 'warning' : 'info'}>
          {card.kind === 'credit' ? 'Credit' : 'Debit'}
        </Badge>
        <Button
          variant="ghost"
          onClick={() => toggle.mutate()}
          disabled={toggle.isPending}
          title={
            card.is_active
              ? 'Stop offering this card. Past entries still name it.'
              : 'Offer this card again when recording a payment.'
          }
        >
          {card.is_active ? (
            <Archive className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <RotateCcw className="h-3.5 w-3.5" aria-hidden />
          )}
          {card.is_active ? 'Archive' : 'Restore'}
          <span className="sr-only"> {card.label}</span>
        </Button>
        <Button variant="ghost" onClick={() => setEditing((open) => !open)}>
          {editing ? 'Close' : 'Edit'}
          <span className="sr-only"> {card.label}</span>
        </Button>
        {/* Disabled rather than hidden, with the reason on hover - the same treatment as an
            account row, so the two never look like they follow different rules. */}
        <Hint
          text={card.delete_blocked_reason ?? 'Delete this card. Nothing has been recorded on it.'}
        >
          <Button
            variant="ghost"
            onClick={() => {
              if (
                window.confirm(
                  `Delete ${card.label} ··${card.last4}? This cannot be undone. Nothing ` +
                    'has been recorded on it, so no entries are affected.',
                )
              ) {
                remove.mutate();
              }
            }}
            disabled={!card.can_delete || remove.isPending}
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden />
            Delete
            <span className="sr-only"> {card.label}</span>
          </Button>
        </Hint>
      </div>
      {editing && <CardDetailsForm card={card} onDone={() => setEditing(false)} />}
    </li>
  );
}

/**
 * Correct a card's name, its holder, or its number.
 *
 * **No "kind" field.** A credit card owns a liability account; a debit card points at a bank
 * account that already existed. Switching would either orphan an account with postings
 * against it or start filing card spending as money leaving a bank account that never lost
 * it. The honest correction is a new card and an archive of the wrong one.
 *
 * The number field starts **empty rather than pre-filled**, because there is nothing to
 * pre-fill it with - the number was discarded when the card was added, and only the last
 * four digits survive. Left blank it is not sent, and the stored digits stay as they are.
 */
function CardDetailsForm({ card, onDone }: { card: PaymentCard; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [label, setLabel] = useState(card.label);
  const [holderName, setHolderName] = useState(card.holder_name ?? '');
  const [number, setNumber] = useState('');

  const digits = normaliseCardNumber(number);
  const problem = cardNumberProblem(digits);

  const save = useMutation({
    mutationFn: () =>
      billingApi.updateCard(card.id, {
        label: label.trim(),
        holder_name: holderName.trim(),
        ...(digits ? { card_number: number } : {}),
      }),
    onSuccess: (updated) => {
      // Cleared first: the number has done its only job.
      setNumber('');
      void queryClient.invalidateQueries({ queryKey: ['billing-cards'] });
      void queryClient.invalidateQueries({ queryKey: ['billing-options'] });
      toast.success(`Saved ${updated.label} ··${updated.last4}`);
      onDone();
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not save the card'),
  });

  return (
    <div className="border-border bg-surface-sunken/50 mt-3 space-y-3 rounded-lg border border-dashed p-3">
      <div className="grid gap-3 sm:grid-cols-3">
        <Input
          label="Name this card"
          autoFocus
          required
          placeholder="HDFC Millennia"
          value={label}
          onChange={(event) => setLabel(event.target.value)}
        />
        <Input
          label="Name on the card"
          autoComplete="off"
          placeholder="Jhon Doe"
          value={holderName}
          onChange={(event) => setHolderName(event.target.value)}
          hint="Clear it to remove it."
        />
        <Input
          label="Card number"
          autoComplete="off"
          inputMode="numeric"
          placeholder={`Currently ··${card.last4}`}
          value={number}
          onChange={(event) => setNumber(event.target.value)}
          className="tabular-nums"
          error={problem ?? undefined}
          hint="Leave blank to keep the current one. Only the last four digits are stored."
        />
      </div>
      <div className="flex flex-wrap items-center justify-end gap-2">
        <Button variant="ghost" onClick={onDone} disabled={save.isPending}>
          Cancel
        </Button>
        <Button
          onClick={() => save.mutate()}
          disabled={label.trim() === '' || problem !== null || save.isPending}
        >
          {save.isPending ? 'Saving…' : 'Save card'}
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Adding
// ---------------------------------------------------------------------------
function NewAccountCard({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [kind, setKind] = useState<MoneyKind>('bank');
  const [bankName, setBankName] = useState('');
  const [holderName, setHolderName] = useState('');
  const [accountNumber, setAccountNumber] = useState('');

  const create = useMutation({
    mutationFn: () =>
      billingApi.createMoneyAccount(
        name.trim(),
        kind,
        kind === 'bank'
          ? {
              ...(bankName.trim() ? { bank_name: bankName.trim() } : {}),
              ...(holderName.trim() ? { holder_name: holderName.trim() } : {}),
              ...(accountNumber.trim() ? { account_number: accountNumber.trim() } : {}),
            }
          : undefined,
      ),
    onSuccess: (account) => {
      void queryClient.invalidateQueries({ queryKey: ['billing-options'] });
      void queryClient.invalidateQueries({ queryKey: ['accounts'] });
      toast.success(`Added "${account.name}"`, { description: account.bank_name ?? undefined });
      onDone();
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not add the account'),
  });

  const digits = accountNumber.replace(/[\s-]/g, '');
  const numberLooksWrong = digits !== '' && !/^\d+$/.test(digits);
  const canSave = name.trim() !== '' && !numberLooksWrong;

  return (
    <Card className="mb-4">
      <CardHeader
        title="New account"
        description="A second bank, a UPI wallet, a partner's petty cash."
      />
      <CardBody>
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (canSave) create.mutate();
          }}
        >
          <div className="grid gap-3 sm:grid-cols-[1fr_12rem]">
            <Input
              label="Account name"
              autoFocus
              required
              placeholder="Name of the account"
              value={name}
              onChange={(event) => setName(event.target.value)}
              hint="What you call it on this screen."
            />
            <Select
              label="Behaves like"
              value={kind}
              onChange={(event) => setKind(event.target.value as MoneyKind)}
              options={[
                { value: 'bank', label: 'A bank account' },
                { value: 'cash', label: 'Cash in hand' },
              ]}
              /* The distinction is how it gets checked, not what it is called: cash
                 against a physical count, a bank against a statement. */
              hint={kind === 'bank' ? 'Checked against a statement' : 'Checked by counting'}
            />
          </div>

          {/* Only for a bank. Cash has no bank, number or holder, so the fields go away
              rather than sitting there disabled forever. */}
          {kind === 'bank' && (
            <div className="grid gap-3 sm:grid-cols-3">
              <Input
                label="Bank name"
                placeholder="HDFC Bank"
                value={bankName}
                onChange={(event) => setBankName(event.target.value)}
                hint="Optional."
              />
              <Input
                label="Account holder"
                placeholder="Jhon Doe"
                value={holderName}
                onChange={(event) => setHolderName(event.target.value)}
                hint="Optional - whose account it is."
              />
              <Input
                label="Account number"
                autoComplete="off"
                inputMode="numeric"
                placeholder="50100123454321"
                value={accountNumber}
                onChange={(event) => setAccountNumber(event.target.value)}
                className="tabular-nums"
                error={numberLooksWrong ? 'Digits only.' : undefined}
                hint="Optional. Stored encrypted."
              />
            </div>
          )}

          <div className="border-border flex items-center justify-end gap-2 border-t pt-3">
            <Button type="button" variant="ghost" onClick={onDone} disabled={create.isPending}>
              Cancel
            </Button>
            <Button type="submit" disabled={!canSave || create.isPending}>
              {create.isPending ? 'Adding…' : 'Add account'}
            </Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}

/**
 * Register a card from its number.
 *
 * **The number is never stored, here or on the server.** It lives in state while the form
 * is open, goes up once, and is cleared on success.
 */
function NewCardCard({ banks, onDone }: { banks: MoneyAccount[]; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [label, setLabel] = useState('');
  const [kind, setKind] = useState<CardKind>('credit');
  const [number, setNumber] = useState('');
  const [holderName, setHolderName] = useState('');
  const [bankId, setBankId] = useState(banks[0]?.id ?? '');

  const digits = normaliseCardNumber(number);
  // Length, charset, and the Luhn check digit - all blocking. See `cards.ts`.
  const problem = cardNumberProblem(digits);

  const add = useMutation({
    mutationFn: () =>
      billingApi.addCard({
        label: label.trim(),
        kind,
        card_number: number,
        ...(holderName.trim() ? { holder_name: holderName.trim() } : {}),
        ...(kind === 'debit' && bankId ? { bank_account_id: bankId } : {}),
      }),
    onSuccess: (card) => {
      // Cleared first, before anything can await: the number has done its only job.
      setNumber('');
      setLabel('');
      setHolderName('');

      void queryClient.invalidateQueries({ queryKey: ['billing-cards'] });
      void queryClient.invalidateQueries({ queryKey: ['billing-options'] });
      // A credit card creates a liability account, so the chart of accounts has changed.
      void queryClient.invalidateQueries({ queryKey: ['accounts'] });
      void queryClient.invalidateQueries({ queryKey: ['trial-balance'] });

      toast.success(`Added ${card.label} ··${card.last4}`, {
        description:
          card.kind === 'credit'
            ? `${NETWORK_LABELS[card.network]} credit card. What you spend on it is money owed.`
            : `${NETWORK_LABELS[card.network]} debit card on ${card.account_name}.`,
      });
      onDone();
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not add the card'),
  });

  const needsBank = kind === 'debit';
  const canSave = label.trim() !== '' && problem === null && (!needsBank || bankId !== '');

  return (
    <Card className="mb-4">
      <CardHeader
        title="New card"
        description="Only the network and the last four digits are kept. The number itself is never stored."
      />
      <CardBody>
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (canSave) add.mutate();
          }}
        >
          <div className="grid gap-3 sm:grid-cols-[1fr_12rem]">
            <Input
              label="Name this card"
              autoFocus
              required
              placeholder="HDFC Millennia"
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              hint="How you refer to it. Shown with the last four digits."
            />
            <Select
              label="Kind"
              value={kind}
              onChange={(event) => setKind(event.target.value as CardKind)}
              options={[
                { value: 'credit', label: 'Credit card' },
                { value: 'debit', label: 'Debit card' },
              ]}
              hint={
                kind === 'credit'
                  ? 'Spending on it is money you owe.'
                  : 'Spends from a bank account you already have.'
              }
            />
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <Input
              label="Card number"
              required
              autoComplete="off"
              inputMode="numeric"
              placeholder="0000 0000 0000 0000"
              value={number}
              onChange={(event) => setNumber(event.target.value)}
              className="tabular-nums"
              error={problem ?? undefined}
              hint={`${MIN_DIGITS} to ${MAX_DIGITS} digits. Used to work out the network and last four, then discarded.`}
            />
            <Input
              label="Name on the card"
              autoComplete="off"
              placeholder="Jhon Doe"
              value={holderName}
              onChange={(event) => setHolderName(event.target.value)}
              hint="Optional. Kept as typed - unlike the number."
            />
          </div>

          {needsBank && (
            <Select
              label="Draws on"
              value={bankId}
              onChange={(event) => setBankId(event.target.value)}
              options={banks.map((account) => ({ value: account.id, label: account.name }))}
              placeholder={banks.length === 0 ? 'No bank accounts yet' : undefined}
              hint="A debit card spends from an account you already have, so it gets no account of its own."
              error={banks.length === 0 ? 'Add a bank account first.' : undefined}
            />
          )}

          <div className="border-border flex items-center justify-end gap-2 border-t pt-3">
            <Button type="button" variant="ghost" onClick={onDone} disabled={add.isPending}>
              Cancel
            </Button>
            <Button type="submit" disabled={!canSave || add.isPending}>
              {add.isPending ? 'Adding…' : 'Add card'}
            </Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}
