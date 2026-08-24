import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/format.dart';
import '../../core/locale_settings.dart';
import '../../models/page.dart';
import '../../models/sales.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../../theme/app_theme.dart';
import '../../theme/oklch.dart';
import '../../theme/tokens.dart';
import '../../widgets/app_badge.dart';
import '../../widgets/app_button.dart';
import '../../widgets/app_card.dart';
import '../../widgets/app_input.dart';
import '../../widgets/app_select.dart';
import '../../widgets/data_table.dart';
import '../../widgets/metric_tile.dart';
import '../../widgets/primitives.dart';
import '../../widgets/toast.dart';
import 'party_form.dart';

/// Sales - invoices, customers, payments, and receivables.
///
/// The invoice list is the default view because it is what a small business opens this
/// software to look at: who owes money, and how overdue.
class SalesScreen extends ConsumerStatefulWidget {
  const SalesScreen({super.key, this.tab});

  final String? tab;

  @override
  ConsumerState<SalesScreen> createState() => _SalesScreenState();
}

class _SalesScreenState extends ConsumerState<SalesScreen> {
  bool _composing = false;

  static const List<(String, String)> _tabs = <(String, String)>[
    ('invoices', 'Invoices'),
    ('customers', 'Customers'),
    ('payments', 'Payments'),
    ('ageing', 'Ageing'),
  ];

  String get _active {
    final bool known = _tabs.any(
      ((String, String) entry) => entry.$1 == widget.tab,
    );
    return known ? widget.tab! : 'invoices';
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        PageHeader(
          title: 'Sales',
          description:
              'Invoices post straight to the ledger. A posted invoice is a statutory record '
              'and cannot be edited.',
          action: _active == 'invoices'
              ? AppButton(
                  onPressed: () => setState(() => _composing = true),
                  leftIcon: LucideIcons.plus,
                  label: 'New invoice',
                )
              : null,
        ),
        AppTabs(
          tabs: _tabs,
          active: _active,
          onChanged: (String next) {
            // Close the composer on the way out. It belongs to the Invoices tab, and a
            // half-written invoice hovering above the customer list is confusing - worse,
            // the "New invoice" button is only on that tab, so there was no way to dismiss
            // it.
            setState(() => _composing = false);
            context.replace('/invoices?tab=$next');
          },
        ),
        if (_active == 'invoices' && _composing) ...<Widget>[
          _InvoiceComposer(onClose: () => setState(() => _composing = false)),
          const SizedBox(height: 16),
        ],
        switch (_active) {
          'customers' => const _CustomerList(),
          'payments' => const _PaymentList(),
          'ageing' => const _AgeingReport(),
          _ => const _InvoiceList(),
        },
      ],
    );
  }
}

const Map<String, BadgeTone> _statusTones = <String, BadgeTone>{
  'draft': BadgeTone.neutral,
  'posted': BadgeTone.info,
  'partially_paid': BadgeTone.warning,
  'paid': BadgeTone.success,
  'cancelled': BadgeTone.danger,
};

const Map<String, String> _statusLabels = <String, String>{
  'draft': 'Draft',
  'posted': 'Unpaid',
  'partially_paid': 'Part paid',
  'paid': 'Paid',
  'cancelled': 'Cancelled',
};

// =============================================================================
// Invoice list
// =============================================================================
class _InvoiceList extends ConsumerStatefulWidget {
  const _InvoiceList();

  @override
  ConsumerState<_InvoiceList> createState() => _InvoiceListState();
}

class _InvoiceListState extends ConsumerState<_InvoiceList> {
  InvoiceQuery _query = const InvoiceQuery();
  String? _posting;

  Future<void> _post(Invoice invoice) async {
    setState(() => _posting = invoice.id);
    try {
      final Invoice posted = await ref
          .read(salesApiProvider)
          .postInvoice(invoice.id);
      invalidateDocuments(ref);
      if (mounted) {
        context.toastSuccess('${posted.invoiceNumber} posted to the ledger');
      }
    } catch (error) {
      if (mounted) context.toastApiError(error, 'Could not post the invoice');
    } finally {
      if (mounted) setState(() => _posting = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final AsyncValue<Paged<Invoice>> invoices = ref.watch(
      invoicesProvider(_query),
    );
    final Paged<Invoice>? page = invoices.valueOrNull;
    final String currency = localeSettings().currency;

    return AppCard(
      child: Column(
        children: <Widget>[
          CardHeader(
            title: 'Invoices',
            action: CheckRow(
              value: _query.overdueOnly,
              label: 'Overdue only',
              fontSize: 12,
              onChanged: (bool next) =>
                  setState(() => _query = InvoiceQuery(overdueOnly: next)),
            ),
          ),
          AppDataTable<Invoice>(
            rows: page?.items ?? const <Invoice>[],
            rowKey: (Invoice row) => row.id,
            isLoading: invoices.isLoading,
            empty: EmptyState(
              title: _query.overdueOnly ? 'Nothing overdue' : 'No invoices yet',
              description: _query.overdueOnly
                  ? 'Every posted invoice is within its payment terms.'
                  : 'Create one to bill a customer.',
            ),
            columns: <AppColumn<Invoice>>[
              AppColumn<Invoice>(
                header: 'Invoice',
                fixedWidth: 140,
                cell: (Invoice row) => Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(row.invoiceNumber, style: monoStyle(color: t.content)),
                    Text(
                      formatDate(row.invoiceDate),
                      style: TextStyle(fontSize: 11, color: t.contentMuted),
                    ),
                  ],
                ),
              ),
              AppColumn<Invoice>(
                header: 'Customer',
                cell: (Invoice row) =>
                    Text(row.customerName, overflow: TextOverflow.ellipsis),
              ),
              AppColumn<Invoice>(
                header: 'Due',
                hideOnNarrow: true,
                fixedWidth: 156,
                cell: (Invoice row) => Text(
                  formatDate(row.dueDate) +
                      (row.isOverdue ? ' (${row.daysOverdue}d late)' : ''),
                  style: TextStyle(
                    color: row.isOverdue ? t.danger : t.content,
                    fontWeight: row.isOverdue
                        ? FontWeight.w500
                        : FontWeight.w400,
                  ),
                ),
              ),
              AppColumn<Invoice>(
                header: 'Status',
                fixedWidth: 110,
                cell: (Invoice row) => AppBadge(
                  _statusLabels[row.status] ?? row.status,
                  tone: _statusTones[row.status] ?? BadgeTone.neutral,
                ),
              ),
              AppColumn<Invoice>(
                header: 'Total',
                numeric: true,
                cell: (Invoice row) =>
                    Text(formatMoney(row.grandTotal, currency: currency)),
              ),
              AppColumn<Invoice>(
                header: 'Outstanding',
                numeric: true,
                cell: (Invoice row) => isZeroMoney(row.outstanding)
                    ? Text('-', style: TextStyle(color: t.contentMuted))
                    : Text(
                        formatMoney(row.outstanding, currency: currency),
                        style: const TextStyle(fontWeight: FontWeight.w500),
                      ),
              ),
              AppColumn<Invoice>(
                header: '',
                fixedWidth: 80,
                cell: (Invoice row) => row.status == 'draft'
                    ? AppButton(
                        onPressed: () => _post(row),
                        loading: _posting == row.id,
                        variant: AppButtonVariant.secondary,
                        size: AppButtonSize.sm,
                        label: 'Post',
                      )
                    : const SizedBox.shrink(),
              ),
            ],
          ),
          if (page != null)
            Pagination(
              page: page.meta.page,
              totalPages: page.meta.totalPages,
              totalItems: page.meta.totalItems,
              onChanged: (int next) => setState(
                () => _query = InvoiceQuery(
                  page: next,
                  overdueOnly: _query.overdueOnly,
                ),
              ),
            ),
        ],
      ),
    );
  }
}

// =============================================================================
// Invoice composer
// =============================================================================
class _DraftLine {
  _DraftLine()
    : description = TextEditingController(),
      quantity = TextEditingController(text: '1'),
      unitPrice = TextEditingController(),
      taxRate = TextEditingController(text: '18');

  final TextEditingController description;
  final TextEditingController quantity;
  final TextEditingController unitPrice;
  final TextEditingController taxRate;

  void dispose() {
    description.dispose();
    quantity.dispose();
    unitPrice.dispose();
    taxRate.dispose();
  }

  bool get isUsable =>
      description.text.trim().isNotEmpty &&
      (double.tryParse(unitPrice.text) ?? 0) > 0;

  SalesLineInput toInput() => SalesLineInput(
    description: description.text.trim(),
    quantity: quantity.text.isEmpty ? '1' : quantity.text,
    unitPrice: unitPrice.text,
    taxRate: taxRate.text.isEmpty ? '0' : taxRate.text,
  );
}

class _InvoiceComposer extends ConsumerStatefulWidget {
  const _InvoiceComposer({required this.onClose});

  final VoidCallback onClose;

  @override
  ConsumerState<_InvoiceComposer> createState() => _InvoiceComposerState();
}

class _InvoiceComposerState extends ConsumerState<_InvoiceComposer> {
  String _customerId = '';
  final List<_DraftLine> _lines = <_DraftLine>[_DraftLine()];
  bool _postNow = true;
  bool _saving = false;

  @override
  void dispose() {
    for (final _DraftLine line in _lines) {
      line.dispose();
    }
    super.dispose();
  }

  /// A local preview only.
  ///
  /// The server recomputes every figure and its answer is authoritative - this exists so
  /// the user is not typing blind, not to be the source of the totals. Doubles are fine
  /// for exactly that reason, and the caption says so.
  (double taxable, double tax, double total) get _preview {
    double taxable = 0;
    double tax = 0;
    for (final _DraftLine line in _lines) {
      final double quantity = double.tryParse(line.quantity.text) ?? 0;
      final double price = double.tryParse(line.unitPrice.text) ?? 0;
      final double rate = double.tryParse(line.taxRate.text) ?? 0;
      final double base = quantity * price;
      taxable += base;
      tax += base * rate / 100;
    }
    return (taxable, tax, taxable + tax);
  }

  bool get _canSubmit =>
      _customerId.isNotEmpty &&
      _lines.isNotEmpty &&
      _lines.every((_DraftLine l) => l.isUsable);

  Future<void> _create() async {
    setState(() => _saving = true);
    try {
      final Invoice invoice = await ref
          .read(salesApiProvider)
          .createInvoice(
            customerId: _customerId,
            post: _postNow,
            lines: _lines.map((_DraftLine line) => line.toInput()).toList(),
          );
      invalidateDocuments(ref);
      if (!mounted) return;
      context.toastSuccess(
        '${invoice.invoiceNumber} created',
        description: _postNow ? 'Posted to the ledger.' : null,
      );
      widget.onClose();
    } catch (error) {
      if (mounted) context.toastApiError(error, 'Could not create the invoice');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final Paged<Customer>? customers = ref
        .watch(allCustomersProvider)
        .valueOrNull;
    final bool noCustomers = customers != null && customers.items.isEmpty;
    final (double taxable, double tax, double total) preview = _preview;

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const CardHeader(
            title: 'New invoice',
            description:
                'Totals and the GST split are computed by the server from these lines.',
          ),
          CardBody(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              spacing: 16,
              children: <Widget>[
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.end,
                      spacing: 8,
                      children: <Widget>[
                        Expanded(
                          child: AppSelect(
                            label: 'Customer',
                            value: _customerId,
                            placeholder: 'Select a customer…',
                            options: <SelectOption>[
                              for (final Customer customer
                                  in customers?.items ?? const <Customer>[])
                                SelectOption(
                                  value: customer.id,
                                  label:
                                      customer.name +
                                      (customer.gstin != null
                                          ? ' · ${customer.gstin}'
                                          : ''),
                                ),
                            ],
                            onChanged: (String next) =>
                                setState(() => _customerId = next),
                          ),
                        ),
                        // Inline, because the alternative is abandoning a half-typed
                        // invoice to go to another tab. On first use the list is empty, so
                        // this is the only path forward - the button is emphasised then.
                        AppButton(
                          onPressed: () async {
                            final CreatedParty? created = await showPartyForm(
                              context,
                              kind: PartyKind.customer,
                            );
                            // Selected straight away: the user asked for this customer in
                            // order to invoice them, so making them find it in the list
                            // again is busywork.
                            if (created != null) {
                              setState(() => _customerId = created.id);
                            }
                          },
                          variant: noCustomers
                              ? AppButtonVariant.primary
                              : AppButtonVariant.secondary,
                          leftIcon: LucideIcons.plus,
                          label: 'New',
                        ),
                      ],
                    ),
                    if (noCustomers)
                      Padding(
                        padding: const EdgeInsets.only(top: 6),
                        child: Text(
                          'No customers yet - add one to raise your first invoice.',
                          style: TextStyle(fontSize: 12, color: t.contentMuted),
                        ),
                      ),
                  ],
                ),

                // Lines.
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  spacing: 8,
                  children: <Widget>[
                    for (int index = 0; index < _lines.length; index++)
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.end,
                        spacing: 8,
                        children: <Widget>[
                          Expanded(
                            flex: 5,
                            child: AppInput(
                              label: index == 0 ? 'Description' : null,
                              controller: _lines[index].description,
                              placeholder: 'Widget',
                              onChanged: (_) => setState(() {}),
                            ),
                          ),
                          Expanded(
                            flex: 2,
                            child: AppNumberInput(
                              label: index == 0 ? 'Qty' : null,
                              controller: _lines[index].quantity,
                              onChanged: (_) => setState(() {}),
                            ),
                          ),
                          Expanded(
                            flex: 2,
                            child: AppNumberInput(
                              label: index == 0 ? 'Price' : null,
                              controller: _lines[index].unitPrice,
                              decimals: 2,
                              onChanged: (_) => setState(() {}),
                            ),
                          ),
                          Expanded(
                            flex: 2,
                            child: AppNumberInput(
                              label: index == 0 ? 'GST %' : null,
                              controller: _lines[index].taxRate,
                              decimals: 2,
                              onChanged: (_) => setState(() {}),
                            ),
                          ),
                          SizedBox(
                            width: 36,
                            child: _lines.length > 1
                                ? AppIconButton(
                                    icon: LucideIcons.trash2,
                                    tooltip: 'Remove line ${index + 1}',
                                    size: 15,
                                    onPressed: () => setState(() {
                                      _lines.removeAt(index).dispose();
                                    }),
                                  )
                                : const SizedBox.shrink(),
                          ),
                        ],
                      ),
                    AppButton(
                      onPressed: () => setState(() => _lines.add(_DraftLine())),
                      variant: AppButtonVariant.ghost,
                      size: AppButtonSize.sm,
                      leftIcon: LucideIcons.plus,
                      label: 'Add line',
                    ),
                  ],
                ),

                // Estimate.
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: t.surfaceSunken,
                    borderRadius: BorderRadius.circular(Radii.lg),
                  ),
                  child: Column(
                    children: <Widget>[
                      _PreviewRow(
                        label: 'Taxable (estimate)',
                        value: preview.$1,
                      ),
                      _PreviewRow(label: 'GST (estimate)', value: preview.$2),
                      Container(
                        margin: const EdgeInsets.only(top: 4),
                        padding: const EdgeInsets.only(top: 4),
                        decoration: BoxDecoration(
                          border: Border(top: BorderSide(color: t.border)),
                        ),
                        child: _PreviewRow(
                          label: 'Total (estimate)',
                          value: preview.$3,
                          emphasised: true,
                        ),
                      ),
                      Padding(
                        padding: const EdgeInsets.only(top: 6),
                        child: Text(
                          'An estimate. The server splits CGST/SGST or IGST by place of '
                          'supply and its figures are the ones recorded.',
                          style: TextStyle(fontSize: 11, color: t.contentMuted),
                        ),
                      ),
                    ],
                  ),
                ),

                Row(
                  children: <Widget>[
                    CheckRow(
                      value: _postNow,
                      label: 'Post to the ledger immediately',
                      onChanged: (bool next) => setState(() => _postNow = next),
                    ),
                    const Spacer(),
                    AppButton(
                      onPressed: widget.onClose,
                      variant: AppButtonVariant.secondary,
                      label: 'Cancel',
                    ),
                    const SizedBox(width: 8),
                    AppButton(
                      onPressed: _canSubmit && !_saving ? _create : null,
                      loading: _saving,
                      label: _postNow ? 'Create and post' : 'Save draft',
                    ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _PreviewRow extends StatelessWidget {
  const _PreviewRow({
    required this.label,
    required this.value,
    this.emphasised = false,
  });

  final String label;
  final double value;
  final bool emphasised;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final TextStyle style = TextStyle(
      fontSize: 13,
      color: emphasised ? t.content : t.contentMuted,
      fontWeight: emphasised ? FontWeight.w600 : FontWeight.w400,
      fontFeatures: tabularFigures,
    );
    return Row(
      children: <Widget>[
        Text(label, style: style),
        const Spacer(),
        Text(value.toStringAsFixed(2), style: style),
      ],
    );
  }
}

// =============================================================================
// Customers
// =============================================================================
class _CustomerList extends ConsumerStatefulWidget {
  const _CustomerList();

  @override
  ConsumerState<_CustomerList> createState() => _CustomerListState();
}

class _CustomerListState extends ConsumerState<_CustomerList> {
  SearchQuery _query = const SearchQuery();
  final TextEditingController _search = TextEditingController();

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final AsyncValue<Paged<Customer>> customers = ref.watch(
      customersProvider(_query),
    );
    final Paged<Customer>? page = customers.valueOrNull;
    final String currency = localeSettings().currency;

    void addCustomer() => showPartyForm(context, kind: PartyKind.customer);

    return AppCard(
      child: Column(
        children: <Widget>[
          CardHeader(
            title: 'Customers',
            action: Row(
              mainAxisSize: MainAxisSize.min,
              spacing: 8,
              children: <Widget>[
                AppInput(
                  controller: _search,
                  placeholder: 'Search…',
                  width: 192,
                  onChanged: (String value) =>
                      setState(() => _query = SearchQuery(search: value)),
                ),
                AppButton(
                  onPressed: addCustomer,
                  leftIcon: LucideIcons.plus,
                  label: 'New customer',
                ),
              ],
            ),
          ),
          AppDataTable<Customer>(
            rows: page?.items ?? const <Customer>[],
            rowKey: (Customer row) => row.id,
            isLoading: customers.isLoading,
            empty: EmptyState(
              title: 'No customers',
              description: 'Add one to start invoicing.',
              // The empty state used to say "add one" with no way to do it.
              action: AppButton(
                onPressed: addCustomer,
                leftIcon: LucideIcons.plus,
                label: 'Add a customer',
              ),
            ),
            columns: <AppColumn<Customer>>[
              AppColumn<Customer>(
                header: 'Code',
                fixedWidth: 110,
                cell: (Customer row) =>
                    Text(row.code, style: monoStyle(color: t.content)),
              ),
              AppColumn<Customer>(
                header: 'Customer',
                cell: (Customer row) => Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(row.name, overflow: TextOverflow.ellipsis),
                    if (row.email != null)
                      Text(
                        row.email!,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(fontSize: 11, color: t.contentMuted),
                      ),
                  ],
                ),
              ),
              AppColumn<Customer>(
                header: 'GSTIN',
                hideOnNarrow: true,
                fixedWidth: 160,
                cell: (Customer row) => row.gstin != null
                    ? Text(
                        row.gstin!,
                        style: monoStyle(fontSize: 11, color: t.content),
                      )
                    : Text('-', style: TextStyle(color: t.contentMuted)),
              ),
              AppColumn<Customer>(
                header: 'Terms',
                hideOnNarrow: true,
                fixedWidth: 96,
                cell: (Customer row) => Text('${row.paymentTermsDays} days'),
              ),
              AppColumn<Customer>(
                header: 'Credit limit',
                numeric: true,
                cell: (Customer row) =>
                    Text(formatMoney(row.creditLimit, currency: currency)),
              ),
            ],
          ),
          if (page != null)
            Pagination(
              page: page.meta.page,
              totalPages: page.meta.totalPages,
              totalItems: page.meta.totalItems,
              onChanged: (int next) => setState(
                () => _query = SearchQuery(page: next, search: _query.search),
              ),
            ),
        ],
      ),
    );
  }
}

// =============================================================================
// Payments
// =============================================================================
class _PaymentList extends ConsumerStatefulWidget {
  const _PaymentList();

  @override
  ConsumerState<_PaymentList> createState() => _PaymentListState();
}

class _PaymentListState extends ConsumerState<_PaymentList> {
  int _page = 1;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final AsyncValue<Paged<Payment>> payments = ref.watch(
      paymentsProvider(_page),
    );
    final Paged<Payment>? page = payments.valueOrNull;
    final String currency = localeSettings().currency;

    return AppCard(
      child: Column(
        children: <Widget>[
          AppDataTable<Payment>(
            rows: page?.items ?? const <Payment>[],
            rowKey: (Payment row) => row.id,
            isLoading: payments.isLoading,
            empty: const EmptyState(
              title: 'No payments recorded',
              description: 'Receipts appear here once customers start paying.',
            ),
            columns: <AppColumn<Payment>>[
              AppColumn<Payment>(
                header: 'Receipt',
                fixedWidth: 140,
                cell: (Payment row) => Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(row.paymentNumber, style: monoStyle(color: t.content)),
                    Text(
                      formatDate(row.paymentDate),
                      style: TextStyle(fontSize: 11, color: t.contentMuted),
                    ),
                  ],
                ),
              ),
              AppColumn<Payment>(
                header: 'Customer',
                cell: (Payment row) =>
                    Text(row.customerName, overflow: TextOverflow.ellipsis),
              ),
              AppColumn<Payment>(
                header: 'Method',
                hideOnNarrow: true,
                fixedWidth: 130,
                cell: (Payment row) => Text(row.method.replaceAll('_', ' ')),
              ),
              AppColumn<Payment>(
                header: 'Amount',
                numeric: true,
                cell: (Payment row) =>
                    Text(formatMoney(row.amount, currency: currency)),
              ),
              AppColumn<Payment>(
                header: 'Unallocated',
                numeric: true,
                cell: (Payment row) => isZeroMoney(row.unallocatedAmount)
                    ? Text('-', style: TextStyle(color: t.contentMuted))
                    : AppBadge(
                        formatMoney(row.unallocatedAmount, currency: currency),
                        tone: BadgeTone.warning,
                      ),
              ),
            ],
          ),
          if (page != null)
            Pagination(
              page: page.meta.page,
              totalPages: page.meta.totalPages,
              totalItems: page.meta.totalItems,
              onChanged: (int next) => setState(() => _page = next),
            ),
        ],
      ),
    );
  }
}

// =============================================================================
// Ageing
// =============================================================================
class _AgeingReport extends ConsumerWidget {
  const _AgeingReport();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return AgeingView(
      ageing: ref.watch(receivablesAgeingProvider),
      title: 'Receivables ageing',
      totalLabel: 'Total outstanding',
      countHeader: 'Invoices',
      emptyTitle: 'Nothing outstanding',
      emptyDescription: 'No unpaid invoices.',
      overdueTone: context.tokens.warning,
    );
  }
}

/// The ageing report, shared by receivables and payables.
///
/// The two are the same table with different nouns, and keeping them one widget is what
/// stops the bucket labels and the overdue emphasis drifting between them.
class AgeingView extends StatelessWidget {
  const AgeingView({
    super.key,
    required this.ageing,
    required this.title,
    required this.totalLabel,
    required this.countHeader,
    required this.emptyTitle,
    required this.emptyDescription,
    required this.overdueTone,
  });

  final AsyncValue<Ageing> ageing;
  final String title;
  final String totalLabel;
  final String countHeader;
  final String emptyTitle;
  final String emptyDescription;
  final Color overdueTone;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final Ageing? data = ageing.valueOrNull;
    final String currency = localeSettings().currency;

    if (data == null) {
      return AppCard(
        padding: const EdgeInsets.all(20),
        child: const Skeleton(height: 200),
      );
    }

    final bool hasOverdue = !isZeroMoney(data.totalOverdue);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      spacing: 16,
      children: <Widget>[
        TileGrid(
          maxColumns: 2,
          children: <Widget>[
            MetricTile(
              label: totalLabel,
              value: formatMoney(data.totalOutstanding, currency: currency),
              valueSize: 20,
              uppercaseLabel: true,
            ),
            MetricTile(
              label: 'Overdue',
              value: formatMoney(data.totalOverdue, currency: currency),
              valueSize: 20,
              uppercaseLabel: true,
              icon: hasOverdue ? LucideIcons.circleAlert : null,
              valueTone: hasOverdue ? overdueTone : null,
            ),
          ],
        ),
        AppCard(
          borderColour: hasOverdue ? overdueTone.at(0.4) : null,
          child: Column(
            children: <Widget>[
              CardHeader(
                title: title,
                description: 'As at ${formatDate(data.asOf)}',
              ),
              AppDataTable<AgeingBucket>(
                rows: data.buckets,
                rowKey: (AgeingBucket row) => row.label,
                empty: EmptyState(
                  title: emptyTitle,
                  description: emptyDescription,
                ),
                columns: <AppColumn<AgeingBucket>>[
                  AppColumn<AgeingBucket>(
                    header: 'Bucket',
                    cell: (AgeingBucket row) => Text(row.label),
                  ),
                  AppColumn<AgeingBucket>(
                    header: countHeader,
                    numeric: true,
                    cell: (AgeingBucket row) => Text('${row.count}'),
                  ),
                  AppColumn<AgeingBucket>(
                    header: 'Amount',
                    numeric: true,
                    cell: (AgeingBucket row) =>
                        Text(formatMoney(row.amount, currency: currency)),
                  ),
                ],
              ),
            ],
          ),
        ),
        Text(
          'Buckets come from the server, so they use the same due dates the invoices '
          'themselves carry.',
          style: TextStyle(fontSize: 12, color: t.contentMuted),
        ),
      ],
    );
  }
}
