import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/format.dart';
import '../../core/locale_settings.dart';
import '../../models/inventory.dart';
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
import '../../widgets/data_table.dart';
import '../../widgets/metric_tile.dart';
import '../../widgets/primitives.dart';
import '../../widgets/toast.dart';
import '../sales/party_form.dart';
import '../sales/sales_screen.dart' show AgeingView;
import 'inventory_forms.dart';

/// Inventory and purchasing - stock, products, suppliers, movements, bills, payables.
///
/// The stock view leads with valuation, because the number that must reconcile to the
/// Inventory ledger account is the one worth showing first.
class InventoryScreen extends ConsumerWidget {
  const InventoryScreen({super.key, this.tab});

  final String? tab;

  static const List<(String, String)> _tabs = <(String, String)>[
    ('stock', 'Stock'),
    ('products', 'Products'),
    ('suppliers', 'Suppliers'),
    ('movements', 'Movements'),
    ('bills', 'Bills'),
    ('payables', 'Payables'),
  ];

  String get _active {
    final bool known = _tabs.any(((String, String) entry) => entry.$1 == tab);
    return known ? tab! : 'stock';
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        const PageHeader(
          title: 'Inventory & purchasing',
          description:
              'Stock is an append-only ledger. Valuation is weighted average and reconciles '
              'exactly to the Inventory account.',
        ),
        AppTabs(
          tabs: _tabs,
          active: _active,
          onChanged: (String next) => context.replace('/inventory?tab=$next'),
        ),
        switch (_active) {
          'products' => const _ProductList(),
          'suppliers' => const _SupplierList(),
          'movements' => const _MovementLog(),
          'bills' => const _BillList(),
          'payables' => const _PayablesView(),
          _ => const _StockView(),
        },
      ],
    );
  }
}

// =============================================================================
// Stock
// =============================================================================
class _StockView extends ConsumerWidget {
  const _StockView();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AppTokens t = context.tokens;
    final StockValuation? valuation = ref
        .watch(stockValuationProvider)
        .valueOrNull;
    final AsyncValue<List<StockLevel>> levels = ref.watch(stockLevelsProvider);
    final List<ReorderRow> reorder =
        ref.watch(reorderProvider).valueOrNull ?? const <ReorderRow>[];
    final String currency = localeSettings().currency;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      spacing: 16,
      children: <Widget>[
        TileGrid(
          maxColumns: 3,
          children: <Widget>[
            MetricTile(
              label: 'Stock value',
              value: valuation == null
                  ? null
                  : formatMoney(valuation.totalValue, currency: currency),
              valueSize: 20,
              uppercaseLabel: true,
              hint: 'Reconciles to the Inventory account',
            ),
            MetricTile(
              label: 'Products in stock',
              value: '${valuation?.productCount ?? 0}',
              valueSize: 20,
              uppercaseLabel: true,
            ),
            MetricTile(
              label: 'Need reorder',
              value: '${reorder.length}',
              valueSize: 20,
              uppercaseLabel: true,
              icon: reorder.isNotEmpty ? LucideIcons.triangleAlert : null,
              valueTone: reorder.isNotEmpty ? t.warning : null,
            ),
          ],
        ),

        if (reorder.isNotEmpty)
          AppCard(
            borderColour: t.warning.at(0.4),
            child: Column(
              children: <Widget>[
                const CardHeader(
                  title: 'Below reorder level',
                  description: 'At or under the level set on the product.',
                ),
                AppDataTable<ReorderRow>(
                  rows: reorder,
                  rowKey: (ReorderRow row) => row.productId,
                  columns: <AppColumn<ReorderRow>>[
                    AppColumn<ReorderRow>(
                      header: 'SKU',
                      fixedWidth: 130,
                      cell: (ReorderRow row) =>
                          Text(row.sku, style: monoStyle(color: t.content)),
                    ),
                    AppColumn<ReorderRow>(
                      header: 'Product',
                      cell: (ReorderRow row) =>
                          Text(row.name, overflow: TextOverflow.ellipsis),
                    ),
                    AppColumn<ReorderRow>(
                      header: 'On hand',
                      numeric: true,
                      cell: (ReorderRow row) =>
                          Text(formatQuantity(row.quantityOnHand)),
                    ),
                    AppColumn<ReorderRow>(
                      header: 'Reorder at',
                      numeric: true,
                      cell: (ReorderRow row) =>
                          Text(formatQuantity(row.reorderLevel)),
                    ),
                    AppColumn<ReorderRow>(
                      header: 'Short by',
                      numeric: true,
                      cell: (ReorderRow row) => Text(
                        formatQuantity(row.shortfall),
                        style: TextStyle(
                          color: t.warning,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),

        AppCard(
          child: Column(
            children: <Widget>[
              CardHeader(
                title: 'Stock on hand',
                action: Row(
                  mainAxisSize: MainAxisSize.min,
                  spacing: 8,
                  children: <Widget>[
                    AppButton(
                      onPressed: () => showStockTransferForm(context),
                      variant: AppButtonVariant.secondary,
                      size: AppButtonSize.sm,
                      leftIcon: LucideIcons.arrowLeftRight,
                      label: 'Move stock',
                    ),
                    AppButton(
                      onPressed: () => showStockAdjustForm(context),
                      variant: AppButtonVariant.secondary,
                      size: AppButtonSize.sm,
                      leftIcon: LucideIcons.slidersHorizontal,
                      label: 'Adjust',
                    ),
                    AppButton(
                      onPressed: () => showWarehouseForm(context),
                      variant: AppButtonVariant.secondary,
                      size: AppButtonSize.sm,
                      leftIcon: LucideIcons.plus,
                      label: 'Location',
                    ),
                  ],
                ),
              ),
              AppDataTable<StockLevel>(
                rows: levels.valueOrNull ?? const <StockLevel>[],
                rowKey: (StockLevel row) =>
                    '${row.productId}-${row.warehouseId}',
                isLoading: levels.isLoading,
                empty: const EmptyState(
                  title: 'No stock yet',
                  description: 'Record a goods receipt to bring stock in.',
                ),
                columns: <AppColumn<StockLevel>>[
                  AppColumn<StockLevel>(
                    header: 'SKU',
                    fixedWidth: 130,
                    cell: (StockLevel row) => Text(
                      row.productSku,
                      style: monoStyle(color: t.content),
                    ),
                  ),
                  AppColumn<StockLevel>(
                    header: 'Product',
                    cell: (StockLevel row) =>
                        Text(row.productName, overflow: TextOverflow.ellipsis),
                  ),
                  AppColumn<StockLevel>(
                    header: 'Warehouse',
                    hideOnNarrow: true,
                    fixedWidth: 110,
                    cell: (StockLevel row) => Text(row.warehouseCode),
                  ),
                  AppColumn<StockLevel>(
                    header: 'On hand',
                    numeric: true,
                    cell: (StockLevel row) =>
                        Text(formatQuantity(row.quantity)),
                  ),
                  AppColumn<StockLevel>(
                    header: 'Avg cost',
                    numeric: true,
                    hideOnNarrow: true,
                    cell: (StockLevel row) =>
                        Text(formatMoney(row.averageCost, currency: currency)),
                  ),
                  AppColumn<StockLevel>(
                    header: 'Value',
                    numeric: true,
                    cell: (StockLevel row) =>
                        Text(formatMoney(row.totalValue, currency: currency)),
                  ),
                ],
              ),
            ],
          ),
        ),
      ],
    );
  }
}

// =============================================================================
// Products
// =============================================================================
class _ProductList extends ConsumerStatefulWidget {
  const _ProductList();

  @override
  ConsumerState<_ProductList> createState() => _ProductListState();
}

class _ProductListState extends ConsumerState<_ProductList> {
  SearchQuery _query = const SearchQuery();
  final TextEditingController _search = TextEditingController();
  final TextEditingController _scan = TextEditingController();
  bool _archiving = false;

  @override
  void dispose() {
    _search.dispose();
    _scan.dispose();
    super.dispose();
  }

  Future<void> _lookupBarcode() async {
    final String code = _scan.text.trim();
    if (code.isEmpty) return;
    try {
      final Product product = await ref
          .read(inventoryApiProvider)
          .byBarcode(code);
      if (!mounted) return;
      context.toastSuccess(product.name, description: 'SKU ${product.sku}');
      _scan.clear();
    } catch (_) {
      if (mounted) context.toastError('No product with that barcode');
    }
  }

  /// Archive or restore a product.
  ///
  /// The nearest thing to deletion that is safe here. A product named on a posted bill or a
  /// stock movement cannot be removed - the entry would point at nothing - so it is hidden
  /// from every picker instead, and the action is fully reversible.
  Future<void> _toggleArchive(Product product) async {
    final bool archiving = product.isActive;
    if (archiving) {
      final bool confirmed = await confirmAction(
        context,
        title: 'Archive ${product.name}?',
        message:
            'It disappears from pickers but its history and stock stay. You can restore it '
            'later.',
        confirmLabel: 'Archive',
      );
      if (!confirmed) return;
    }

    setState(() => _archiving = true);
    try {
      final Product saved = await ref
          .read(inventoryApiProvider)
          .updateProduct(product.id, isActive: !archiving);
      invalidateInventory(ref);
      if (!mounted) return;
      context.toastSuccess(
        saved.isActive ? '${saved.name} restored' : '${saved.name} archived',
        description: saved.isActive
            ? null
            : 'Hidden from pickers. Its history and stock are untouched.',
      );
    } catch (error) {
      if (mounted) context.toastApiError(error, 'Could not update the product');
    } finally {
      if (mounted) setState(() => _archiving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final AsyncValue<Paged<Product>> products = ref.watch(
      productsProvider(_query),
    );
    final Paged<Product>? page = products.valueOrNull;
    final String currency = localeSettings().currency;

    return AppCard(
      child: Column(
        children: <Widget>[
          CardHeader(
            title: 'Products',
            action: Row(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.end,
              spacing: 8,
              children: <Widget>[
                AppInput(
                  controller: _scan,
                  placeholder: 'Scan barcode…',
                  leftIcon: LucideIcons.scanLine,
                  width: 176,
                  // A hardware scanner types the code then sends Enter.
                  onSubmitted: (_) => _lookupBarcode(),
                ),
                AppInput(
                  controller: _search,
                  placeholder: 'Search…',
                  width: 160,
                  onChanged: (String value) =>
                      setState(() => _query = SearchQuery(search: value)),
                ),
                AppButton(
                  onPressed: () => showProductForm(context),
                  size: AppButtonSize.sm,
                  leftIcon: LucideIcons.plus,
                  label: 'New',
                ),
              ],
            ),
          ),
          AppDataTable<Product>(
            rows: page?.items ?? const <Product>[],
            rowKey: (Product row) => row.id,
            isLoading: products.isLoading,
            empty: const EmptyState(
              title: 'No products',
              description: 'Add one to start buying and selling.',
            ),
            columns: <AppColumn<Product>>[
              AppColumn<Product>(
                header: 'SKU',
                fixedWidth: 130,
                cell: (Product row) =>
                    Text(row.sku, style: monoStyle(color: t.content)),
              ),
              AppColumn<Product>(
                header: 'Product',
                cell: (Product row) => Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(row.name, overflow: TextOverflow.ellipsis),
                    if (row.barcode != null)
                      Text(
                        row.barcode!,
                        style: monoStyle(fontSize: 11, color: t.contentMuted),
                      ),
                  ],
                ),
              ),
              AppColumn<Product>(
                header: 'Kind',
                hideOnNarrow: true,
                fixedWidth: 110,
                cell: (Product row) => AppBadge(
                  row.kind,
                  tone: row.kind == 'stocked'
                      ? BadgeTone.info
                      : BadgeTone.neutral,
                ),
              ),
              AppColumn<Product>(
                header: 'GST',
                numeric: true,
                hideOnNarrow: true,
                fixedWidth: 72,
                cell: (Product row) => Text('${formatQuantity(row.taxRate)}%'),
              ),
              AppColumn<Product>(
                header: 'Sale price',
                numeric: true,
                cell: (Product row) =>
                    Text(formatMoney(row.salePrice, currency: currency)),
              ),
              AppColumn<Product>(
                header: 'On hand',
                numeric: true,
                cell: (Product row) => row.tracksStock
                    ? Text(
                        formatQuantity(row.quantityOnHand),
                        style: TextStyle(
                          color: row.needsReorder ? t.warning : t.content,
                          fontWeight: row.needsReorder
                              ? FontWeight.w500
                              : FontWeight.w400,
                        ),
                      )
                    : Text('-', style: TextStyle(color: t.contentMuted)),
              ),
              AppColumn<Product>(
                header: '',
                fixedWidth: 116,
                cell: (Product row) => Row(
                  mainAxisAlignment: MainAxisAlignment.end,
                  mainAxisSize: MainAxisSize.min,
                  children: <Widget>[
                    AppIconButton(
                      icon: LucideIcons.pencil,
                      tooltip: 'Edit ${row.name}',
                      size: 14,
                      onPressed: () => showProductForm(context, product: row),
                    ),
                    if (row.tracksStock)
                      AppIconButton(
                        icon: LucideIcons.slidersHorizontal,
                        tooltip: 'Adjust stock for ${row.name}',
                        size: 14,
                        onPressed: () =>
                            showStockAdjustForm(context, product: row),
                      ),
                    AppIconButton(
                      icon: row.isActive
                          ? LucideIcons.archive
                          : LucideIcons.archiveRestore,
                      tooltip: row.isActive
                          ? 'Archive ${row.name}'
                          : 'Restore ${row.name}',
                      size: 14,
                      onPressed: _archiving ? null : () => _toggleArchive(row),
                    ),
                  ],
                ),
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
// Suppliers
// =============================================================================
/// Suppliers had no screen at all until the web app grew one, which made two workflows
/// unreachable: entering a bill, and confirming a scanned invoice - both need a supplier on
/// file and neither offered a way to create one.
class _SupplierList extends ConsumerStatefulWidget {
  const _SupplierList();

  @override
  ConsumerState<_SupplierList> createState() => _SupplierListState();
}

class _SupplierListState extends ConsumerState<_SupplierList> {
  int _page = 1;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final AsyncValue<Paged<Supplier>> suppliers = ref.watch(
      suppliersProvider(_page),
    );
    final Paged<Supplier>? page = suppliers.valueOrNull;

    void add() => showPartyForm(context, kind: PartyKind.supplier);

    return AppCard(
      child: Column(
        children: <Widget>[
          CardHeader(
            title: 'Suppliers',
            description:
                "A supplier's GSTIN is what lets input GST be claimed and what matches a "
                'scanned invoice automatically.',
            action: AppButton(
              onPressed: add,
              leftIcon: LucideIcons.plus,
              label: 'New supplier',
            ),
          ),
          AppDataTable<Supplier>(
            rows: page?.items ?? const <Supplier>[],
            rowKey: (Supplier row) => row.id,
            isLoading: suppliers.isLoading,
            empty: EmptyState(
              title: 'No suppliers',
              description:
                  'Add one to enter bills or to confirm a scanned invoice.',
              action: AppButton(
                onPressed: add,
                leftIcon: LucideIcons.plus,
                label: 'Add a supplier',
              ),
            ),
            columns: <AppColumn<Supplier>>[
              AppColumn<Supplier>(
                header: 'Code',
                fixedWidth: 110,
                cell: (Supplier row) =>
                    Text(row.code, style: monoStyle(color: t.content)),
              ),
              AppColumn<Supplier>(
                header: 'Supplier',
                cell: (Supplier row) => Column(
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
              AppColumn<Supplier>(
                header: 'GSTIN',
                hideOnNarrow: true,
                fixedWidth: 160,
                cell: (Supplier row) => row.gstin != null
                    ? Text(
                        row.gstin!,
                        style: monoStyle(fontSize: 11, color: t.content),
                      )
                    : Text('-', style: TextStyle(color: t.contentMuted)),
              ),
              AppColumn<Supplier>(
                header: 'City',
                hideOnNarrow: true,
                fixedWidth: 130,
                cell: (Supplier row) => Text(row.city ?? '-'),
              ),
              AppColumn<Supplier>(
                header: 'Terms',
                numeric: true,
                cell: (Supplier row) => Text('${row.paymentTermsDays} days'),
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
// Movements
// =============================================================================
class _MovementLog extends ConsumerStatefulWidget {
  const _MovementLog();

  @override
  ConsumerState<_MovementLog> createState() => _MovementLogState();
}

class _MovementLogState extends ConsumerState<_MovementLog> {
  int _page = 1;

  static const Map<String, BadgeTone> _tones = <String, BadgeTone>{
    'receipt': BadgeTone.success,
    'issue': BadgeTone.warning,
    'adjustment': BadgeTone.danger,
    'transfer_in': BadgeTone.info,
    'transfer_out': BadgeTone.info,
    'reversal': BadgeTone.danger,
  };

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final AsyncValue<Paged<StockMovement>> movements = ref.watch(
      movementsProvider(_page),
    );
    final Paged<StockMovement>? page = movements.valueOrNull;
    final String currency = localeSettings().currency;

    return AppCard(
      child: Column(
        children: <Widget>[
          const CardHeader(
            title: 'Stock movements',
            description:
                'Append-only. Every receipt, issue, adjustment, and transfer.',
          ),
          AppDataTable<StockMovement>(
            rows: page?.items ?? const <StockMovement>[],
            rowKey: (StockMovement row) => row.id,
            isLoading: movements.isLoading,
            empty: const EmptyState(
              title: 'No movements',
              description: 'Stock activity appears here.',
            ),
            columns: <AppColumn<StockMovement>>[
              AppColumn<StockMovement>(
                header: 'Date',
                fixedWidth: 116,
                cell: (StockMovement row) => Text(formatDate(row.movementDate)),
              ),
              AppColumn<StockMovement>(
                header: 'Product',
                cell: (StockMovement row) =>
                    Text(row.productName, overflow: TextOverflow.ellipsis),
              ),
              AppColumn<StockMovement>(
                header: 'Type',
                fixedWidth: 130,
                cell: (StockMovement row) => AppBadge(
                  row.kind.replaceAll('_', ' '),
                  tone: _tones[row.kind] ?? BadgeTone.neutral,
                ),
              ),
              AppColumn<StockMovement>(
                header: 'Qty',
                numeric: true,
                cell: (StockMovement row) {
                  final bool negative = isNegativeMoney(row.quantity);
                  return Text(
                    '${negative ? '' : '+'}${formatQuantity(row.quantity)}',
                    style: TextStyle(color: negative ? t.warning : t.success),
                  );
                },
              ),
              AppColumn<StockMovement>(
                header: 'Balance',
                numeric: true,
                hideOnNarrow: true,
                cell: (StockMovement row) =>
                    Text(formatQuantity(row.balanceAfter)),
              ),
              AppColumn<StockMovement>(
                header: 'Cost',
                numeric: true,
                cell: (StockMovement row) =>
                    Text(formatMoney(row.totalCost, currency: currency)),
              ),
              AppColumn<StockMovement>(
                header: 'Posted',
                hideOnNarrow: true,
                fixedWidth: 140,
                cell: (StockMovement row) => row.journalEntryId != null
                    ? const AppBadge('yes', tone: BadgeTone.success)
                    // A transfer moves no value, so it correctly posts nothing.
                    : Text(
                        'no ledger effect',
                        style: TextStyle(fontSize: 11, color: t.contentMuted),
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
// Bills
// =============================================================================
class _BillList extends ConsumerStatefulWidget {
  const _BillList();

  @override
  ConsumerState<_BillList> createState() => _BillListState();
}

class _BillListState extends ConsumerState<_BillList> {
  int _page = 1;
  String? _posting;

  static const Map<String, BadgeTone> _tones = <String, BadgeTone>{
    'draft': BadgeTone.neutral,
    'posted': BadgeTone.info,
    'partially_paid': BadgeTone.warning,
    'paid': BadgeTone.success,
    'cancelled': BadgeTone.danger,
  };

  Future<void> _post(Bill bill) async {
    setState(() => _posting = bill.id);
    try {
      final Bill posted = await ref
          .read(inventoryApiProvider)
          .postBill(bill.id);
      invalidateDocuments(ref);
      if (mounted) {
        context.toastSuccess(
          '${posted.billNumber} posted',
          description: 'Payable recognised and input GST claimed.',
        );
      }
    } catch (error) {
      if (mounted) context.toastApiError(error, 'Could not post the bill');
    } finally {
      if (mounted) setState(() => _posting = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final AsyncValue<Paged<Bill>> bills = ref.watch(billsProvider(_page));
    final Paged<Bill>? page = bills.valueOrNull;
    final String currency = localeSettings().currency;

    return AppCard(
      child: Column(
        children: <Widget>[
          const CardHeader(
            title: 'Bills',
            description:
                'A duplicate supplier invoice number is refused - it is the most expensive '
                'error in payables.',
          ),
          AppDataTable<Bill>(
            rows: page?.items ?? const <Bill>[],
            rowKey: (Bill row) => row.id,
            isLoading: bills.isLoading,
            empty: const EmptyState(
              title: 'No bills',
              description: 'Enter a supplier invoice to record what you owe.',
            ),
            columns: <AppColumn<Bill>>[
              AppColumn<Bill>(
                header: 'Bill',
                fixedWidth: 150,
                cell: (Bill row) => Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(row.billNumber, style: monoStyle(color: t.content)),
                    if (row.supplierInvoiceNumber != null)
                      Text(
                        'their ref ${row.supplierInvoiceNumber}',
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(fontSize: 11, color: t.contentMuted),
                      ),
                  ],
                ),
              ),
              AppColumn<Bill>(
                header: 'Supplier',
                cell: (Bill row) =>
                    Text(row.supplierName, overflow: TextOverflow.ellipsis),
              ),
              AppColumn<Bill>(
                header: 'Due',
                hideOnNarrow: true,
                fixedWidth: 120,
                cell: (Bill row) => Text(
                  formatDate(row.dueDate),
                  style: TextStyle(
                    color: row.isOverdue ? t.danger : t.content,
                    fontWeight: row.isOverdue
                        ? FontWeight.w500
                        : FontWeight.w400,
                  ),
                ),
              ),
              AppColumn<Bill>(
                header: 'Status',
                fixedWidth: 120,
                cell: (Bill row) => AppBadge(
                  row.status.replaceAll('_', ' '),
                  tone: _tones[row.status] ?? BadgeTone.neutral,
                ),
              ),
              AppColumn<Bill>(
                header: 'Total',
                numeric: true,
                cell: (Bill row) =>
                    Text(formatMoney(row.grandTotal, currency: currency)),
              ),
              AppColumn<Bill>(
                header: 'Outstanding',
                numeric: true,
                cell: (Bill row) => isZeroMoney(row.outstanding)
                    ? Text('-', style: TextStyle(color: t.contentMuted))
                    : Text(formatMoney(row.outstanding, currency: currency)),
              ),
              AppColumn<Bill>(
                header: '',
                fixedWidth: 80,
                cell: (Bill row) => row.status == 'draft'
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
              onChanged: (int next) => setState(() => _page = next),
            ),
        ],
      ),
    );
  }
}

// =============================================================================
// Payables
// =============================================================================
class _PayablesView extends ConsumerWidget {
  const _PayablesView();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return AgeingView(
      ageing: ref.watch(payablesAgeingProvider),
      title: 'Payables ageing',
      totalLabel: 'Total payable',
      countHeader: 'Bills',
      emptyTitle: 'Nothing payable',
      emptyDescription: 'No unpaid bills.',
      overdueTone: context.tokens.danger,
    );
  }
}
