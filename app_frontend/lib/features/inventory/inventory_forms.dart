/// Inventory write forms - product, warehouse, stock adjustment, transfer.
///
/// **There is no delete, and that is deliberate.** A product named on a posted bill or a
/// stock movement cannot be removed without leaving a ledger entry pointing at nothing, so
/// the backend has no delete endpoint. Archiving hides it from every picker while the
/// history stays intact and reversible, which is what "delete" actually means for a product
/// that has been traded.
///
/// **A stock adjustment writes to the ledger, so it asks for a reason.** Correcting stock up
/// or down changes the value of your inventory and posts the difference to an expense
/// account - it is a write-off with no commercial document behind it, which is exactly what
/// an auditor looks for. A blank reason makes that unanswerable months later.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../api/inventory_api.dart';
import '../../core/api_error.dart';
import '../../core/format.dart';
import '../../models/inventory.dart';
import '../../models/page.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../../theme/tokens.dart';
import '../../widgets/app_button.dart';
import '../../widgets/app_input.dart';
import '../../widgets/app_modal.dart';
import '../../widgets/app_select.dart';
import '../../widgets/primitives.dart';
import '../../widgets/toast.dart';

// =============================================================================
// Product
// =============================================================================
Future<void> showProductForm(BuildContext context, {Product? product}) {
  return showDialog<void>(
    context: context,
    builder: (BuildContext context) => _ProductForm(product: product),
  );
}

class _ProductForm extends ConsumerStatefulWidget {
  const _ProductForm({this.product});

  /// Present to edit, absent to create.
  final Product? product;

  @override
  ConsumerState<_ProductForm> createState() => _ProductFormState();
}

class _ProductFormState extends ConsumerState<_ProductForm> {
  late final TextEditingController _name;
  late final TextEditingController _sku;
  late final TextEditingController _barcode;
  late final TextEditingController _hsn;
  late final TextEditingController _unit;
  late final TextEditingController _taxRate;
  late final TextEditingController _salePrice;
  late final TextEditingController _purchasePrice;
  late final TextEditingController _reorderLevel;

  Map<String, String> _fieldErrors = const <String, String>{};
  bool _saving = false;

  bool get _editing => widget.product != null;

  @override
  void initState() {
    super.initState();
    final Product? p = widget.product;
    _name = TextEditingController(text: p?.name ?? '');
    _sku = TextEditingController(text: p?.sku ?? '');
    _barcode = TextEditingController(text: p?.barcode ?? '');
    _hsn = TextEditingController(text: p?.hsnCode ?? '');
    _unit = TextEditingController(text: p?.unit ?? 'pcs');
    _taxRate = TextEditingController(text: p?.taxRate ?? '18');
    _salePrice = TextEditingController(text: p?.salePrice ?? '');
    _purchasePrice = TextEditingController(text: p?.purchasePrice ?? '');
    _reorderLevel = TextEditingController(text: p?.reorderLevel ?? '0');
  }

  @override
  void dispose() {
    _name.dispose();
    _sku.dispose();
    _barcode.dispose();
    _hsn.dispose();
    _unit.dispose();
    _taxRate.dispose();
    _salePrice.dispose();
    _purchasePrice.dispose();
    _reorderLevel.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    setState(() => _saving = true);
    try {
      final InventoryApi api = ref.read(inventoryApiProvider);
      final Product saved = _editing
          // `sku` is deliberately not sent: the update schema does not accept it, because a
          // code already printed on a label or quoted on a bill should not silently change.
          ? await api.updateProduct(
              widget.product!.id,
              name: _name.text.trim(),
              unit: _unit.text.trim().isEmpty ? 'pcs' : _unit.text.trim(),
              taxRate: _taxRate.text.isEmpty ? '0' : _taxRate.text,
              salePrice: _salePrice.text.isEmpty ? '0' : _salePrice.text,
              purchasePrice: _purchasePrice.text.isEmpty
                  ? '0'
                  : _purchasePrice.text,
              reorderLevel: _reorderLevel.text.isEmpty
                  ? '0'
                  : _reorderLevel.text,
              barcode: _barcode.text.trim(),
              hsnCode: _hsn.text.trim(),
            )
          : await api.createProduct(
              name: _name.text.trim(),
              sku: _sku.text.trim(),
              unit: _unit.text.trim().isEmpty ? 'pcs' : _unit.text.trim(),
              taxRate: _taxRate.text.isEmpty ? '0' : _taxRate.text,
              salePrice: _salePrice.text.isEmpty ? '0' : _salePrice.text,
              purchasePrice: _purchasePrice.text.isEmpty
                  ? '0'
                  : _purchasePrice.text,
              reorderLevel: _reorderLevel.text.isEmpty
                  ? '0'
                  : _reorderLevel.text,
              barcode: _barcode.text.trim(),
              hsnCode: _hsn.text.trim(),
            );

      invalidateInventory(ref);
      if (!mounted) return;
      context.toastSuccess(
        _editing ? '${saved.name} updated' : '${saved.name} added',
        description: 'SKU ${saved.sku}',
      );
      Navigator.of(context).pop();
    } catch (error) {
      if (!mounted) return;
      final ApiError apiError = ApiError.from(error);
      setState(() {
        _saving = false;
        _fieldErrors = apiError.fieldErrors;
      });
      context.toastError(
        apiError.code == 'unknown_error'
            ? 'Could not save the product'
            : apiError.message,
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return AppModal(
      title: _editing ? 'Edit ${widget.product!.name}' : 'New product',
      description: _editing
          ? 'The SKU cannot be changed - it may already be printed on a label or quoted on '
                'a bill.'
          : 'Only a name is required. A SKU is generated if you leave it blank.',
      footer: <Widget>[
        AppButton(
          onPressed: _saving ? null : () => Navigator.of(context).pop(),
          variant: AppButtonVariant.ghost,
          label: 'Cancel',
        ),
        AppButton(
          onPressed: _saving || _name.text.trim().isEmpty ? null : _save,
          loading: _saving,
          label: _saving
              ? 'Saving…'
              : _editing
              ? 'Save changes'
              : 'Add product',
        ),
      ],
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        spacing: 12,
        children: <Widget>[
          AppInput(
            label: 'Name',
            controller: _name,
            required: true,
            autofocus: true,
            placeholder: 'Widget Assembly A',
            error: _fieldErrors['name'],
            onChanged: (_) => setState(() {}),
          ),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            spacing: 12,
            children: <Widget>[
              Expanded(
                child: AppInput(
                  label: 'SKU',
                  controller: _sku,
                  placeholder: 'Generated if blank',
                  enabled: !_editing,
                  error: _fieldErrors['sku'],
                  hint: _editing ? 'Fixed once the product exists.' : null,
                ),
              ),
              Expanded(
                child: AppInput(
                  label: 'Barcode',
                  controller: _barcode,
                  placeholder: '8901234567890',
                  error: _fieldErrors['barcode'],
                ),
              ),
            ],
          ),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            spacing: 12,
            children: <Widget>[
              Expanded(
                child: AppInput(
                  label: 'Unit',
                  controller: _unit,
                  placeholder: 'pcs',
                ),
              ),
              Expanded(
                child: AppNumberInput(
                  label: 'GST %',
                  controller: _taxRate,
                  decimals: 2,
                  error: _fieldErrors['tax_rate'],
                ),
              ),
              Expanded(
                child: AppInput(
                  label: 'HSN code',
                  controller: _hsn,
                  placeholder: '8483',
                  hint: 'For the GST return',
                ),
              ),
            ],
          ),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            spacing: 12,
            children: <Widget>[
              Expanded(
                child: AppNumberInput(
                  label: 'Sale price',
                  controller: _salePrice,
                  decimals: 2,
                  error: _fieldErrors['sale_price'],
                ),
              ),
              Expanded(
                child: AppNumberInput(
                  label: 'Purchase price',
                  controller: _purchasePrice,
                  decimals: 2,
                  error: _fieldErrors['purchase_price'],
                ),
              ),
              Expanded(
                child: AppNumberInput(
                  label: 'Reorder at',
                  controller: _reorderLevel,
                  hint: 'Flags a shortfall',
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

// =============================================================================
// Warehouse
// =============================================================================
Future<void> showWarehouseForm(BuildContext context) {
  return showDialog<void>(
    context: context,
    builder: (BuildContext context) => const _WarehouseForm(),
  );
}

class _WarehouseForm extends ConsumerStatefulWidget {
  const _WarehouseForm();

  @override
  ConsumerState<_WarehouseForm> createState() => _WarehouseFormState();
}

class _WarehouseFormState extends ConsumerState<_WarehouseForm> {
  final TextEditingController _code = TextEditingController();
  final TextEditingController _name = TextEditingController();
  bool _isDefault = false;
  bool _saving = false;
  Map<String, String> _fieldErrors = const <String, String>{};

  @override
  void dispose() {
    _code.dispose();
    _name.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    setState(() => _saving = true);
    try {
      final Warehouse saved = await ref
          .read(inventoryApiProvider)
          .createWarehouse(
            code: _code.text.trim().toUpperCase(),
            name: _name.text.trim(),
            isDefault: _isDefault,
          );
      invalidateInventory(ref);
      if (!mounted) return;
      context.toastSuccess('${saved.name} added');
      Navigator.of(context).pop();
    } catch (error) {
      if (!mounted) return;
      final ApiError apiError = ApiError.from(error);
      setState(() {
        _saving = false;
        _fieldErrors = apiError.fieldErrors;
      });
      context.toastError(
        apiError.code == 'unknown_error'
            ? 'Could not add the warehouse'
            : apiError.message,
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final bool canSave =
        _code.text.trim().isNotEmpty && _name.text.trim().isNotEmpty;

    return AppModal(
      title: 'New location',
      description: 'A shop, a godown, a van - anywhere stock physically sits.',
      footer: <Widget>[
        AppButton(
          onPressed: _saving ? null : () => Navigator.of(context).pop(),
          variant: AppButtonVariant.ghost,
          label: 'Cancel',
        ),
        AppButton(
          onPressed: canSave && !_saving ? _save : null,
          loading: _saving,
          label: _saving ? 'Adding…' : 'Add location',
        ),
      ],
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        spacing: 12,
        children: <Widget>[
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            spacing: 12,
            children: <Widget>[
              SizedBox(
                width: 128,
                child: AppInput(
                  label: 'Code',
                  controller: _code,
                  required: true,
                  autofocus: true,
                  placeholder: 'MAIN',
                  error: _fieldErrors['code'],
                  onChanged: (String value) {
                    final String upper = value.toUpperCase();
                    if (upper != value) {
                      _code.value = TextEditingValue(
                        text: upper,
                        selection: TextSelection.collapsed(
                          offset: upper.length,
                        ),
                      );
                    }
                    setState(() {});
                  },
                ),
              ),
              Expanded(
                child: AppInput(
                  label: 'Name',
                  controller: _name,
                  required: true,
                  placeholder: 'Main shop',
                  error: _fieldErrors['name'],
                  onChanged: (_) => setState(() {}),
                ),
              ),
            ],
          ),
          Align(
            alignment: Alignment.centerLeft,
            child: CheckRow(
              value: _isDefault,
              label: 'Use this by default for new stock',
              onChanged: (bool next) => setState(() => _isDefault = next),
            ),
          ),
        ],
      ),
    );
  }
}

// =============================================================================
// Stock adjustment
// =============================================================================
Future<void> showStockAdjustForm(BuildContext context, {Product? product}) {
  return showDialog<void>(
    context: context,
    builder: (BuildContext context) => _StockAdjustForm(product: product),
  );
}

class _StockAdjustForm extends ConsumerStatefulWidget {
  const _StockAdjustForm({this.product});

  final Product? product;

  @override
  ConsumerState<_StockAdjustForm> createState() => _StockAdjustFormState();
}

class _StockAdjustFormState extends ConsumerState<_StockAdjustForm> {
  late String _productId = widget.product?.id ?? '';
  String _warehouseId = '';
  String _direction = 'decrease';
  final TextEditingController _quantity = TextEditingController();
  final TextEditingController _reason = TextEditingController();
  bool _saving = false;

  @override
  void dispose() {
    _quantity.dispose();
    _reason.dispose();
    super.dispose();
  }

  bool get _canSave =>
      _productId.isNotEmpty &&
      (double.tryParse(_quantity.text) ?? 0) > 0 &&
      _reason.text.trim().length >= 3;

  Future<void> _save() async {
    setState(() => _saving = true);
    try {
      final StockMovement movement = await ref
          .read(inventoryApiProvider)
          .adjust(
            productId: _productId,
            // The API takes a signed delta; the form asks a direction and a positive
            // number, because "-5" typed into a quantity box is easy to get backwards.
            quantityDelta: _direction == 'decrease'
                ? '-${_quantity.text}'
                : _quantity.text,
            reason: _reason.text.trim(),
            warehouseId: _warehouseId,
          );
      invalidateInventory(ref);
      if (!mounted) return;
      context.toastSuccess(
        'Stock adjusted',
        // Formatted the way the tables format it, so the toast and the row it just changed
        // do not disagree about the same number.
        description:
            '${movement.productName} - now '
            '${formatQuantity(movement.balanceAfter)} on hand',
      );
      Navigator.of(context).pop();
    } catch (error) {
      if (!mounted) return;
      setState(() => _saving = false);
      context.toastApiError(error, 'Could not adjust stock');
    }
  }

  @override
  Widget build(BuildContext context) {
    final Paged<Product>? products = ref.watch(allProductsProvider).valueOrNull;
    final List<Warehouse> warehouses =
        ref.watch(warehousesProvider).valueOrNull ?? const <Warehouse>[];

    return AppModal(
      title: 'Adjust stock',
      description:
          'For a stock take, breakage, or theft - anything that changes stock with no bill '
          'behind it.',
      footer: <Widget>[
        AppButton(
          onPressed: _saving ? null : () => Navigator.of(context).pop(),
          variant: AppButtonVariant.ghost,
          label: 'Cancel',
        ),
        AppButton(
          onPressed: _canSave && !_saving ? _save : null,
          loading: _saving,
          label: _saving ? 'Adjusting…' : 'Adjust stock',
        ),
      ],
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        spacing: 12,
        children: <Widget>[
          AppSelect(
            label: 'Product',
            required: true,
            value: _productId,
            placeholder: 'Choose…',
            options: <SelectOption>[
              for (final Product product
                  in products?.items ?? const <Product>[])
                SelectOption(
                  value: product.id,
                  label: '${product.name} (${product.sku})',
                ),
            ],
            onChanged: (String next) => setState(() => _productId = next),
          ),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            spacing: 12,
            children: <Widget>[
              Expanded(
                child: AppSelect(
                  label: 'Location',
                  value: _warehouseId,
                  placeholder: 'Default',
                  options: <SelectOption>[
                    for (final Warehouse warehouse in warehouses)
                      SelectOption(value: warehouse.id, label: warehouse.name),
                  ],
                  onChanged: (String next) =>
                      setState(() => _warehouseId = next),
                ),
              ),
              Expanded(
                child: AppSelect(
                  label: 'Direction',
                  value: _direction,
                  options: const <SelectOption>[
                    SelectOption(value: 'decrease', label: 'Reduce stock'),
                    SelectOption(value: 'increase', label: 'Increase stock'),
                  ],
                  onChanged: (String next) => setState(() => _direction = next),
                ),
              ),
            ],
          ),
          AppNumberInput(
            label: 'Quantity',
            controller: _quantity,
            required: true,
            placeholder: '0',
            hint: 'A positive number. The direction above decides the sign.',
            onChanged: (_) => setState(() {}),
          ),
          AppInput(
            label: 'Reason',
            controller: _reason,
            required: true,
            placeholder: 'Stock take 29 July - two units damaged',
            hint:
                'Required. This writes off value with no document behind it, so the reason '
                'is the only record of why.',
            onChanged: (_) => setState(() {}),
          ),
        ],
      ),
    );
  }
}

// =============================================================================
// Transfer
// =============================================================================
Future<void> showStockTransferForm(BuildContext context) {
  return showDialog<void>(
    context: context,
    builder: (BuildContext context) => const _StockTransferForm(),
  );
}

class _StockTransferForm extends ConsumerStatefulWidget {
  const _StockTransferForm();

  @override
  ConsumerState<_StockTransferForm> createState() => _StockTransferFormState();
}

class _StockTransferFormState extends ConsumerState<_StockTransferForm> {
  String _productId = '';
  String _fromId = '';
  String _toId = '';
  final TextEditingController _quantity = TextEditingController();
  bool _saving = false;

  @override
  void dispose() {
    _quantity.dispose();
    super.dispose();
  }

  bool get _sameLocation => _fromId.isNotEmpty && _fromId == _toId;

  bool get _canSave =>
      _productId.isNotEmpty &&
      _fromId.isNotEmpty &&
      _toId.isNotEmpty &&
      !_sameLocation &&
      (double.tryParse(_quantity.text) ?? 0) > 0;

  Future<void> _save() async {
    setState(() => _saving = true);
    try {
      final List<StockMovement> movements = await ref
          .read(inventoryApiProvider)
          .transfer(
            productId: _productId,
            fromWarehouseId: _fromId,
            toWarehouseId: _toId,
            quantity: _quantity.text,
          );
      invalidateInventory(ref);
      if (!mounted) return;
      context.toastSuccess(
        'Stock transferred',
        description:
            '${movements.length} movements recorded - no effect on total value.',
      );
      Navigator.of(context).pop();
    } catch (error) {
      if (!mounted) return;
      setState(() => _saving = false);
      context.toastApiError(error, 'Could not transfer stock');
    }
  }

  @override
  Widget build(BuildContext context) {
    final Paged<Product>? products = ref.watch(allProductsProvider).valueOrNull;
    final List<Warehouse> warehouses =
        ref.watch(warehousesProvider).valueOrNull ?? const <Warehouse>[];
    final List<SelectOption> locations = <SelectOption>[
      for (final Warehouse warehouse in warehouses)
        SelectOption(value: warehouse.id, label: warehouse.name),
    ];

    return AppModal(
      title: 'Move stock',
      description:
          'Between your own locations. The total value of your stock does not change.',
      footer: <Widget>[
        AppButton(
          onPressed: _saving ? null : () => Navigator.of(context).pop(),
          variant: AppButtonVariant.ghost,
          label: 'Cancel',
        ),
        AppButton(
          onPressed: _canSave && !_saving ? _save : null,
          loading: _saving,
          label: _saving ? 'Moving…' : 'Move stock',
        ),
      ],
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        spacing: 12,
        children: <Widget>[
          AppSelect(
            label: 'Product',
            required: true,
            value: _productId,
            placeholder: 'Choose…',
            options: <SelectOption>[
              for (final Product product
                  in products?.items ?? const <Product>[])
                SelectOption(
                  value: product.id,
                  label: '${product.name} (${product.sku})',
                ),
            ],
            onChanged: (String next) => setState(() => _productId = next),
          ),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            spacing: 12,
            children: <Widget>[
              Expanded(
                child: AppSelect(
                  label: 'From',
                  required: true,
                  value: _fromId,
                  placeholder: 'Choose…',
                  options: locations,
                  onChanged: (String next) => setState(() => _fromId = next),
                ),
              ),
              Expanded(
                child: AppSelect(
                  label: 'To',
                  required: true,
                  value: _toId,
                  placeholder: 'Choose…',
                  options: locations,
                  error: _sameLocation ? 'Pick a different location' : null,
                  onChanged: (String next) => setState(() => _toId = next),
                ),
              ),
            ],
          ),
          AppNumberInput(
            label: 'Quantity',
            controller: _quantity,
            required: true,
            placeholder: '0',
            onChanged: (_) => setState(() {}),
          ),
          Text(
            'A transfer records two movements - out of one location, into the other - and '
            'no ledger entry, because nothing was bought, sold, or lost. Only the location '
            'changed.',
            style: TextStyle(fontSize: 12, color: context.tokens.contentMuted),
          ),
        ],
      ),
    );
  }
}
