import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:stellarerp_desktop/features/billing/accounts_panel.dart';
import 'package:stellarerp_desktop/models/billing.dart';
import 'package:stellarerp_desktop/state/data_providers.dart';
import 'package:stellarerp_desktop/theme/app_theme.dart';

/// Do the new widgets actually build?
///
/// A narrow question, and worth its own file because `flutter analyze` cannot answer it.
/// The one UI bug in this app that reached a release binary was a `Builder` whose closure
/// captured the variable it was being assigned to, so it returned a widget containing
/// itself - a clean analyze, a clean unit suite, and a stack overflow on the first frame.
/// These tests pump each widget in both themes and at a narrow width, which is where that
/// class of mistake surfaces.
void main() {
  const MoneyAccount cash = MoneyAccount(
    id: 'acct-cash',
    name: 'Cash on Hand',
    isDefault: true,
    kind: MoneyAccountKind.cash,
  );
  const MoneyAccount bank = MoneyAccount(
    id: 'acct-bank',
    name: 'Primary Bank Account',
    isDefault: false,
    kind: MoneyAccountKind.bank,
    code: '1120',
    bankName: 'HDFC Bank',
    holderName: 'Jhon Doe',
    accountNumberLast4: '4321',
  );

  /// A closed account: still listed when asked for, never offered in a picker.
  const MoneyAccount archivedBank = MoneyAccount(
    id: 'acct-old',
    name: 'Old current account',
    isDefault: false,
    kind: MoneyAccountKind.bank,
    code: '1123',
    isActive: false,
    canArchive: true,
  );

  // The same account under a second name - what a debit card is.
  const MoneyAccount debitCard = MoneyAccount(
    id: 'acct-bank',
    name: 'SBI Debit ··1234',
    isDefault: false,
    kind: MoneyAccountKind.bank,
    cardId: 'card-debit',
    cardLast4: '1234',
  );
  const MoneyAccount creditCard = MoneyAccount(
    id: 'acct-card',
    name: 'HDFC Millennia ··4242',
    isDefault: false,
    kind: MoneyAccountKind.creditCard,
    cardId: 'card-credit',
    cardLast4: '4242',
  );

  const PaymentCard credit = PaymentCard(
    id: 'card-credit',
    label: 'HDFC Millennia',
    kind: CardKind.credit,
    network: CardNetwork.visa,
    last4: '4242',
    accountId: 'acct-card',
    accountName: 'HDFC Millennia',
    isActive: true,
  );
  const PaymentCard archivedDebit = PaymentCard(
    id: 'card-debit',
    label: 'SBI Debit',
    kind: CardKind.debit,
    network: CardNetwork.rupay,
    last4: '1234',
    accountId: 'acct-bank',
    accountName: 'Primary Bank Account',
    isActive: false,
  );

  const BillingOptions options = BillingOptions(
    categories: <Category>[],
    moneyAccounts: <MoneyAccount>[cash, bank, debitCard, creditCard],
    cards: <PaymentCard>[credit, archivedDebit],
    today: '2026-08-02',
    currency: 'INR',
  );

  /// Pumps [child] inside just enough app to be realistic: both themes come from the
  /// real token set, and the card list is overridden so nothing reaches for an
  /// `ApiClient` that only `main` can build.
  Future<void> pump(
    WidgetTester tester,
    Widget child, {
    ThemeData? theme,
    Size size = const Size(1280, 900),
  }) async {
    tester.view.physicalSize = size;
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(
      ProviderScope(
        overrides: <Override>[
          billingCardsProvider.overrideWith(
            (Ref ref, bool includeArchived) => Future<List<PaymentCard>>.value(
              includeArchived
                  ? <PaymentCard>[credit, archivedDebit]
                  : <PaymentCard>[credit],
            ),
          ),
          // Overridden for the same reason as the cards: unstubbed, the provider reaches
          // for an `ApiClient` that only `main` can build, fails, and `retrying` schedules
          // a backoff timer that outlives the test - which surfaces as "a Timer is still
          // pending", not as anything pointing at the missing stub.
          moneyAccountsProvider.overrideWith(
            (Ref ref, bool includeArchived) => Future<List<MoneyAccount>>.value(
              includeArchived
                  ? <MoneyAccount>[cash, bank, archivedBank]
                  : <MoneyAccount>[cash, bank],
            ),
          ),
        ],
        child: MaterialApp(
          theme: theme ?? AppTheme.light(),
          home: Scaffold(body: SingleChildScrollView(child: child)),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  group('AccountsPanel', () {
    testWidgets('builds, and lists accounts apart from cards', (
      WidgetTester tester,
    ) async {
      await pump(tester, const AccountsPanel(options: options));

      expect(tester.takeException(), isNull);
      expect(find.text('Accounts & cards'), findsOneWidget);
      expect(find.text('Cash on Hand'), findsOneWidget);
      // `textContaining`, not `text`: the name is rendered as rich text so the last four
      // digits can be dimmed beside it, and an exact match would miss the whole span.
      expect(find.textContaining('Primary Bank Account'), findsWidgets);
      // The bank and holder take the place of the account code on the second line.
      expect(find.text('HDFC Bank · Jhon Doe'), findsOneWidget);

      // The debit card must NOT appear among the cash and bank accounts, even though it
      // carries a bank account's id - it belongs in the card list.
      expect(find.text('SBI Debit ··1234'), findsNothing);
    });

    testWidgets('builds in the dark theme too', (WidgetTester tester) async {
      await pump(
        tester,
        const AccountsPanel(options: options),
        theme: AppTheme.dark(),
      );
      expect(tester.takeException(), isNull);
      expect(find.text('Accounts & cards'), findsOneWidget);
    });

    testWidgets('builds at a narrow window without overflowing', (
      WidgetTester tester,
    ) async {
      // Overflow is reported as an exception in tests, so `takeException` catches a
      // layout that only breaks when the window is dragged small.
      await pump(
        tester,
        const AccountsPanel(options: options),
        size: const Size(720, 900),
      );
      expect(tester.takeException(), isNull);
    });

    testWidgets('archived rows appear only once asked, in their own group', (
      WidgetTester tester,
    ) async {
      await pump(tester, const AccountsPanel(options: options));

      // The default view is the active rows alone - no group, no heading.
      expect(find.textContaining('HDFC Millennia'), findsWidgets);
      expect(find.textContaining('Archived ('), findsNothing);
      expect(find.textContaining('Old current account'), findsNothing);

      // Both sections share one toggle, so there are two of them on screen.
      await tester.tap(find.text('Show archived').first);
      await tester.pumpAndSettle();

      expect(tester.takeException(), isNull);
      expect(find.text('Hide archived'), findsWidgets);

      // Grouped under a heading rather than sitting among the live rows with a badge - one
      // group for the closed account, one for the archived card.
      expect(find.textContaining('Archived ('), findsNWidgets(2));
      expect(find.textContaining('Old current account'), findsOneWidget);
    });

    testWidgets('names the card kinds, since one is a liability', (
      WidgetTester tester,
    ) async {
      await pump(tester, const AccountsPanel(options: options));
      // The badge is the only thing on this row saying which of the two it is, and the
      // distinction is the difference between an asset and a debt.
      expect(find.text('Credit'), findsOneWidget);
    });

    testWidgets('every card row offers to archive or restore it', (
      WidgetTester tester,
    ) async {
      // The control the user reported missing. Asserted on the rendered label rather than
      // on the widget type, because "the button exists in the tree" and "the button is
      // visible on the row" are different claims and only the second one matters.
      await pump(tester, const AccountsPanel(options: options));
      expect(find.text('Archive'), findsOneWidget);

      // `.first`: both sections carry the toggle, so the finder matches twice.
      await tester.tap(find.text('Show archived').first);
      await tester.pumpAndSettle();

      // One Archive, for the live card - the two seeded accounts cannot be archived, so
      // they offer nothing. Two Restores: the archived card and the closed account, which
      // is the same control on both kinds of row.
      expect(find.text('Archive'), findsOneWidget);
      expect(find.text('Restore'), findsNWidgets(2));
    });

    testWidgets('both add actions are offered', (WidgetTester tester) async {
      // Matching the web accounts page. Adding an account was previously reachable only
      // from the recording form, so the accounts screen was the one place you could not
      // make one.
      await pump(tester, const AccountsPanel(options: options));
      expect(find.text('Add an account'), findsOneWidget);
      expect(find.text('Add a card'), findsOneWidget);
    });
  });

  group('AccountsPanel standalone', () {
    testWidgets('drops its own heading, keeps the action', (
      WidgetTester tester,
    ) async {
      // On the Accounts screen the page header has already said "Accounts & cards", so
      // the card must not say it again - but "Add a card" is the point of that header row
      // and has to survive.
      await pump(
        tester,
        const AccountsPanel(options: options, standalone: true),
      );

      expect(tester.takeException(), isNull);
      expect(find.text('Accounts & cards'), findsNothing);
      expect(find.text('Add a card'), findsOneWidget);
      expect(find.text('Cash on Hand'), findsOneWidget);
    });

    testWidgets('an account with details shows them under its name', (
      WidgetTester tester,
    ) async {
      await pump(
        tester,
        const AccountsPanel(options: options, standalone: true),
      );
      // The bank and holder replace the account code on the second line, and the tail of
      // the number sits beside the name.
      expect(find.textContaining('HDFC Bank'), findsWidgets);
    });
  });

  group('TransferForm', () {
    testWidgets('builds, and offers each real account once', (
      WidgetTester tester,
    ) async {
      await pump(tester, TransferForm(options: options, onClose: () {}));

      expect(tester.takeException(), isNull);
      expect(find.text('Move money between accounts'), findsOneWidget);

      // Both sides are seeded. "From" takes the default account; "To" takes the first
      // account that is *not* it, because the one thing a transfer cannot be is an account
      // to itself - so the form opens ready to use rather than already invalid.
      expect(find.text('Cash on Hand'), findsOneWidget);
      expect(find.text('Primary Bank Account'), findsOneWidget);
      expect(find.text('Choose an account'), findsNothing);
    });

    testWidgets('has no category field', (WidgetTester tester) async {
      await pump(tester, TransferForm(options: options, onClose: () {}));
      // Not an oversight: moving your own money is neither earning nor spending it, so
      // there is no income or expense line for it to go against. A category field
      // appearing here would mean the accounting had been misunderstood.
      expect(find.text('Category'), findsNothing);
    });

    testWidgets('builds in the dark theme and at a narrow window', (
      WidgetTester tester,
    ) async {
      await pump(
        tester,
        TransferForm(options: options, onClose: () {}),
        theme: AppTheme.dark(),
        size: const Size(720, 900),
      );
      expect(tester.takeException(), isNull);
    });
  });
}
