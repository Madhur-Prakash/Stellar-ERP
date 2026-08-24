import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_error.dart';
import '../../models/sales.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../../widgets/app_button.dart';
import '../../widgets/app_input.dart';
import '../../widgets/app_modal.dart';
import '../../widgets/toast.dart';

/// Create a customer or a supplier.
///
/// **One form for both**, because they are the same form: a name, tax registration, contact
/// details, and payment terms. The two endpoints differ only in which extra fields they
/// accept, and duplicating this would mean the GSTIN hint and the state-code explanation
/// drift apart between the two screens.
///
/// Only the name is required. Everything else is optional on the server too, and asking a
/// shopkeeper for a GSTIN and a credit limit before they can raise their first invoice is
/// how software gets abandoned at step one. The GSTIN is worth prompting for, though - it
/// is what derives the place of supply, so **without it every invoice is treated as
/// intra-state** and the CGST/SGST versus IGST split may be wrong.
enum PartyKind { customer, supplier }

/// A GSTIN is 15 characters: a 2-digit state code, a 10-character PAN, then 3 more.
const int _gstinLength = 15;

/// Opens the form. Resolves to the created record, or null if cancelled.
Future<CreatedParty?> showPartyForm(
  BuildContext context, {
  required PartyKind kind,
}) {
  return showDialog<CreatedParty>(
    context: context,
    builder: (BuildContext context) => _PartyForm(kind: kind),
  );
}

class _PartyForm extends ConsumerStatefulWidget {
  const _PartyForm({required this.kind});

  final PartyKind kind;

  @override
  ConsumerState<_PartyForm> createState() => _PartyFormState();
}

class _PartyFormState extends ConsumerState<_PartyForm> {
  final TextEditingController _name = TextEditingController();
  final TextEditingController _gstin = TextEditingController();
  final TextEditingController _email = TextEditingController();
  final TextEditingController _phone = TextEditingController();
  final TextEditingController _city = TextEditingController();
  final TextEditingController _terms = TextEditingController(text: '30');

  Map<String, String> _fieldErrors = const <String, String>{};
  bool _saving = false;

  bool get _isCustomer => widget.kind == PartyKind.customer;

  String get _label => _isCustomer ? 'customer' : 'supplier';

  @override
  void dispose() {
    _name.dispose();
    _gstin.dispose();
    _email.dispose();
    _phone.dispose();
    _city.dispose();
    _terms.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_name.text.trim().isEmpty) {
      setState(
        () => _fieldErrors = <String, String>{'name': 'A name is required'},
      );
      return;
    }

    setState(() => _saving = true);
    try {
      // **The city field is named differently on each side.** A customer has a billing
      // address and a separate shipping one, so its column is `billing_city`; a supplier has
      // one address and uses `city`. Both request schemas are `extra="forbid"`, so sending
      // the wrong key is a 422 - which is why the two calls are separate rather than sharing
      // a body builder.
      final CreatedParty created = _isCustomer
          ? await ref
                .read(salesApiProvider)
                .createCustomer(
                  name: _name.text.trim(),
                  gstin: _gstin.text.trim().toUpperCase(),
                  email: _email.text.trim(),
                  phone: _phone.text.trim(),
                  city: _city.text.trim(),
                  paymentTermsDays: int.tryParse(_terms.text) ?? 0,
                )
          : await ref
                .read(inventoryApiProvider)
                .createSupplier(
                  name: _name.text.trim(),
                  gstin: _gstin.text.trim().toUpperCase(),
                  email: _email.text.trim(),
                  phone: _phone.text.trim(),
                  city: _city.text.trim(),
                  paymentTermsDays: int.tryParse(_terms.text) ?? 0,
                );

      // Both list views and every picker that reads them.
      if (_isCustomer) {
        ref.invalidate(customersProvider);
        ref.invalidate(allCustomersProvider);
      } else {
        ref.invalidate(suppliersProvider);
        ref.invalidate(allSuppliersProvider);
      }

      if (!mounted) return;
      context.toastSuccess(
        '${created.name} added',
        description: created.code.isEmpty ? null : 'Code ${created.code}',
      );
      Navigator.of(context).pop(created);
    } catch (error) {
      if (!mounted) return;
      final ApiError apiError = ApiError.from(error);
      setState(() {
        _saving = false;
        _fieldErrors = apiError.fieldErrors;
      });
      context.toastError(
        apiError.code == 'unknown_error'
            ? 'Could not add the $_label'
            : apiError.message,
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final String gstin = _gstin.text.trim();
    final bool gstinLooksWrong =
        gstin.isNotEmpty && gstin.length != _gstinLength;

    return AppModal(
      title: _isCustomer ? 'New customer' : 'New supplier',
      description: _isCustomer
          ? 'Only a name is required. A GSTIN lets the correct GST split be applied.'
          : 'Only a name is required. A GSTIN lets input GST be claimed correctly.',
      footer: <Widget>[
        AppButton(
          onPressed: _saving ? null : () => Navigator.of(context).pop(),
          variant: AppButtonVariant.ghost,
          label: 'Cancel',
        ),
        AppButton(
          onPressed: _saving || _name.text.trim().isEmpty ? null : _submit,
          loading: _saving,
          label: _saving ? 'Adding…' : 'Add $_label',
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
            placeholder: _isCustomer
                ? 'Sharma Enterprises'
                : 'Mumbai Wholesale Traders',
            error: _fieldErrors['name'],
            onChanged: (_) => setState(() {}),
          ),
          AppInput(
            label: 'GSTIN',
            controller: _gstin,
            placeholder: '27AABCU9603R1ZM',
            error: _fieldErrors['gstin'],
            hint: gstinLooksWrong
                ? 'A GSTIN is $_gstinLength characters - this is ${gstin.length}.'
                : 'Optional. Its first two digits are the state, which decides '
                      'CGST/SGST versus IGST.',
            onChanged: (_) => setState(() {}),
          ),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            spacing: 12,
            children: <Widget>[
              Expanded(
                child: AppInput(
                  label: 'Email',
                  controller: _email,
                  placeholder: 'accounts@example.com',
                  error: _fieldErrors['email'],
                  keyboardType: TextInputType.emailAddress,
                ),
              ),
              Expanded(
                child: AppInput(
                  label: 'Phone',
                  controller: _phone,
                  placeholder: '022 2345 6789',
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
                  label: 'City',
                  controller: _city,
                  placeholder: 'Mumbai',
                ),
              ),
              Expanded(
                child: AppNumberInput(
                  label: 'Payment terms',
                  controller: _terms,
                  // Whole days: "30.5 days until due" is not a thing anyone means.
                  decimals: 0,
                  hint: 'Days until due',
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
