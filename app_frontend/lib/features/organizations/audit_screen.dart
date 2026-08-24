import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/format.dart';
import '../../models/organization.dart';
import '../../state/data_providers.dart';
import '../../theme/app_theme.dart';
import '../../theme/oklch.dart';
import '../../theme/tokens.dart';
import '../../widgets/app_badge.dart';
import '../../widgets/app_button.dart';
import '../../widgets/app_card.dart';
import '../../widgets/app_select.dart';
import '../../widgets/primitives.dart';

/// The audit log - an append-only record of every action taken in this organization.
///
/// **Cursor-paginated, not offset.** The trail is append-heavy: offsets both degrade with
/// depth and shift rows under the reader as new events land, so the API is cursor-based and
/// the screen follows `next_cursor`.
class AuditScreen extends ConsumerStatefulWidget {
  const AuditScreen({super.key});

  @override
  ConsumerState<AuditScreen> createState() => _AuditScreenState();
}

class _AuditScreenState extends ConsumerState<AuditScreen> {
  AuditFilter _filter = const AuditFilter();

  static const Map<String, BadgeTone> _severityTones = <String, BadgeTone>{
    'info': BadgeTone.neutral,
    'warning': BadgeTone.warning,
    'critical': BadgeTone.danger,
  };

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final List<String> actions =
        ref.watch(auditActionsProvider).valueOrNull ?? const <String>[];
    final AsyncValue<AuditFeed> feed = ref.watch(auditFeedProvider(_filter));
    final AuditFeed? data = feed.valueOrNull;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        const PageHeader(
          title: 'Audit log',
          description:
              'An append-only record of every action taken in this organization.',
        ),

        AppCard(
          child: CardBody(
            padding: const EdgeInsets.all(16),
            child: Row(
              spacing: 12,
              children: <Widget>[
                Icon(LucideIcons.filter, size: 16, color: t.contentMuted),
                SizedBox(
                  width: 260,
                  child: AppSelect(
                    value: _filter.action ?? '',
                    placeholder: 'All actions',
                    options: <SelectOption>[
                      for (final String action in actions)
                        SelectOption(value: action, label: action),
                    ],
                    onChanged: (String next) => setState(
                      () => _filter = AuditFilter(
                        action: next.isEmpty ? null : next,
                        severity: _filter.severity,
                      ),
                    ),
                  ),
                ),
                SizedBox(
                  width: 200,
                  child: AppSelect(
                    value: _filter.severity ?? '',
                    placeholder: 'All severities',
                    options: const <SelectOption>[
                      SelectOption(value: 'info', label: 'Info'),
                      SelectOption(value: 'warning', label: 'Warning'),
                      SelectOption(value: 'critical', label: 'Critical'),
                    ],
                    onChanged: (String next) => setState(
                      () => _filter = AuditFilter(
                        action: _filter.action,
                        severity: next.isEmpty ? null : next,
                      ),
                    ),
                  ),
                ),
                if (_filter.action != null || _filter.severity != null)
                  AppButton(
                    onPressed: () =>
                        setState(() => _filter = const AuditFilter()),
                    variant: AppButtonVariant.ghost,
                    size: AppButtonSize.sm,
                    label: 'Clear filters',
                  ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 16),

        AppCard(
          child: feed.isLoading
              ? Padding(
                  padding: const EdgeInsets.all(20),
                  child: Column(
                    spacing: 12,
                    children: <Widget>[
                      for (int index = 0; index < 8; index++)
                        const Skeleton(height: 40),
                    ],
                  ),
                )
              : (data == null || data.entries.isEmpty)
              ? EmptyState(
                  icon: LucideIcons.fileText,
                  title: 'No matching events',
                  description:
                      _filter.action != null || _filter.severity != null
                      ? 'Try widening your filters.'
                      : 'Actions across your organization will appear here as they '
                            'happen.',
                )
              : Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: <Widget>[
                    for (final AuditEntry entry in data.entries)
                      _AuditRow(
                        entry: entry,
                        tone:
                            _severityTones[entry.severity] ?? BadgeTone.neutral,
                        isLast: entry == data.entries.last,
                      ),
                  ],
                ),
        ),

        if (data != null && data.hasMore) ...<Widget>[
          const SizedBox(height: 16),
          Center(
            child: AppButton(
              onPressed: () =>
                  ref.read(auditFeedProvider(_filter).notifier).loadMore(),
              loading: data.isLoadingMore,
              variant: AppButtonVariant.secondary,
              label: 'Load more',
            ),
          ),
        ],
      ],
    );
  }
}

class _AuditRow extends StatefulWidget {
  const _AuditRow({
    required this.entry,
    required this.tone,
    required this.isLast,
  });

  final AuditEntry entry;
  final BadgeTone tone;
  final bool isLast;

  @override
  State<_AuditRow> createState() => _AuditRowState();
}

class _AuditRowState extends State<_AuditRow> {
  bool _hovered = false;

  /// Render an audit diff value for display.
  ///
  /// The values are dynamic - a diff can hold a string, number, boolean, null, or a nested
  /// JSONB object. Passing an object to string interpolation yields something like
  /// `_Map<String, dynamic>`, which is worse than useless in an audit trail, so objects are
  /// serialised instead.
  static String _renderValue(Object? value) {
    if (value == null) return '-';
    if (value is Map || value is List) return jsonEncode(value);
    return '$value';
  }

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final AuditEntry entry = widget.entry;

    return MouseRegion(
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
        decoration: BoxDecoration(
          color: _hovered ? t.surfaceHover.at(0.4) : Colors.transparent,
          border: widget.isLast
              ? null
              : Border(bottom: BorderSide(color: t.border)),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          spacing: 12,
          children: <Widget>[
            Padding(
              padding: const EdgeInsets.only(top: 2),
              child: AppBadge(entry.severity, tone: widget.tone),
            ),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    entry.summary ?? entry.action,
                    style: TextStyle(
                      fontSize: 13,
                      color: t.content,
                      height: 1.35,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Wrap(
                    spacing: 10,
                    runSpacing: 4,
                    crossAxisAlignment: WrapCrossAlignment.center,
                    children: <Widget>[
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 6,
                          vertical: 2,
                        ),
                        decoration: BoxDecoration(
                          color: t.surfaceSunken,
                          borderRadius: BorderRadius.circular(Radii.xs),
                        ),
                        child: Text(
                          entry.action,
                          style: monoStyle(fontSize: 11, color: t.contentMuted),
                        ),
                      ),
                      Text(
                        entry.actor.display,
                        style: TextStyle(fontSize: 11, color: t.contentMuted),
                      ),
                      if (entry.ipAddress != null)
                        Text(
                          entry.ipAddress!,
                          style: TextStyle(fontSize: 11, color: t.contentMuted),
                        ),
                      Tooltip(
                        message: formatDateTime(entry.createdAt),
                        child: Text(
                          formatRelative(entry.createdAt),
                          style: TextStyle(fontSize: 11, color: t.contentMuted),
                        ),
                      ),
                    ],
                  ),

                  // The field-level diff. Shown inline because "what changed" is usually the
                  // reason someone opened this.
                  if (entry.changes.isNotEmpty)
                    Container(
                      margin: const EdgeInsets.only(top: 8),
                      padding: const EdgeInsets.only(left: 12),
                      decoration: BoxDecoration(
                        border: Border(
                          left: BorderSide(color: t.border, width: 2),
                        ),
                      ),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        spacing: 4,
                        children: <Widget>[
                          for (final MapEntry<String, AuditChange> change
                              in entry.changes.entries)
                            Text.rich(
                              TextSpan(
                                children: <InlineSpan>[
                                  TextSpan(
                                    text: '${change.key}: ',
                                    style: TextStyle(
                                      color: t.contentSecondary,
                                      fontWeight: FontWeight.w500,
                                    ),
                                  ),
                                  // A snapshot is printed as one value. Showing it as
                                  // "- → value" would invent a previous value that was
                                  // never recorded - see [AuditChange].
                                  if (change.value.isDiff) ...<InlineSpan>[
                                    TextSpan(
                                      text: _renderValue(change.value.before),
                                      style: const TextStyle(
                                        decoration: TextDecoration.lineThrough,
                                      ),
                                    ),
                                    const TextSpan(text: ' → '),
                                    TextSpan(
                                      text: _renderValue(change.value.after),
                                      style: TextStyle(color: t.content),
                                    ),
                                  ] else
                                    TextSpan(
                                      text: _renderValue(change.value.value),
                                      style: TextStyle(color: t.content),
                                    ),
                                ],
                              ),
                              style: TextStyle(
                                fontSize: 11,
                                color: t.contentMuted,
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
      ),
    );
  }
}
