import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../core/format.dart';
import '../../models/trust.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../../theme/tokens.dart';
import '../../widgets/app_badge.dart';
import '../../widgets/app_button.dart';
import '../../widgets/app_card.dart';
import '../../widgets/app_select.dart';
import '../../widgets/app_time_field.dart';
import '../../widgets/primitives.dart';
import '../../widgets/toast.dart';

/// Trust - the third ledger, natively.
///
/// The same screen as the web app's `TrustPage`, and deliberately the same three
/// presentation decisions, because each is an honesty decision rather than a layout
/// one:
///
/// * **The backlog's age is the headline, not the seal count.** "412 entries
///   sealed" is reassuring and says nothing about now. "9 entries unsealed for 6
///   days" is the only figure that distinguishes sealing working from sealing having
///   silently stopped.
/// * **What the seal does *not* prove is on the screen.** While the signing key
///   lives on the server, a seal proves the books have not changed *since* it was
///   written - not that they were right when it was.
/// * **A pending seal shows no timestamp.** `sealedAt` comes from the network, and
///   until the network has answered there is no time to show. Rendering the device's
///   clock there would undermine the exact claim the screen exists to make.
///
/// **There is no verifier here, on purpose.** Verification belongs to the
/// counterparty, in a browser, against the chain - a bank that had to install a
/// desktop app would not check the invoice, and one that trusted this client's
/// verdict would have gained nothing over trusting the business that sent the file.
class TrustScreen extends ConsumerStatefulWidget {
  const TrustScreen({super.key});

  @override
  ConsumerState<TrustScreen> createState() => _TrustScreenState();
}

class _TrustScreenState extends ConsumerState<TrustScreen> {
  bool _busy = false;
  bool _showDetails = false;

  Future<void> _run(
    Future<String> Function() action, {
    required String failure,
  }) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final String message = await action();
      if (!mounted) return;
      context.toastSuccess(message);
      // Both, because sealing changes the status *and* the history, and a screen
      // showing a fresh status next to a stale list is worse than one stale screen.
      ref.invalidate(attestationStatusProvider);
      ref.invalidate(sealHistoryProvider);
    } catch (error) {
      if (mounted) context.toastApiError(error, failure);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final AsyncValue<AttestationStatus> status = ref.watch(
      attestationStatusProvider,
    );

    return status.when(
      loading: () => const PageSkeleton(),
      error: (Object error, StackTrace _) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          const PageHeader(
            title: 'Trust',
            description: 'The third ledger - proof your books are unchanged.',
          ),
          EmptyState(
            icon: LucideIcons.triangleAlert,
            title: 'Could not load sealing status',
            description: '$error',
          ),
        ],
      ),
      // A Column, not a ListView, and not a stylistic choice: `AppShell` places
      // every screen inside `SingleChildScrollView -> Column`, so the screen is
      // handed *unbounded* height. A ListView needs a bounded one, throws
      // "Vertical viewport was given unbounded height" during layout, and in a
      // release build that renders as an empty page - a screen whose entire job is
      // to report on the books, silently showing nothing. The shell scrolls, and
      // supplies the padding too.
      data: (AttestationStatus s) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          PageHeader(
            title: 'Trust',
            description: 'The third ledger - proof your books are unchanged.',
            action: s.enabled
                ? Row(
                    mainAxisSize: MainAxisSize.min,
                    spacing: 8,
                    children: <Widget>[
                      AppButton(
                        variant: AppButtonVariant.outline,
                        size: AppButtonSize.sm,
                        leftIcon: LucideIcons.refreshCw,
                        label: 'Check the chain',
                        loading: _busy,
                        onPressed: () => _run(() async {
                          final ReconcileResult r = await ref
                              .read(trustApiProvider)
                              .reconcile();
                          return r.agrees == true
                              ? 'The chain and your database agree.'
                              : 'Reconciled against the chain (head #${r.chainHead ?? 0}).';
                        }, failure: 'Could not reconcile'),
                      ),
                      AppButton(
                        size: AppButtonSize.sm,
                        leftIcon: LucideIcons.shieldCheck,
                        label: 'Seal now',
                        loading: _busy,
                        onPressed: s.ready
                            ? () => _run(() async {
                                final SealNowResult r = await ref
                                    .read(trustApiProvider)
                                    .sealNow();
                                return r.message;
                              }, failure: 'Could not seal')
                            : null,
                      ),
                    ],
                  )
                : null,
          ),

          if (s.warnings.isNotEmpty) _Warnings(warnings: s.warnings),

          _SealingState(status: s),
          const SizedBox(height: 16),

          // Two per row rather than three: a desktop window is often narrower than
          // a browser, and three tiles at 1100px crushed the sub-labels onto four
          // lines each.
          LayoutBuilder(
            builder: (BuildContext context, BoxConstraints box) {
              final int columns = box.maxWidth >= 900
                  ? 3
                  : (box.maxWidth >= 560 ? 2 : 1);
              final List<Widget> tiles = <Widget>[
                _Metric(
                  label: 'Entries sealed',
                  value: formatNumber(s.entriesSealed),
                  sub:
                      'across ${formatNumber(s.sealsConfirmed)} '
                      'seal${s.sealsConfirmed == 1 ? '' : 's'}',
                ),
                _Metric(
                  label: 'Waiting to be sealed',
                  value: formatNumber(s.unsealedEntries),
                  sub: s.daysUnsealed == null
                      ? 'nothing outstanding'
                      : 'oldest is ${s.daysUnsealed} '
                            'day${s.daysUnsealed == 1 ? '' : 's'} old',
                  bad: (s.daysUnsealed ?? 0) >= 2,
                ),
                _Metric(
                  label: 'Chain',
                  value: !s.chain.reachable
                      ? 'unreachable'
                      : s.chain.head == null
                      ? '—'
                      : '#${s.chain.head}',
                  sub: !s.chain.reachable
                      ? (s.chain.error ?? 'could not be read')
                      : s.chain.agreesWithLocal == false
                      ? 'disagrees with this database'
                      : '${s.network ?? ''} · '
                            '${formatNumber(s.chain.entries ?? 0)} entries',
                  bad: s.chain.agreesWithLocal == false,
                ),
              ];
              return GridView.count(
                crossAxisCount: columns,
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                mainAxisSpacing: 16,
                crossAxisSpacing: 16,
                childAspectRatio: columns == 1 ? 4.2 : 2.1,
                children: tiles,
              );
            },
          ),

          const SizedBox(height: 16),
          _History(),

          const SizedBox(height: 16),
          _Settings(
            status: s,
            busy: _busy,
            showDetails: _showDetails,
            onToggleDetails: () => setState(() => _showDetails = !_showDetails),
            onCadence: (SealCadence cadence) => _run(() async {
              await ref.read(trustApiProvider).setCadence(cadence);
              return 'Sealing schedule updated.';
            }, failure: 'Could not change the schedule'),
            onSealTime: (String time) => _run(() async {
              await ref
                  .read(trustApiProvider)
                  .setCadence(s.cadence, sealTime: time);
              return 'Daily seal set for $time ${s.timezone}.';
            }, failure: 'Could not change the sealing time'),
            onEnable: () => _run(() async {
              await ref.read(trustApiProvider).enable();
              return 'Sealing is on. Your books are now committed to Stellar.';
            }, failure: 'Could not switch sealing on'),
            onDisable: () => _run(() async {
              await ref.read(trustApiProvider).disable();
              return 'Sealing is off. Everything already sealed stays verifiable.';
            }, failure: 'Could not switch sealing off'),
          ),

          const SizedBox(height: 16),
          AppCard(
            padding: const EdgeInsets.all(20),
            child: _Explainer(colour: t),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------

class _Warnings extends StatelessWidget {
  const _Warnings({required this.warnings});

  final List<String> warnings;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    // Server-ordered, worst first, and rendered in that order. Sorting here would
    // put a configuration note above a chain divergence.
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        for (int i = 0; i < warnings.length; i++)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: i == 0 ? t.warningBg : t.surfaceSunken,
                border: Border.all(
                  color: i == 0 ? t.warning.withValues(alpha: 0.3) : t.border,
                ),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                spacing: 10,
                children: <Widget>[
                  Icon(
                    LucideIcons.triangleAlert,
                    size: 16,
                    color: i == 0 ? t.warning : t.contentMuted,
                  ),
                  Expanded(
                    child: Text(
                      warnings[i],
                      style: TextStyle(
                        fontSize: 13,
                        height: 1.5,
                        color: i == 0 ? t.warning : t.contentSecondary,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
      ],
    );
  }
}

class _SealingState extends StatelessWidget {
  const _SealingState({required this.status});

  final AttestationStatus status;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final bool on = status.isSealing;

    return AppCard(
      padding: const EdgeInsets.all(20),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        spacing: 12,
        children: <Widget>[
          Container(
            width: 40,
            height: 40,
            decoration: BoxDecoration(
              color: on ? t.successBg : t.surfaceSunken,
              shape: BoxShape.circle,
            ),
            child: Icon(
              on ? LucideIcons.shieldCheck : LucideIcons.shieldOff,
              size: 20,
              color: on ? t.success : t.contentMuted,
            ),
          ),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  on ? 'Your books are being sealed' : 'Sealing is off',
                  style: TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.w600,
                    color: t.content,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  status.lastSeal?.sealedAt != null
                      ? 'Last sealed ${formatDateTime(status.lastSeal!.sealedAt!)}'
                      : status.openSeal != null
                      ? 'Seal #${status.openSeal!.seq} is awaiting confirmation'
                      : 'Nothing sealed yet',
                  style: TextStyle(fontSize: 13, color: t.contentMuted),
                ),
              ],
            ),
          ),
          if (status.contractUrl != null)
            AppTextLink(
              label: 'View the contract',
              trailingIcon: LucideIcons.externalLink,
              onTap: () => launchUrl(Uri.parse(status.contractUrl!)),
            ),
        ],
      ),
    );
  }
}

class _Metric extends StatelessWidget {
  const _Metric({
    required this.label,
    required this.value,
    required this.sub,
    this.bad = false,
  });

  final String label;
  final String value;
  final String sub;
  final bool bad;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    return AppCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: MainAxisAlignment.center,
        children: <Widget>[
          Text(
            label.toUpperCase(),
            style: TextStyle(
              fontSize: 11,
              fontWeight: FontWeight.w600,
              letterSpacing: 0.6,
              color: t.contentMuted,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            value,
            style: TextStyle(
              fontSize: 24,
              fontWeight: FontWeight.w600,
              fontFeatures: const <FontFeature>[FontFeature.tabularFigures()],
              color: bad ? t.danger : t.content,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            sub,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(fontSize: 12, color: t.contentMuted),
          ),
        ],
      ),
    );
  }
}

class _History extends ConsumerWidget {
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AppTokens t = context.tokens;
    final AsyncValue<SealPage> page = ref.watch(sealHistoryProvider);

    return AppCard(
      padding: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Padding(
            padding: const EdgeInsets.all(20),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              spacing: 12,
              children: <Widget>[
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(
                        'Seal history',
                        style: TextStyle(
                          fontSize: 14,
                          fontWeight: FontWeight.w600,
                          color: t.content,
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        'Each seal commits a batch of entries. '
                        'Every one links to the one before it.',
                        style: TextStyle(fontSize: 13, color: t.contentMuted),
                      ),
                    ],
                  ),
                ),
                if (page.valueOrNull?.items.isNotEmpty ?? false)
                  AppBadge(
                    page.valueOrNull!.continuous
                        ? 'Unbroken chain'
                        : 'Chain broken',
                    tone: page.valueOrNull!.continuous
                        ? BadgeTone.success
                        : BadgeTone.danger,
                  ),
              ],
            ),
          ),
          page.when(
            loading: () => const Padding(
              padding: EdgeInsets.fromLTRB(20, 0, 20, 20),
              child: SkeletonText(lines: 3),
            ),
            error: (Object error, StackTrace _) => Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
              child: Text(
                '$error',
                style: TextStyle(fontSize: 13, color: t.danger),
              ),
            ),
            data: (SealPage data) => data.items.isEmpty
                ? const Padding(
                    padding: EdgeInsets.fromLTRB(20, 0, 20, 20),
                    child: EmptyState(
                      icon: LucideIcons.clock,
                      title: 'Nothing sealed yet',
                      description:
                          'Post an entry and press Seal now, or wait for '
                          "tonight's scheduled seal.",
                    ),
                  )
                : Column(
                    children: <Widget>[
                      for (final Seal seal in data.items) _SealRow(seal: seal),
                    ],
                  ),
          ),
        ],
      ),
    );
  }
}

class _SealRow extends StatelessWidget {
  const _SealRow({required this.seal});

  final Seal seal;

  (String, BadgeTone) get _badge => switch (seal.status) {
    SealStatus.confirmed => ('On chain', BadgeTone.success),
    SealStatus.submitted => ('Awaiting confirmation', BadgeTone.info),
    SealStatus.pending => ('Queued', BadgeTone.warning),
    SealStatus.failed => ('Failed', BadgeTone.danger),
  };

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final (String label, BadgeTone tone) = _badge;

    return Container(
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 16),
      decoration: BoxDecoration(
        border: Border(top: BorderSide(color: t.border)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        spacing: 12,
        children: <Widget>[
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                // A Wrap rather than a Row: the seal number, its status badge and
                // the explorer link do not fit side by side once the window is
                // narrow, and a Row there overflowed by ~60px - which in Flutter
                // means the status badge was simply not drawn. Truncating would be
                // worse than wrapping: "Seal #5" with no badge reads as a confirmed
                // seal, and on this screen that is the one thing it must not do.
                Wrap(
                  spacing: 8,
                  runSpacing: 4,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: <Widget>[
                    Text(
                      'Seal #${seal.seq}',
                      style: TextStyle(
                        fontSize: 14,
                        fontWeight: FontWeight.w600,
                        fontFeatures: const <FontFeature>[
                          FontFeature.tabularFigures(),
                        ],
                        color: t.content,
                      ),
                    ),
                    AppBadge(label, tone: tone),
                    if (seal.isConfirmed && seal.explorerUrl != null)
                      AppTextLink(
                        label: 'transaction',
                        trailingIcon: LucideIcons.externalLink,
                        onTap: () => launchUrl(Uri.parse(seal.explorerUrl!)),
                      ),
                  ],
                ),
                const SizedBox(height: 4),
                Text(
                  '${formatNumber(seal.entryCount)} '
                  'entr${seal.entryCount == 1 ? 'y' : 'ies'} · '
                  '${seal.entryDateFrom == seal.entryDateTo ? seal.entryDateFrom : '${seal.entryDateFrom} to ${seal.entryDateTo}'}',
                  style: TextStyle(fontSize: 13, color: t.contentSecondary),
                ),
                const SizedBox(height: 4),
                Text(
                  seal.merkleRoot,
                  style: TextStyle(
                    fontSize: 11,
                    fontFamily: 'monospace',
                    color: t.contentMuted,
                  ),
                ),
                if (seal.lastError != null && !seal.isConfirmed) ...<Widget>[
                  const SizedBox(height: 4),
                  Text(
                    seal.lastError!,
                    style: TextStyle(fontSize: 12, color: t.danger),
                  ),
                ],
              ],
            ),
          ),
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: <Widget>[
              // No timestamp until the network gives one.
              Text(
                seal.sealedAt == null ? '—' : formatDateTime(seal.sealedAt!),
                style: TextStyle(fontSize: 13, color: t.contentSecondary),
              ),
              Text(
                seal.sealedAt != null
                    ? 'network time'
                    : seal.status == SealStatus.failed
                    ? 'never sealed'
                    : 'not yet confirmed',
                style: TextStyle(fontSize: 12, color: t.contentMuted),
              ),
              const SizedBox(height: 4),
              Text(
                'proves 1 entry with ${seal.treeDepth} '
                'hash${seal.treeDepth == 1 ? '' : 'es'}',
                style: TextStyle(fontSize: 12, color: t.contentMuted),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _Settings extends StatelessWidget {
  const _Settings({
    required this.status,
    required this.busy,
    required this.showDetails,
    required this.onToggleDetails,
    required this.onCadence,
    required this.onSealTime,
    required this.onEnable,
    required this.onDisable,
  });

  final AttestationStatus status;
  final bool busy;
  final bool showDetails;
  final VoidCallback onToggleDetails;
  final ValueChanged<SealCadence> onCadence;
  final ValueChanged<String> onSealTime;
  final VoidCallback onEnable;
  final VoidCallback onDisable;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;

    return AppCard(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Row(
            children: <Widget>[
              Expanded(
                child: Text(
                  'Settings',
                  style: TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.w600,
                    color: t.content,
                  ),
                ),
              ),
              AppButton(
                variant: AppButtonVariant.ghost,
                size: AppButtonSize.sm,
                label: showDetails ? 'Hide details' : 'Show details',
                onPressed: onToggleDetails,
              ),
            ],
          ),
          const SizedBox(height: 16),
          // Two across at width, stacked when narrow - the web's grid.
          LayoutBuilder(
            builder: (BuildContext context, BoxConstraints box) {
              final Widget cadence = AppSelect(
                label: 'How often to seal',
                value: status.cadence.wire,
                enabled: status.enabled && !busy,
                hint:
                    'Sealing more often narrows the window in which history could '
                    'be rewritten. On Stellar it costs a fraction of a cent, so '
                    'daily is the default.',
                options: <SelectOption>[
                  for (final SealCadence c in SealCadence.values)
                    SelectOption(value: c.wire, label: c.label),
                ],
                onChanged: (String wire) => onCadence(SealCadence.parse(wire)),
              );

              // Only for the daily cadence: for the other two there is no time to
              // choose, and a disabled field implying otherwise reads worse than no
              // field at all.
              final Widget? time = status.cadence == SealCadence.daily
                  ? AppTimeField(
                      label: 'What time of day',
                      value: status.effectiveSealTime,
                      enabled: status.enabled && !busy,
                      hint:
                          // Double-quoted: the copy contains apostrophes, and
                          // escaping them inside single quotes reads worse than
                          // switching the delimiter.
                          "${status.timezone} - your organization's clock, not "
                          "the server's. Any minute of the day."
                          "${status.sealTime == null ? ' Currently following the server default.' : ''}",
                      onChanged: onSealTime,
                    )
                  : null;

              if (time == null) return cadence;
              if (box.maxWidth < 640) {
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  spacing: 16,
                  children: <Widget>[cadence, time],
                );
              }
              return Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                spacing: 16,
                children: <Widget>[
                  Expanded(child: cadence),
                  Expanded(child: time),
                ],
              );
            },
          ),
          const SizedBox(height: 16),
          Align(
            alignment: Alignment.centerLeft,
            child: status.enabled
                ? AppButton(
                    variant: AppButtonVariant.outline,
                    leftIcon: LucideIcons.shieldOff,
                    label: 'Turn sealing off',
                    loading: busy,
                    onPressed: onDisable,
                  )
                : AppButton(
                    leftIcon: LucideIcons.shieldCheck,
                    label: 'Turn sealing on',
                    loading: busy,
                    onPressed: onEnable,
                  ),
          ),
          const SizedBox(height: 6),
          Text(
            'Turning it off stops new seals. Everything already sealed stays '
            'verifiable forever.',
            style: TextStyle(fontSize: 12, color: t.contentMuted),
          ),
          if (showDetails) _Details(status: status),
        ],
      ),
    );
  }
}

/// One label-over-value pair from the details block.
///
/// Extracted so the one-column and two-column arrangements share it rather than
/// each building its own - a duplicated cell is how the two layouts start
/// disagreeing about padding.
class _DetailRow extends StatelessWidget {
  const _DetailRow({required this.label, required this.value});

  final String label;
  final String? value;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            label,
            style: TextStyle(
              fontSize: 12,
              fontWeight: FontWeight.w500,
              color: t.contentMuted,
            ),
          ),
          const SizedBox(height: 2),
          SelectableText(
            value ?? '—',
            style: TextStyle(
              fontSize: 12,
              fontFamily: 'monospace',
              color: t.content,
            ),
          ),
        ],
      ),
    );
  }
}

class _Details extends StatelessWidget {
  const _Details({required this.status});

  final AttestationStatus status;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final List<(String, String?)> rows = <(String, String?)>[
      ('Network', status.network),
      ('Contract', status.contractId),
      ('Your namespace on chain', status.orgNamespace),
      ('Signing account', status.signerPublicKey),
      (
        'Registered',
        status.registeredAt == null
            ? null
            : formatDateTime(status.registeredAt!),
      ),
    ];

    return Container(
      margin: const EdgeInsets.only(top: 16),
      padding: const EdgeInsets.only(top: 16),
      decoration: BoxDecoration(
        border: Border(top: BorderSide(color: t.border)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          // Two columns at width, one when narrow - the same shape the web page
          // uses. Stacked, these five rows pushed the signing-key note off the
          // bottom of a desktop window, and that note is the honest limitation the
          // whole screen is built around.
          LayoutBuilder(
            builder: (BuildContext context, BoxConstraints box) {
              final bool wide = box.maxWidth >= 640;
              final List<Widget> cells = <Widget>[
                for (final (String label, String? value) in rows)
                  _DetailRow(label: label, value: value),
              ];

              if (!wide) {
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: cells,
                );
              }

              // Paired left-to-right so the reading order matches the single
              // column: Network beside Contract, namespace beside signer.
              final List<Widget> pairs = <Widget>[];
              for (int i = 0; i < cells.length; i += 2) {
                pairs.add(
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    spacing: 24,
                    children: <Widget>[
                      Expanded(child: cells[i]),
                      Expanded(
                        child: i + 1 < cells.length
                            ? cells[i + 1]
                            : const SizedBox.shrink(),
                      ),
                    ],
                  ),
                );
              }
              return Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: pairs,
              );
            },
          ),
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: status.externalSigner ? t.successBg : t.surfaceSunken,
              border: Border.all(
                color: status.externalSigner
                    ? t.success.withValues(alpha: 0.3)
                    : t.border,
              ),
              borderRadius: BorderRadius.circular(8),
            ),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              spacing: 10,
              children: <Widget>[
                Icon(
                  LucideIcons.keyRound,
                  size: 16,
                  color: status.externalSigner ? t.success : t.contentMuted,
                ),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(
                        status.externalSigner
                            ? 'The signing key is held outside this server'
                            : 'The signing key is held on this server',
                        style: TextStyle(
                          fontSize: 13,
                          fontWeight: FontWeight.w500,
                          color: status.externalSigner
                              ? t.success
                              : t.contentSecondary,
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        status.externalSigner
                            ? 'Sealing needs a signature this server cannot produce '
                                  'alone, so a seal is evidence about the books rather '
                                  'than only about this machine.'
                            : 'A seal therefore proves your books have not changed '
                                  'since it was written - not that they were correct '
                                  'when it was. Adding your accountant as a co-signer '
                                  'on the Stellar account closes that gap.',
                        style: TextStyle(
                          fontSize: 13,
                          height: 1.5,
                          color: status.externalSigner
                              ? t.success
                              : t.contentSecondary,
                        ),
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
}

class _Explainer extends StatelessWidget {
  const _Explainer({required this.colour});

  final AppTokens colour;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = colour;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text(
          'What the third ledger is',
          style: TextStyle(
            fontSize: 14,
            fontWeight: FontWeight.w600,
            color: t.content,
          ),
        ),
        const SizedBox(height: 6),
        Text(
          'Your accounts already keep two ledgers: the journal, which records what '
          'happened to the money, and the audit trail, which records who did it. '
          'Both live in your own database - which means both are trusted by you and '
          'by nobody else. Anyone with your database password could rewrite either, '
          'and no bank, buyer, or auditor could tell.',
          style: TextStyle(
            fontSize: 13,
            height: 1.6,
            color: t.contentSecondary,
          ),
        ),
        const SizedBox(height: 8),
        Text(
          'The proof ledger is the third. Periodically it computes a single '
          'fingerprint of your journal and writes it to a public network. Later, '
          'anyone you choose can be handed one invoice and check it against that '
          'fingerprint - and see that it has not been altered since. They need no '
          'account, no wallet, and no access to anything else in your books.',
          style: TextStyle(
            fontSize: 13,
            height: 1.6,
            color: t.contentSecondary,
          ),
        ),
        const SizedBox(height: 12),
        Text(
          'No amount, customer, product, or account number ever leaves this '
          'server. Only 32-byte fingerprints, a count, and a total.',
          style: TextStyle(fontSize: 12, color: t.contentMuted),
        ),
      ],
    );
  }
}
