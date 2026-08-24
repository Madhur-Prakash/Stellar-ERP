import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../models/billing.dart';
import '../../state/data_providers.dart';
import '../../widgets/primitives.dart';
import 'accounts_panel.dart';

/// Accounts - every account and card in one place, with every detail editable.
///
/// A screen of its own rather than only the panel at the foot of Billing, because the two
/// are asked at different moments. Billing is "record this payment, quickly"; this is
/// "which account is this, and what is its number" - the question someone has open beside
/// a bank statement, or when a new card arrives, or when the accountant asks.
///
/// It is a thin wrapper on purpose. [AccountsPanel] already holds the list, the add forms,
/// and the in-place detail editing, and duplicating any of that so the two surfaces could
/// drift is the failure mode worth avoiding. Guarded on `account:read` in `router.dart`
/// rather than `journal:read`: this is the chart of accounts, and the account number behind
/// it is the most sensitive thing either screen shows.
class AccountsScreen extends ConsumerWidget {
  const AccountsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final BillingOptions? options = ref
        .watch(billingOptionsProvider)
        .valueOrNull;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        const PageHeader(
          title: 'Banks & cards',
          description:
              'Where your money sits and the cards you spend on. These are the choices '
              'offered when recording a payment, and every detail here is editable.',
        ),
        if (options != null) AccountsPanel(options: options, standalone: true),
      ],
    );
  }
}
