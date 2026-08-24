import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/format.dart';
import '../../core/locale_settings.dart';
import '../../models/billing.dart';
import '../../models/page.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../../theme/app_theme.dart';
import '../../theme/oklch.dart';
import '../../theme/tokens.dart';
import '../../widgets/app_badge.dart';
import '../../widgets/app_button.dart';
import '../../widgets/app_card.dart';
import '../../widgets/app_input.dart';
import '../../widgets/app_modal.dart';
import '../../widgets/app_select.dart';
import '../../widgets/data_table.dart';
import '../../widgets/info_tip.dart';
import '../../widgets/metric_tile.dart';
import '../../widgets/primitives.dart';
import '../../widgets/toast.dart';
import 'accounts_panel.dart';

/// Billing - the fast path for recording money.
///
/// This is the screen most users will only ever open, so the design target is **seconds,
/// not minutes**: two buttons, an amount, a note, done. The date defaults to today, the
/// category and the cash account default to sensible choices, and the amount field takes
/// focus so the whole entry is typeable without touching the mouse.
///
/// The form stays open after saving. Someone catching up on a week of receipts enters six
/// things in a row, and closing after each one would make them press "Money out" six times.
///
/// Every entry posts real double-entry to the ledger, which is why the figures show up on
/// the dashboard, in the P&L, and in the analytics trend without anything else being
/// wired up.
class BillingScreen extends ConsumerStatefulWidget {
  const BillingScreen({super.key});

  @override
  ConsumerState<BillingScreen> createState() => _BillingScreenState();
}

/// Which form is open.
///
/// [transfer] sits alongside the two directions rather than under them, because moving
/// your own money is a third thing and not a variety of either - it has no category and
/// no counterparty, and it stays out of the money-in and money-out totals.
enum _Action {
  moneyIn(Direction.in_),
  moneyOut(Direction.out),
  transfer(null);

  const _Action(this.direction);

  /// The direction this records, or null for a transfer.
  final Direction? direction;
}

class _BillingScreenState extends ConsumerState<BillingScreen> {
  _Action? _composing;
  BillingQuery _query = const BillingQuery();
  final TextEditingController _search = TextEditingController();

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final BillingOptions? options = ref
        .watch(billingOptionsProvider)
        .valueOrNull;
    final BillingSummary? summary = ref
        .watch(billingSummaryProvider)
        .valueOrNull;
    final AsyncValue<Paged<BillingEntry>> entries = ref.watch(
      billingEntriesProvider(_query),
    );
    final String currency = options?.currency ?? localeSettings().currency;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        const PageHeader(
          title: 'Billing',
          description:
              'Record money coming in and going out. Each entry posts straight to your '
              'books, so the dashboard and reports update immediately.',
        ),

        // The actions, given the prominence they deserve - this is the reason the
        // screen exists, not a toolbar afterthought. Transfer is third and visually
        // quieter: it is a real thing people do, but it is not why they opened this.
        LayoutBuilder(
          builder: (BuildContext context, BoxConstraints constraints) {
            final bool side = constraints.maxWidth >= 760;
            final List<Widget> buttons = <Widget>[
              for (final _Action action in _Action.values)
                _ActionButton(
                  action: action,
                  active: _composing == action,
                  onTap: () => setState(
                    () => _composing = _composing == action ? null : action,
                  ),
                ),
            ];
            return side
                ? Row(
                    spacing: 12,
                    children: buttons
                        .map((Widget b) => Expanded(child: b))
                        .toList(growable: false),
                  )
                : Column(spacing: 12, children: buttons);
          },
        ),
        const SizedBox(height: 16),

        if (_composing == _Action.transfer && options != null) ...<Widget>[
          TransferForm(
            options: options,
            onClose: () => setState(() => _composing = null),
          ),
          const SizedBox(height: 16),
        ],

        if (_composing?.direction != null && options != null) ...<Widget>[
          _EntryForm(
            // Rebuilding on direction change is what keeps the category selection
            // correct. Switching from money-out to money-in changes which categories are
            // valid, and syncing that after the fact is the "you might not need an
            // effect" anti-pattern - a fresh key just initialises it right.
            key: ValueKey<Direction>(_composing!.direction!),
            direction: _composing!.direction!,
            options: options,
            onClose: () => setState(() => _composing = null),
          ),
          const SizedBox(height: 16),
        ],

        // ---- Totals ----
        TileGrid(
          maxColumns: 3,
          children: <Widget>[
            MetricTile(
              label: 'Money in',
              value: summary == null
                  ? null
                  : formatMoney(summary.moneyIn, currency: currency),
              icon: LucideIcons.arrowDownLeft,
              valueTone: context.tokens.success,
              valueSize: 20,
            ),
            MetricTile(
              label: 'Money out',
              value: summary == null
                  ? null
                  : formatMoney(summary.moneyOut, currency: currency),
              icon: LucideIcons.arrowUpRight,
              valueTone: context.tokens.danger,
              valueSize: 20,
            ),
            MetricTile(
              label: 'Net',
              value: summary == null
                  ? null
                  : formatMoney(summary.net, currency: currency),
              icon: LucideIcons.wallet,
              valueSize: 20,
              hint: summary == null
                  ? null
                  : '${summary.entryCount} '
                        '${summary.entryCount == 1 ? 'entry' : 'entries'} · '
                        '${formatDate(summary.fromDate)} to ${formatDate(summary.toDate)}',
              info: <Widget>[
                infoText(
                  'Money in less money out, for the entries on this screen only. Not the '
                  'same as profit, which also counts invoices and bills.',
                ),
              ],
            ),
          ],
        ),
        const SizedBox(height: 16),

        _EntryList(
          entries: entries,
          currency: currency,
          query: _query,
          search: _search,
          onQueryChanged: (BillingQuery next) => setState(() => _query = next),
        ),

        if (options != null) ...<Widget>[
          const SizedBox(height: 16),
          AccountsPanel(options: options),
        ],
      ],
    );
  }
}

// =============================================================================
// The three buttons
// =============================================================================
class _ActionButton extends StatefulWidget {
  const _ActionButton({
    required this.action,
    required this.active,
    required this.onTap,
  });

  final _Action action;
  final bool active;
  final VoidCallback onTap;

  @override
  State<_ActionButton> createState() => _ActionButtonState();
}

class _ActionButtonState extends State<_ActionButton> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;

    // The transfer tile is deliberately neutral rather than green or red: a transfer
    // neither gains nor loses anything, and colouring it like income or an expense
    // would say that it did.
    final (
      Color tone,
      Color background,
      IconData icon,
      String title,
      String subtitle,
    ) = switch (widget.action) {
      _Action.moneyIn => (
        t.success,
        t.successBg,
        LucideIcons.arrowDownLeft,
        'Money in',
        'A sale, a receipt, money received',
      ),
      _Action.moneyOut => (
        t.danger,
        t.dangerBg,
        LucideIcons.arrowUpRight,
        'Money out',
        'A bill, an expense, money paid',
      ),
      _Action.transfer => (
        t.primary,
        t.surfaceSunken,
        LucideIcons.arrowLeftRight,
        'Transfer',
        'Between your own accounts',
      ),
    };

    final bool neutral = widget.action == _Action.transfer;

    return Semantics(
      button: true,
      expanded: widget.active,
      child: MouseRegion(
        cursor: SystemMouseCursors.click,
        onEnter: (_) => setState(() => _hovered = true),
        onExit: (_) => setState(() => _hovered = false),
        child: GestureDetector(
          onTap: widget.onTap,
          child: AnimatedContainer(
            duration: Motion.fast,
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: background,
              borderRadius: BorderRadius.circular(Radii.xl),
              border: Border.all(
                color: widget.active
                    ? tone
                    : neutral
                    ? (_hovered ? t.contentMuted.at(0.5) : t.border)
                    : _hovered
                    ? tone.at(0.6)
                    : tone.at(0.3),
                width: widget.active ? 2 : 1,
              ),
            ),
            child: Row(
              spacing: 12,
              children: <Widget>[
                Container(
                  width: 36,
                  height: 36,
                  decoration: BoxDecoration(
                    color: tone.at(neutral ? 0.1 : 0.15),
                    borderRadius: BorderRadius.circular(Radii.lg),
                  ),
                  alignment: Alignment.center,
                  child: Icon(icon, size: 18, color: tone),
                ),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(
                        title,
                        style: TextStyle(
                          fontSize: 14,
                          fontWeight: FontWeight.w600,
                          color: t.content,
                        ),
                      ),
                      Text(
                        subtitle,
                        style: TextStyle(fontSize: 12, color: t.contentMuted),
                      ),
                    ],
                  ),
                ),
                Icon(LucideIcons.plus, size: 16, color: t.contentMuted),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

// =============================================================================
// The form
// =============================================================================
class _EntryForm extends ConsumerStatefulWidget {
  const _EntryForm({
    super.key,
    required this.direction,
    required this.options,
    required this.onClose,
  });

  final Direction direction;
  final BillingOptions options;
  final VoidCallback onClose;

  @override
  ConsumerState<_EntryForm> createState() => _EntryFormState();
}

class _EntryFormState extends ConsumerState<_EntryForm> {
  final TextEditingController _amount = TextEditingController();
  final TextEditingController _description = TextEditingController();
  final TextEditingController _party = TextEditingController();
  final TextEditingController _reference = TextEditingController();
  final FocusNode _amountFocus = FocusNode();

  late String _entryDate;
  late String _categoryId;

  /// The picker *key*, not the account id - a debit card and the bank account it draws
  /// on share an id, so the widget needs something that tells them apart. Resolved back
  /// to an account id on save.
  late String _accountKey;
  bool _saving = false;

  List<Category> get _relevant => widget.options.categories
      .where((Category c) => c.direction == widget.direction)
      .toList(growable: false);

  @override
  void initState() {
    super.initState();
    _entryDate = widget.options.today;

    final List<Category> relevant = _relevant;
    _categoryId =
        relevant.where((Category c) => c.isDefault).firstOrNull?.id ??
        (relevant.isEmpty ? '' : relevant.first.id);

    final List<MoneyAccount> accounts = widget.options.moneyAccounts;
    _accountKey =
        accounts.where((MoneyAccount a) => a.isDefault).firstOrNull?.key ??
        (accounts.isEmpty ? '' : accounts.first.key);
  }

  /// The account the picker currently names, or null if the list is empty.
  MoneyAccount? get _account =>
      accountForKey(widget.options.moneyAccounts, _accountKey);

  @override
  void dispose() {
    _amount.dispose();
    _description.dispose();
    _party.dispose();
    _reference.dispose();
    _amountFocus.dispose();
    super.dispose();
  }

  /// Grouped in template order rather than alphabetically: the chart is already ordered so
  /// that trading categories come before household ones, and reordering would separate
  /// accounts that belong together.
  List<SelectGroup> get _categoryGroups {
    final List<SelectGroup> groups = <SelectGroup>[];
    for (final Category category in _relevant) {
      final SelectOption option = SelectOption(
        value: category.id,
        label: category.name,
      );
      final SelectGroup? existing = groups
          .where((SelectGroup g) => g.label == category.group)
          .firstOrNull;
      if (existing != null) {
        existing.options.add(option);
      } else {
        groups.add(
          SelectGroup(label: category.group, options: <SelectOption>[option]),
        );
      }
    }
    return groups;
  }

  bool get _canSave {
    final double amount = double.tryParse(_amount.text) ?? 0;
    return amount > 0 &&
        _description.text.trim().isNotEmpty &&
        _party.text.trim().isNotEmpty;
  }

  Future<void> _save() async {
    setState(() => _saving = true);
    try {
      final BillingEntry entry = await ref
          .read(billingApiProvider)
          .record(
            direction: widget.direction,
            amount: _amount.text,
            description: _description.text.trim(),
            party: _party.text.trim(),
            entryDate: _entryDate,
            categoryId: _categoryId,
            // The account id, not the picker key. A debit card posts to the bank
            // account it draws on, which is what makes it a way of *using* that
            // account rather than a second place the same money lives.
            moneyAccountId: _account?.id,
            reference: _reference.text.trim(),
          );

      // The figures on the dashboard and analytics come from the ledger this just wrote
      // to, so their caches are stale the moment this succeeds.
      invalidateLedger(ref);

      if (!mounted) return;
      context.toastSuccess(
        '${widget.direction.isIn ? 'Received' : 'Paid'} '
        '${formatMoney(entry.amount, currency: widget.options.currency)}',
        description: '${entry.description} · ${entry.categoryName}',
      );

      // Kept open, with the amount and note cleared and the date retained: someone
      // catching up on a week of receipts enters several in a row on the same day.
      _amount.clear();
      _description.clear();
      _party.clear();
      _reference.clear();
      _amountFocus.requestFocus();
      setState(() {});
    } catch (error) {
      if (mounted) context.toastApiError(error, 'Could not save the entry');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final bool isIn = widget.direction.isIn;
    final List<SelectGroup> groups = _categoryGroups;

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          CardHeader(
            title: isIn ? 'Record money in' : 'Record money out',
            description:
                'The date, category, and account are pre-filled. Only the amount and a '
                'note are needed.',
            action: AppButton(
              onPressed: widget.onClose,
              variant: AppButtonVariant.ghost,
              label: 'Close',
            ),
          ),
          CardBody(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              spacing: 12,
              children: <Widget>[
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  spacing: 12,
                  children: <Widget>[
                    SizedBox(
                      width: 144,
                      child: AppNumberInput(
                        label: 'Amount',
                        controller: _amount,
                        focusNode: _amountFocus,
                        required: true,
                        autofocus: true,
                        placeholder: '0.00',
                        decimals: 2,
                        textStyle: const TextStyle(
                          fontSize: 15,
                          fontFeatures: tabularFigures,
                        ),
                        onChanged: (_) => setState(() {}),
                      ),
                    ),
                    Expanded(
                      child: AppInput(
                        label: 'What was it for?',
                        controller: _description,
                        required: true,
                        placeholder: isIn ? 'Counter sale' : 'Rent for August',
                        onChanged: (_) => setState(() {}),
                      ),
                    ),
                    AppDateInput(
                      label: 'Date',
                      value: _entryDate,
                      maximum: widget.options.today,
                      width: 160,
                      onChanged: (String next) =>
                          setState(() => _entryDate = next),
                    ),
                  ],
                ),
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  spacing: 12,
                  children: <Widget>[
                    Expanded(
                      child: AppInput(
                        // Required, but still free text. Most parties a small business
                        // deals with - the auto driver, the electricity board, a walk-in
                        // buyer - are never worth a customer record, so this asks who
                        // rather than which record. Naming them is not optional: an amount
                        // whose counterparty is blank is nearly as unidentifiable a month
                        // later as one with no description.
                        label: isIn ? 'From' : 'To',
                        controller: _party,
                        required: true,
                        placeholder: isIn ? 'Walk-in customer' : 'Airtel',
                        hint: isIn
                            ? 'Who the money came from.'
                            : 'Who the money went to.',
                        onChanged: (_) => setState(() {}),
                      ),
                    ),
                    Expanded(
                      child: AppInput(
                        label: 'Reference',
                        controller: _reference,
                        placeholder: 'Cheque or bill no.',
                        hint: 'Optional - a cheque, UPI, or bill number.',
                      ),
                    ),
                  ],
                ),
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  spacing: 12,
                  children: <Widget>[
                    Expanded(
                      child: AppSelect(
                        label: 'Category',
                        value: _categoryId,
                        // Grouped, because the list runs to nearly eighty entries once
                        // business and household categories are both present.
                        groups: groups,
                        onChanged: (String next) =>
                            setState(() => _categoryId = next),
                        action: AppTextLink(
                          label: '+ Add category',
                          fontSize: 12,
                          onTap: _addCategory,
                        ),
                        hint: groups.isEmpty
                            ? 'No categories yet - add one to continue.'
                            : null,
                        error: groups.isEmpty ? ' ' : null,
                      ),
                    ),
                    Expanded(
                      child: AppSelect(
                        label: isIn ? 'Received into' : 'Paid from',
                        value: _accountKey,
                        // Grouped into "Cash & bank" and "Cards", because the two are
                        // not the same kind of thing - one is money you have, the other
                        // can be money you owe.
                        groups: moneyAccountGroups(
                          widget.options.moneyAccounts,
                        ),
                        onChanged: (String next) =>
                            setState(() => _accountKey = next),
                        action: AppTextLink(
                          label: '+ Add account',
                          fontSize: 12,
                          onTap: _addMoneyAccount,
                        ),
                        hint: _account?.kind.isCard ?? false
                            ? isIn
                                  ? 'A refund or credit back onto this card, reducing '
                                        'what you owe.'
                                  : 'Charged to this card - recorded as money owed, '
                                        'not money spent from your balance.'
                            : null,
                      ),
                    ),
                  ],
                ),
                Container(
                  padding: const EdgeInsets.only(top: 12),
                  decoration: BoxDecoration(
                    border: Border(top: BorderSide(color: t.border)),
                  ),
                  child: Row(
                    children: <Widget>[
                      Expanded(
                        child: Text(
                          'Saves to your books immediately. To correct a mistake, reverse '
                          'the entry.',
                          style: TextStyle(fontSize: 12, color: t.contentMuted),
                        ),
                      ),
                      AppButton(
                        onPressed: _canSave && !_saving ? _save : null,
                        loading: _saving,
                        label: _saving
                            ? 'Saving…'
                            : isIn
                            ? 'Record money in'
                            : 'Record money out',
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  /// Add a category from a name alone.
  ///
  /// The account code, parent group, and subtype are derived server-side, because
  /// requiring someone to pick "5265" and "operating_expense" in order to record a payment
  /// for tempo hire would defeat the point of this screen.
  Future<void> _addCategory() async {
    final TextEditingController name = TextEditingController();
    final bool? confirmed = await showAppModal<bool>(
      context: context,
      title: widget.direction.isIn
          ? 'New income category'
          : 'New expense category',
      description: 'Filed alongside the other categories of this kind.',
      builder: (BuildContext context) => AppInput(
        label: 'Name',
        controller: name,
        autofocus: true,
        required: true,
        placeholder: widget.direction.isIn ? 'Workshop fees' : 'Tempo hire',
        onSubmitted: (_) => Navigator.of(context).pop(true),
      ),
      footer: (BuildContext context) => <Widget>[
        AppButton(
          onPressed: () => Navigator.of(context).pop(false),
          variant: AppButtonVariant.ghost,
          label: 'Cancel',
        ),
        AppButton(
          onPressed: () => Navigator.of(context).pop(true),
          label: 'Add',
        ),
      ],
    );

    if (confirmed != true || name.text.trim().isEmpty) {
      name.dispose();
      return;
    }

    try {
      final Category created = await ref
          .read(billingApiProvider)
          .createCategory(name.text.trim(), widget.direction);
      // The options query feeds every dropdown on this screen, and the chart of accounts
      // has genuinely changed.
      ref.invalidate(billingOptionsProvider);
      ref.invalidate(accountsProvider);
      if (!mounted) return;
      setState(() => _categoryId = created.id);
      context.toastSuccess(
        'Added "${created.name}"',
        description: 'Filed under ${created.group}',
      );
    } catch (error) {
      if (mounted) context.toastApiError(error, 'Could not add the category');
    } finally {
      name.dispose();
    }
  }

  /// Add a place money can sit, and select it.
  ///
  /// The dialog itself lives in `accounts_panel.dart`, because the accounts screen offers
  /// the same thing from its own button. The only part specific to this form is the last
  /// line: having just created an account, the picker should already be pointing at it.
  Future<void> _addMoneyAccount() async {
    final MoneyAccount? created = await showAddMoneyAccountDialog(context, ref);
    if (created == null || !mounted) return;
    setState(() => _accountKey = created.key);
  }
}

// =============================================================================
// The day book
// =============================================================================
class _EntryList extends ConsumerWidget {
  const _EntryList({
    required this.entries,
    required this.currency,
    required this.query,
    required this.search,
    required this.onQueryChanged,
  });

  final AsyncValue<Paged<BillingEntry>> entries;
  final String currency;
  final BillingQuery query;
  final TextEditingController search;
  final ValueChanged<BillingQuery> onQueryChanged;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AppTokens t = context.tokens;
    final Paged<BillingEntry>? page = entries.valueOrNull;

    Future<void> reverse(BillingEntry row) async {
      final String? reason = await promptForText(
        context,
        title: 'Reverse "${row.description}"?',
        description:
            'The original stays on the record, cancelled by an opposite entry. There is '
            'no delete and no edit here.',
        placeholder: 'Reason (optional)',
        confirmLabel: 'Reverse entry',
      );
      // Null is Cancel; an empty string is "no reason given", which is fine.
      if (reason == null) return;

      try {
        await ref.read(billingApiProvider).reverse(row.id, reason: reason);
        invalidateLedger(ref);
        if (context.mounted) {
          context.toastSuccess(
            'Entry reversed',
            description:
                'The original stays on the record, cancelled by an opposite entry.',
          );
        }
      } catch (error) {
        if (context.mounted) {
          context.toastApiError(error, 'Could not reverse the entry');
        }
      }
    }

    final List<AppColumn<BillingEntry>> columns = <AppColumn<BillingEntry>>[
      AppColumn<BillingEntry>(
        header: 'Date',
        fixedWidth: 116,
        cell: (BillingEntry row) => Text(formatDate(row.date)),
      ),
      AppColumn<BillingEntry>(
        header: 'Description',
        cell: (BillingEntry row) => Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(
              row.description,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                color: t.content,
                decoration: row.isReversed ? TextDecoration.lineThrough : null,
                decorationColor: t.contentMuted,
              ),
            ),
            Text.rich(
              TextSpan(
                children: <InlineSpan>[
                  if (row.party != null) ...<InlineSpan>[
                    TextSpan(
                      text:
                          '${row.direction.isIn ? 'from' : 'to'} ${row.party}',
                      style: TextStyle(
                        color: t.contentSecondary,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                    const TextSpan(text: ' · '),
                  ],
                  TextSpan(
                    text:
                        '${row.categoryName} · ${row.moneyAccountName}'
                        '${row.reference != null ? ' · ${row.reference}' : ''}',
                  ),
                ],
              ),
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 11, color: t.contentMuted),
            ),
          ],
        ),
      ),
      AppColumn<BillingEntry>(
        header: 'In',
        numeric: true,
        cell: (BillingEntry row) => row.direction.isIn
            ? Text(
                formatMoney(row.amount, currency: currency),
                style: TextStyle(
                  color: t.success,
                  fontWeight: FontWeight.w500,
                  decoration: row.isReversed
                      ? TextDecoration.lineThrough
                      : null,
                ),
              )
            : Text('-', style: TextStyle(color: t.contentMuted)),
      ),
      AppColumn<BillingEntry>(
        header: 'Out',
        numeric: true,
        cell: (BillingEntry row) => !row.direction.isIn
            ? Text(
                formatMoney(row.amount, currency: currency),
                style: TextStyle(
                  color: t.danger,
                  fontWeight: FontWeight.w500,
                  decoration: row.isReversed
                      ? TextDecoration.lineThrough
                      : null,
                ),
              )
            : Text('-', style: TextStyle(color: t.contentMuted)),
      ),
      AppColumn<BillingEntry>(
        header: '',
        fixedWidth: 88,
        cell: (BillingEntry row) => row.isReversed
            ? const AppBadge('Reversed')
            : AppIconButton(
                icon: LucideIcons.undo2,
                tooltip: 'Reverse this entry',
                size: 14,
                onPressed: () => reverse(row),
              ),
      ),
    ];

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          CardHeader(
            title: 'Your entries',
            description:
                'Newest first. Nothing is ever deleted - a correction is an opposite entry.',
            action: Row(
              mainAxisSize: MainAxisSize.min,
              spacing: 8,
              children: <Widget>[
                Segmented(
                  active: query.direction?.wire ?? 'all',
                  segments: const <(String, String, String?)>[
                    ('all', 'All', null),
                    ('in', 'In', null),
                    ('out', 'Out', null),
                  ],
                  onChanged: (String next) => onQueryChanged(
                    BillingQuery(
                      direction: next == 'all' ? null : Direction.parse(next),
                      search: query.search,
                    ),
                  ),
                ),
                AppInput(
                  controller: search,
                  placeholder: 'Search…',
                  width: 160,
                  onChanged: (String value) => onQueryChanged(
                    BillingQuery(direction: query.direction, search: value),
                  ),
                ),
              ],
            ),
          ),
          AppDataTable<BillingEntry>(
            columns: columns,
            rows: page?.items ?? const <BillingEntry>[],
            rowKey: (BillingEntry row) => row.id,
            isLoading: entries.isLoading,
            empty: const EmptyState(
              title: 'Nothing recorded yet',
              description:
                  'Use the buttons above to record your first payment or receipt.',
            ),
          ),
          if (page != null)
            Pagination(
              page: page.meta.page,
              totalPages: page.meta.totalPages,
              totalItems: page.meta.totalItems,
              onChanged: (int next) => onQueryChanged(
                BillingQuery(
                  page: next,
                  direction: query.direction,
                  search: query.search,
                ),
              ),
            ),
        ],
      ),
    );
  }
}
