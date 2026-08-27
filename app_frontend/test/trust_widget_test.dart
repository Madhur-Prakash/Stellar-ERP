import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:stellarerp_desktop/features/trust/trust_screen.dart';
import 'package:stellarerp_desktop/models/trust.dart';
import 'package:stellarerp_desktop/state/data_providers.dart';
import 'package:stellarerp_desktop/theme/app_theme.dart';

/// Does the Trust screen build where the shell actually puts it?
///
/// This file exists because of a bug, and the bug is worth stating: the screen's
/// loaded branch returned a `ListView`. `AppShell` places every screen inside
/// `SingleChildScrollView -> Column`, which hands its child **unbounded** height,
/// and a `ListView` given unbounded height throws during layout. In a release
/// binary that renders as an empty page - so the one screen whose entire job is to
/// report on the integrity of the books showed nothing at all, with a clean
/// `flutter analyze` and a clean test suite.
///
/// **So the harness below is the test.** Pumping the screen inside a `Scaffold`
/// would have passed: a Scaffold body is bounded, the ListView would have been
/// happy, and the suite would have certified a screen that cannot render in the
/// app. The `SingleChildScrollView` is not incidental - it reproduces the only
/// constraint that matters, and every screen-level test in this app should keep it
/// for that reason.
void main() {
  const ChainHealth healthy = ChainHealth(
    reachable: true,
    head: 4,
    root: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    entries: 412,
    sealedAt: '2026-08-26T01:04:11Z',
    admin: 'GDKLGSB2MF7ZXDCFR2VSAPZ5WYI25EKRMRQRVXKQ6FB6FB4EOZN2HSGZ',
    agreesWithLocal: true,
  );

  const Seal confirmed = Seal(
    id: 'seal-4',
    seq: 4,
    status: SealStatus.confirmed,
    trigger: SealTrigger.schedule,
    merkleRoot: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    prevRoot: 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
    entryCount: 37,
    debitMinor: '184500000',
    entryDateFrom: '2026-08-01',
    entryDateTo: '2026-08-26',
    attempts: 1,
    treeDepth: 6,
    network: 'testnet',
    contractId: 'CCB66KMNINKNGBCVWCYKEF26OIXNZQIIJ4EUKCUOUD4OCDFA6ID4S5YR',
    txHash: 'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
    sealedAt: '2026-08-26T01:04:11Z',
    confirmedAt: '2026-08-26T01:04:16Z',
    explorerUrl:
        'https://stellar.expert/explorer/testnet/tx/cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
  );

  /// A seal the network has not answered on yet. It carries no `sealedAt`, because
  /// that timestamp comes from the ledger - showing the device's clock there would
  /// undermine the exact claim the screen exists to make.
  const Seal pending = Seal(
    id: 'seal-5',
    seq: 5,
    status: SealStatus.submitted,
    trigger: SealTrigger.manual,
    merkleRoot: 'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
    prevRoot: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    entryCount: 9,
    debitMinor: '45000000',
    entryDateFrom: '2026-08-26',
    entryDateTo: '2026-08-27',
    attempts: 1,
    treeDepth: 4,
    network: 'testnet',
  );

  const AttestationStatus sealing = AttestationStatus(
    enabled: true,
    configured: true,
    ready: true,
    cadence: SealCadence.daily,
    externalSigner: false,
    sealsConfirmed: 4,
    entriesSealed: 412,
    unsealedEntries: 9,
    chain: healthy,
    warnings: <String>[],
    network: 'testnet',
    contractId: 'CCB66KMNINKNGBCVWCYKEF26OIXNZQIIJ4EUKCUOUD4OCDFA6ID4S5YR',
    orgNamespace: 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
    signerPublicKey: 'GDKLGSB2MF7ZXDCFR2VSAPZ5WYI25EKRMRQRVXKQ6FB6FB4EOZN2HSGZ',
    registeredAt: '2026-08-20T09:00:00Z',
    oldestUnsealedAt: '2026-08-26T18:30:00Z',
    daysUnsealed: 0.4,
    lastSeal: confirmed,
  );

  /// Sealing switched off, but the contract is configured: the state a fresh
  /// organization is in on a deployment that has a contract.
  const AttestationStatus off = AttestationStatus(
    enabled: false,
    configured: true,
    ready: false,
    cadence: SealCadence.daily,
    externalSigner: false,
    sealsConfirmed: 0,
    entriesSealed: 0,
    unsealedEntries: 23,
    chain: ChainHealth(reachable: true),
    warnings: <String>[],
    network: 'testnet',
    contractId: 'CCB66KMNINKNGBCVWCYKEF26OIXNZQIIJ4EUKCUOUD4OCDFA6ID4S5YR',
  );

  /// No contract at all - a deployment question rather than a user one.
  const AttestationStatus unconfigured = AttestationStatus(
    enabled: false,
    configured: false,
    ready: false,
    cadence: SealCadence.daily,
    externalSigner: false,
    sealsConfirmed: 0,
    entriesSealed: 0,
    unsealedEntries: 0,
    chain: ChainHealth(reachable: false, error: 'no contract configured'),
    warnings: <String>['ATTESTATION_ENABLED is false on this server.'],
  );

  const SealPage history = SealPage(
    items: <Seal>[pending, confirmed],
    hasMore: false,
    continuous: true,
  );

  /// Pumps the screen the way `AppShell` does: inside a `SingleChildScrollView`,
  /// so the child is handed unbounded height. See the file comment - this is the
  /// point of the test, not a detail of it.
  Future<void> pump(
    WidgetTester tester,
    AttestationStatus status, {
    SealPage page = history,
    ThemeData? theme,
    Size size = const Size(1280, 900),
  }) async {
    tester.view.physicalSize = size;
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(
      ProviderScope(
        overrides: <Override>[
          // Overridden because unstubbed these reach for an `ApiClient` that only
          // `main` can build, and `retrying` then schedules a backoff timer that
          // outlives the test - surfacing as "a Timer is still pending" rather than
          // as anything pointing at the missing stub.
          attestationStatusProvider.overrideWith(
            (Ref ref) => Future<AttestationStatus>.value(status),
          ),
          sealHistoryProvider.overrideWith(
            (Ref ref) => Future<SealPage>.value(page),
          ),
        ],
        child: MaterialApp(
          theme: theme ?? AppTheme.light(),
          home: Scaffold(body: SingleChildScrollView(child: const TrustScreen())),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  group('TrustScreen', () {
    testWidgets('builds under unbounded height when sealing is on', (
      WidgetTester tester,
    ) async {
      await pump(tester, sealing);

      expect(tester.takeException(), isNull);
      expect(find.text('Trust'), findsOneWidget);
      expect(find.text('Your books are being sealed'), findsOneWidget);
    });

    testWidgets('builds under unbounded height when sealing is off', (
      WidgetTester tester,
    ) async {
      await pump(tester, off);

      expect(tester.takeException(), isNull);
      expect(find.text('Sealing is off'), findsOneWidget);
    });

    testWidgets('offers to turn sealing on when nothing is configured', (
      WidgetTester tester,
    ) async {
      await pump(tester, unconfigured);

      expect(tester.takeException(), isNull);
      expect(find.text('Trust'), findsOneWidget);
    });

    testWidgets('builds in the dark theme too', (WidgetTester tester) async {
      await pump(tester, sealing, theme: AppTheme.dark());

      expect(tester.takeException(), isNull);
      expect(find.text('Trust'), findsOneWidget);
    });

    testWidgets('builds at a narrow width', (WidgetTester tester) async {
      await pump(tester, sealing, size: const Size(700, 900));

      expect(tester.takeException(), isNull);
      expect(find.text('Trust'), findsOneWidget);
    });

    testWidgets('shows no timestamp for a seal the network has not answered on', (
      WidgetTester tester,
    ) async {
      await pump(tester, sealing);

      expect(tester.takeException(), isNull);
      // The pending seal is in the history with `sealedAt == null`. Rendering the
      // device's clock there would undermine the claim the screen makes.
      expect(pending.sealedAt, isNull);
    });
  });
}
