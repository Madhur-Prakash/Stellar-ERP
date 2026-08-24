/**
 * Accounts and cards - where money sits, and what it moves through.
 *
 * Split out of `BillingPage` because it answers a different question. That screen is the
 * fast path for recording a movement; this is the occasional bit of setup that makes the
 * pickers on it useful. Someone opens it when a new card arrives, not six times a day.
 *
 * Two things here are worth knowing before changing anything:
 *
 * 1. **A card number is typed, sent once, and gone.** It lives in a `useState` for as
 *    long as the form is open and is cleared the moment the request succeeds. The response
 *    has no field for it, nothing caches it, and `autoComplete` is off so the browser is
 *    not invited to keep it either. Only the network and the last four digits come back.
 * 2. **A credit card is a liability, not a place you have money.** It shows in the "paid
 *    from" picker because you genuinely can pay with it, but it is never totalled beside a
 *    bank balance, because the number means the opposite thing.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Archive, ArrowLeftRight, CreditCard, Landmark, RotateCcw, Wallet } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card as UICard, CardBody, CardHeader } from '@/components/ui/Card';
import { InfoTip } from '@/components/ui/InfoTip';
import { Input } from '@/components/ui/Input';
import { NumberInput } from '@/components/ui/NumberInput';
import { Select } from '@/components/ui/Select';
import { transferableAccounts } from '@/features/billing/accountPicker';
import { NETWORK_LABELS, cardNumberProblem, normaliseCardNumber } from '@/features/billing/cards';
import {
  type BillingOptions,
  type Card,
  type CardKind,
  type MoneyAccount,
  billingApi,
} from '@/features/billing/api';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';
import { formatMoney } from '@/lib/format';

// ---------------------------------------------------------------------------
// Transfer
// ---------------------------------------------------------------------------

/**
 * Move money between two of your own accounts.
 *
 * **There is no category field, and that is not an omission.** Moving your own money is
 * neither earning it nor spending it, so there is no income or expense line for it to go
 * against - which is also why a transfer is left out of the money-in and money-out totals.
 * Counting one would show income that never came from anywhere and an expense that bought
 * nothing.
 */
export function TransferForm({
  options,
  onClose,
}: {
  options: BillingOptions;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const accounts = transferableAccounts(options.money_accounts);

  const defaultFrom = accounts.find((account) => account.is_default)?.id ?? accounts[0]?.id ?? '';

  const [fromId, setFromId] = useState(defaultFrom);
  // Defaulted too, rather than left blank. **The first account that is not the "from"**,
  // because the one thing a transfer cannot be is an account to itself - seeding both sides
  // with the default account would open the form already invalid.
  const [toId, setToId] = useState(
    accounts.find((account) => account.id !== defaultFrom)?.id ?? '',
  );
  const [amount, setAmount] = useState('');
  const [entryDate, setEntryDate] = useState(options.today);
  const [description, setDescription] = useState('');

  const transfer = useMutation({
    mutationFn: () =>
      billingApi.transfer({
        from_account_id: fromId,
        to_account_id: toId,
        amount,
        entry_date: entryDate,
        ...(description.trim() ? { description: description.trim() } : {}),
      }),
    onSuccess: (result) => {
      // The ledger changed, so every balance derived from it is stale - but the day book
      // and its totals are not, because a transfer is deliberately excluded from both.
      void queryClient.invalidateQueries({ queryKey: ['analytics-dashboard'] });
      void queryClient.invalidateQueries({ queryKey: ['trial-balance'] });
      void queryClient.invalidateQueries({ queryKey: ['accounts'] });

      toast.success(`Moved ${formatMoney(result.amount, options.currency)}`, {
        description: `${result.from_account_name} → ${result.to_account_name}`,
      });

      setAmount('');
      setDescription('');
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not make the transfer'),
  });

  const parsed = Number(amount);
  const sameAccount = fromId !== '' && fromId === toId;
  const canSave =
    fromId !== '' && toId !== '' && !sameAccount && Number.isFinite(parsed) && parsed > 0;

  const accountOptions = accounts.map((account) => ({ value: account.id, label: account.name }));

  return (
    <UICard className="mb-4">
      <CardHeader
        title="Move money between accounts"
        description="A transfer is not income or an expense, so it does not appear in your money-in and money-out totals."
        action={
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
        }
      />
      <CardBody>
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (canSave) transfer.mutate();
          }}
        >
          <div className="grid items-end gap-3 sm:grid-cols-[1fr_auto_1fr]">
            <Select
              label="From"
              value={fromId}
              onChange={(event) => {
                const next = event.target.value;
                setFromId(next);
                // Move "to" out of the way rather than letting the form sit on an error the
                // user did not make: choosing the account that happened to be the
                // destination is a normal thing to do, not a mistake to be told about.
                if (next === toId) {
                  setToId(accounts.find((account) => account.id !== next)?.id ?? '');
                }
              }}
              options={accountOptions}
              hint="The account the money leaves."
            />
            <span className="text-content-muted hidden pb-2.5 sm:block" aria-hidden title="to">
              <ArrowLeftRight className="h-4 w-4" />
            </span>
            <Select
              label="To"
              value={toId}
              onChange={(event) => setToId(event.target.value)}
              options={accountOptions}
              error={sameAccount ? 'Pick a different account.' : undefined}
              hint="The account it arrives in. Paying off a credit card goes here."
            />
          </div>

          <div className="grid gap-3 sm:grid-cols-[9rem_10rem_1fr]">
            <NumberInput
              label="Amount"
              required
              autoFocus
              placeholder="0.00"
              value={amount}
              onValueChange={setAmount}
              className="text-[15px] tabular-nums"
            />
            <Input
              label="Date"
              type="date"
              value={entryDate}
              max={options.today}
              onChange={(event) => setEntryDate(event.target.value)}
            />
            <Input
              label="Note"
              placeholder="Cash deposited at branch"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              hint="Optional - left blank, the ledger names both accounts."
            />
          </div>

          <div className="border-border flex items-center justify-between gap-3 border-t pt-3">
            <p className="text-content-muted text-[12px]">
              Posts to your books as one entry against both accounts.
            </p>
            <Button type="submit" disabled={!canSave || transfer.isPending}>
              {transfer.isPending ? 'Moving…' : 'Move money'}
            </Button>
          </div>
        </form>
      </CardBody>
    </UICard>
  );
}

// ---------------------------------------------------------------------------
// The panel
// ---------------------------------------------------------------------------

/**
 * Everything registered, and the forms to add to it.
 *
 * Placed below the day book on purpose: it is setup, and the screen's job is recording.
 */
export function AccountsPanel({ options }: { options: BillingOptions }) {
  const [showArchived, setShowArchived] = useState(false);
  const [addingCard, setAddingCard] = useState(false);

  // Archived cards are fetched separately rather than folded into `billing-options`,
  // because that payload feeds the pickers and an archived card must never reach one.
  const { data: cards } = useQuery({
    queryKey: ['billing-cards', showArchived],
    queryFn: () => billingApi.cards(showArchived ? { include_archived: true } : undefined),
  });

  const listed = cards ?? options.cards;
  const accounts = transferableAccounts(options.money_accounts).filter(
    (account) => !account.card_id,
  );
  const banks = accounts.filter((account) => account.kind === 'bank');

  return (
    <UICard className="mt-4">
      <CardHeader
        title="Accounts & cards"
        description="Where your money sits, and the cards you spend on. These are the choices offered when recording a payment."
        action={
          <Button variant="secondary" onClick={() => setAddingCard((open) => !open)}>
            {addingCard ? 'Close' : 'Add a card'}
          </Button>
        }
      />
      <CardBody className="space-y-5">
        {addingCard && (
          <AddCardForm
            banks={banks}
            onCancel={() => setAddingCard(false)}
            onAdded={() => setAddingCard(false)}
          />
        )}

        <section>
          <h3 className="text-content-secondary mb-2 flex items-center gap-1.5 text-[13px] font-medium">
            Cash &amp; bank
            <InfoTip label="Cash and bank">
              <p>
                An account number is stored <strong>encrypted</strong>, and stored in full - unlike
                a card number, which is never kept at all. You need the account number to be paid
                and to match a statement, so keeping only four digits would make it useless.
              </p>
              <p className="mt-2">Lists show the last four digits only.</p>
            </InfoTip>
          </h3>
          <ul className="divide-border divide-y">
            {accounts.map((account) => (
              <AccountRow key={account.id} account={account} />
            ))}
          </ul>
        </section>

        <section>
          <div className="mb-2 flex items-center justify-between gap-2">
            <h3 className="text-content-secondary flex items-center gap-1.5 text-[13px] font-medium">
              Cards
              <InfoTip label="Cards">
                <p>
                  Only the card network and the last four digits are stored. The number you type is
                  used to work those out and is never saved.
                </p>
                <p className="mt-2">
                  A credit card is a liability, so what you spend on it is money you owe - it is
                  never added to your cash and bank balance.
                </p>
              </InfoTip>
            </h3>
            <button
              type="button"
              onClick={() => setShowArchived((shown) => !shown)}
              className="text-content-muted hover:text-content text-[12px]"
            >
              {showArchived ? 'Hide archived' : 'Show archived'}
            </button>
          </div>

          {listed.length === 0 ? (
            <p className="text-content-muted py-2 text-[13px]">
              No cards yet. Add one to record what you spend on it.
            </p>
          ) : (
            <ul className="divide-border divide-y">
              {listed.map((card) => (
                <CardRow key={card.id} card={card} />
              ))}
            </ul>
          )}
        </section>
      </CardBody>
    </UICard>
  );
}

/**
 * One cash or bank account, with its details editable in place.
 *
 * Editable here rather than only on the add form, because the account most organizations
 * actually use - "Primary Bank Account" - is created by the chart template before anyone
 * has said which bank it is. Without this it would be the only account that could never
 * carry its own details.
 */
function AccountRow({ account }: { account: MoneyAccount }) {
  const [editing, setEditing] = useState(false);
  const subtitle = [account.bank_name, account.holder_name].filter(Boolean).join(' · ');

  return (
    <li className="py-2.5">
      <div className="flex items-center gap-3">
        <span className="text-content-muted shrink-0" aria-hidden>
          {account.kind === 'cash' ? (
            <Wallet className="h-4 w-4" />
          ) : (
            <Landmark className="h-4 w-4" />
          )}
        </span>
        <span className="min-w-0 flex-1">
          <span className="text-content block truncate text-[13px]">
            {account.name}
            {account.account_number_last4 && (
              <span className="text-content-muted tabular-nums">
                {' '}
                ··{account.account_number_last4}
              </span>
            )}
          </span>
          <span className="text-content-muted block truncate text-[11px]">
            {subtitle || account.code}
          </span>
        </span>
        {account.is_default && <Badge tone="primary">Default</Badge>}
        {/* Only a bank account has details to give. Cash in hand has no bank, no number
            and no holder, so there is nothing here to open. */}
        {account.kind !== 'cash' && (
          <button
            type="button"
            onClick={() => setEditing((open) => !open)}
            className="text-content-muted hover:text-content text-[12px]"
          >
            {editing ? 'Close' : account.bank_name ? 'Edit' : 'Add details'}
          </button>
        )}
      </div>

      {editing && <BankDetailsForm account={account} onDone={() => setEditing(false)} />}
    </li>
  );
}

/**
 * Which bank, whose name, which number.
 *
 * Loads the existing values first, **including the full account number** - this is the one
 * place it is fetched, and it is fetched because the alternative is an edit form that
 * silently wipes a number the user cannot see. Saving `PUT`s the whole set, so clearing a
 * field clears it on the server.
 */
function BankDetailsForm({ account, onDone }: { account: MoneyAccount; onDone: () => void }) {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['bank-details', account.id],
    queryFn: () => billingApi.bankDetails(account.id),
  });

  const [bankName, setBankName] = useState<string | null>(null);
  const [holderName, setHolderName] = useState<string | null>(null);
  const [accountNumber, setAccountNumber] = useState<string | null>(null);

  // `null` means "not touched yet", so the fetched value shows through without an effect
  // syncing server state into local state - the pattern that goes wrong the moment the
  // request resolves a second time.
  const bank = bankName ?? data?.bank_name ?? '';
  const holder = holderName ?? data?.holder_name ?? '';
  const number = accountNumber ?? data?.account_number ?? '';

  const save = useMutation({
    mutationFn: () =>
      // Empty fields are omitted, not sent as `""`. Both mean "cleared" to a `PUT`, but
      // the account number has a minimum length, so an empty string comes back as a
      // validation error instead of removing the number.
      billingApi.saveBankDetails(account.id, {
        ...(bank.trim() ? { bank_name: bank.trim() } : {}),
        ...(holder.trim() ? { holder_name: holder.trim() } : {}),
        ...(number.trim() ? { account_number: number.trim() } : {}),
      }),
    onSuccess: (saved) => {
      void queryClient.invalidateQueries({ queryKey: ['bank-details', account.id] });
      // The picker builds its subtitle from this payload.
      void queryClient.invalidateQueries({ queryKey: ['billing-options'] });
      toast.success(`Saved details for ${account.name}`, {
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
    <div className="border-border bg-surface-sunken/50 mt-2 space-y-3 rounded-lg border border-dashed p-3">
      <div className="grid gap-2 sm:grid-cols-3">
        <Input
          label="Bank name"
          autoFocus
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
        <Button type="button" variant="ghost" onClick={onDone} disabled={save.isPending}>
          Cancel
        </Button>
        <Button
          type="button"
          onClick={() => save.mutate()}
          disabled={isLoading || numberLooksWrong || save.isPending}
        >
          {save.isPending ? 'Saving…' : 'Save details'}
        </Button>
      </div>
    </div>
  );
}

function CardRow({ card }: { card: Card }) {
  const queryClient = useQueryClient();

  const toggle = useMutation({
    mutationFn: () =>
      card.is_active ? billingApi.archiveCard(card.id) : billingApi.restoreCard(card.id),
    onSuccess: (updated) => {
      void queryClient.invalidateQueries({ queryKey: ['billing-cards'] });
      // The pickers come from this payload, and an archived card must leave them.
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
    <li className={cn('flex items-center gap-3 py-2.5', !card.is_active && 'opacity-60')}>
      <span className="text-content-muted shrink-0" aria-hidden>
        <CreditCard className="h-4 w-4" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="text-content block truncate text-[13px]">
          {card.label} <span className="text-content-muted tabular-nums">··{card.last4}</span>
        </span>
        <span className="text-content-muted block truncate text-[11px]">
          {[NETWORK_LABELS[card.network], card.holder_name, card.account_name]
            .filter(Boolean)
            .join(' · ')}
        </span>
      </span>
      <Badge tone={card.kind === 'credit' ? 'warning' : 'info'}>
        {card.kind === 'credit' ? 'Credit' : 'Debit'}
      </Badge>
      {/* A real Button, not a bare muted `<button>`. As plain grey text this read as a
          caption rather than a control - and it sits beside a "Show archived" toggle, so the
          screen appeared to offer archived cards with no way to archive one. Restore was
          worse still: an unlabelled icon with the word only in a screen-reader span. */}
      <Button
        variant="ghost"
        disabled={toggle.isPending}
        onClick={() => toggle.mutate()}
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
    </li>
  );
}

/**
 * Register a card from its number.
 *
 * **The number is never stored, here or on the server.** It is held in state while the
 * form is open, sent once, and cleared on success. `autoComplete="off"` keeps the browser
 * from offering to remember it, which is the one place a "helpful" default would undo the
 * whole arrangement.
 */
function AddCardForm({
  banks,
  onCancel,
  onAdded,
}: {
  banks: MoneyAccount[];
  onCancel: () => void;
  onAdded: () => void;
}) {
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
            ? `${NETWORK_LABELS[card.network]} credit card. What you spend on it is recorded as money owed.`
            : `${NETWORK_LABELS[card.network]} debit card on ${card.account_name}.`,
      });
      onAdded();
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not add the card'),
  });

  const needsBank = kind === 'debit';
  const canSave = label.trim() !== '' && problem === null && (!needsBank || bankId !== '');

  return (
    <form
      className="border-border bg-surface-sunken/50 space-y-3 rounded-lg border border-dashed p-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (canSave) add.mutate();
      }}
    >
      <div className="grid gap-3 sm:grid-cols-[1fr_11rem]">
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
          /* Off, not "cc-number". The browser filling or storing a card number is exactly
             what this design avoids - nothing here keeps one, so nothing should offer to. */
          autoComplete="off"
          inputMode="numeric"
          placeholder="0000 0000 0000 0000"
          value={number}
          onChange={(event) => setNumber(event.target.value)}
          className="tabular-nums"
          error={problem ?? undefined}
          hint={
            'Only the network and the last four digits are kept. The number itself is never stored.'
          }
        />
        <Input
          label="Name on the card"
          /* Off as well. This one *is* stored, but it sits beside the number field and
             letting the browser treat this form as a saved-card form is the thing to
             avoid - it is what would offer to fill, and keep, the number next to it. */
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

      <div className="flex flex-wrap items-center justify-end gap-2">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={add.isPending}>
          Cancel
        </Button>
        <Button type="submit" disabled={!canSave || add.isPending}>
          {add.isPending ? 'Adding…' : 'Add card'}
        </Button>
      </div>
    </form>
  );
}
