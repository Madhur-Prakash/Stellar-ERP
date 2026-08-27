import 'package:flutter/material.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../theme/tokens.dart';
import 'app_input.dart';

/// A time of day, picked from two columns. Matches [AppSelect]'s field metrics.
///
/// **The whole field opens the picker**, not a glyph inside it. That is the point:
/// a control that looks like one target and only responds on a small part of itself
/// teaches people to click twice.
///
/// Two columns rather than one list of 1,440 rows, and rather than Material's
/// `showTimePicker`. The dial is designed for a phone and asks for a drag on a
/// desktop, where a mouse is worse at arcs than at lists; the columns also let the
/// panel carry the app's own radius and tokens, which the platform dialog does not.
///
/// Every tap produces a complete, valid time, so [onChanged] fires immediately -
/// there is no half-entered state to guard against, which is the failure mode a
/// typed `HH:MM` field has.
///
/// The web counterpart is `components/ui/TimePicker.tsx`, deliberately the same
/// shape: two columns, `Now` and `Done`, the selected cell in the primary colour.
class AppTimeField extends StatefulWidget {
  const AppTimeField({
    super.key,
    required this.value,
    required this.onChanged,
    this.label,
    this.hint,
    this.error,
    this.enabled = true,
    this.minuteStep = 1,
    this.required = false,
  });

  /// `HH:MM` on a 24-hour clock.
  final String value;
  final ValueChanged<String> onChanged;

  final String? label;
  final String? hint;
  final String? error;
  final bool enabled;
  final bool required;

  /// Minute granularity. 1 offers every minute; 5 or 15 shorten the column.
  final int minuteStep;

  @override
  State<AppTimeField> createState() => _AppTimeFieldState();
}

/// Shared with `frontend/src/components/ui/TimePicker.tsx`. Explicit numbers on
/// both sides rather than each reaching for its own scale, because the two panels
/// have to agree to the pixel and that is exactly where they would drift.
const double _panelWidth = 220;
const double _columnHeight = 200;
const double _rowHeight = 32;

class _AppTimeFieldState extends State<AppTimeField> {
  final LayerLink _link = LayerLink();
  OverlayEntry? _overlay;

  /// Created when the panel opens, disposed when it closes.
  ///
  /// They used to be built inside `build`, which meant a new controller on every
  /// rebuild - one per tap on a column - and not one of them was ever disposed.
  /// `initialScrollOffset` also only applies to the *first* attachment, so the
  /// replacement controllers silently stopped honouring it and the columns jumped
  /// back to the top mid-interaction.
  ScrollController? _hourScroll;
  ScrollController? _minuteScroll;

  /// Held locally while the panel is open so the columns respond to a tap
  /// immediately, rather than waiting for the request to come back and the parent
  /// to re-render. The parent's value still wins on close.
  late int _hour;
  late int _minute;

  @override
  void dispose() {
    _close();
    super.dispose();
  }

  /// Parses `HH:MM`, falling back to midnight rather than throwing. A malformed
  /// value here means the server sent something unexpected, and a crash on this
  /// screen would be a worse answer than a wrong default that can be corrected.
  (int, int) get _parsed {
    final List<String> parts = widget.value.split(':');
    final int? hour = parts.isEmpty ? null : int.tryParse(parts[0]);
    final int? minute = parts.length < 2 ? null : int.tryParse(parts[1]);
    return (
      hour != null && hour >= 0 && hour <= 23 ? hour : 0,
      minute != null && minute >= 0 && minute <= 59 ? minute : 0,
    );
  }

  String _format(int hour, int minute) =>
      '${hour.toString().padLeft(2, '0')}:${minute.toString().padLeft(2, '0')}';

  void _emit() {
    widget.onChanged(_format(_hour, _minute));
    _overlay?.markNeedsBuild();
  }

  void _close() {
    _overlay?.remove();
    _overlay?.dispose();
    _overlay = null;
    _hourScroll?.dispose();
    _minuteScroll?.dispose();
    _hourScroll = null;
    _minuteScroll = null;
  }

  /// Where a column must sit for [index] to be centred.
  ///
  /// Clamped at both ends: an unclamped offset for 00 is negative and for 59 is
  /// past the extent, and `ScrollController` accepts both at construction only to
  /// snap back on the first frame - which reads as the panel flinching open.
  double _centre(int index, int count) {
    final double raw = index * _rowHeight - (_columnHeight - _rowHeight) / 2;
    final double max = count * _rowHeight - _columnHeight;
    return raw.clamp(0.0, max < 0 ? 0.0 : max);
  }

  /// Scroll both columns to the current selection, after the frame that built
  /// them. Used by `Now`, which changes the value without reopening the panel.
  void _recentre() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final int minuteCount = (60 / widget.minuteStep).ceil();
      if (_hourScroll?.hasClients ?? false) {
        _hourScroll!.jumpTo(_centre(_hour, 24));
      }
      if (_minuteScroll?.hasClients ?? false) {
        _minuteScroll!.jumpTo(
          _centre(_minute ~/ widget.minuteStep, minuteCount),
        );
      }
    });
  }

  void _open() {
    if (!widget.enabled || _overlay != null) return;
    // Destructured into locals first: Dart's pattern assignment only targets local
    // variables, not fields.
    final (int hour, int minute) = _parsed;
    _hour = hour;
    _minute = minute;

    final int minuteCount = (60 / widget.minuteStep).ceil();
    _hourScroll = ScrollController(initialScrollOffset: _centre(_hour, 24));
    _minuteScroll = ScrollController(
      initialScrollOffset: _centre(_minute ~/ widget.minuteStep, minuteCount),
    );

    _overlay = OverlayEntry(
      builder: (BuildContext context) => Stack(
        children: <Widget>[
          // A full-screen catcher behind the panel: without it a tap outside lands
          // on whatever is underneath and the panel stays open over it.
          Positioned.fill(
            child: GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: () => setState(_close),
              child: const SizedBox.shrink(),
            ),
          ),
          CompositedTransformFollower(
            link: _link,
            targetAnchor: Alignment.bottomLeft,
            followerAnchor: Alignment.topLeft,
            offset: const Offset(0, 6),
            child: _panel(context),
          ),
        ],
      ),
    );
    Overlay.of(context).insert(_overlay!);
    setState(() {});
  }

  Widget _panel(BuildContext context) {
    final AppTokens t = context.tokens;
    final List<int> hours = List<int>.generate(24, (int i) => i);
    final List<int> minutes = List<int>.generate(
      (60 / widget.minuteStep).ceil(),
      (int i) => i * widget.minuteStep,
    );

    return Material(
      color: Colors.transparent,
      child: Container(
        width: _panelWidth,
        decoration: BoxDecoration(
          color: t.surface,
          borderRadius: BorderRadius.circular(Radii.xl),
          border: Border.all(color: t.border),
          boxShadow: <BoxShadow>[
            BoxShadow(
              color: Colors.black.withValues(alpha: 0.12),
              blurRadius: 16,
              offset: const Offset(0, 4),
            ),
          ],
        ),
        clipBehavior: Clip.antiAlias,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Container(
              decoration: BoxDecoration(
                border: Border(bottom: BorderSide(color: t.border)),
              ),
              child: Row(
                children: <Widget>[
                  Expanded(child: _heading(t, 'Hour')),
                  Expanded(child: _heading(t, 'Minute')),
                ],
              ),
            ),
            SizedBox(
              height: _columnHeight,
              child: Row(
                children: <Widget>[
                  Expanded(
                    child: _column(hours, _hour, _hourScroll, (int v) {
                      _hour = v;
                      _emit();
                    }),
                  ),
                  Container(width: 1, color: t.border),
                  Expanded(
                    child: _column(minutes, _minute, _minuteScroll, (int v) {
                      _minute = v;
                      _emit();
                    }),
                  ),
                ],
              ),
            ),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
              decoration: BoxDecoration(
                border: Border(top: BorderSide(color: t.border)),
              ),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: <Widget>[
                  _action(t, 'Now', t.contentSecondary, () {
                    final DateTime now = DateTime.now();
                    _hour = now.hour;
                    // Snapped to the step, or a 5-minute column would highlight
                    // nothing at 17:43 and look broken rather than rounded.
                    _minute =
                        (now.minute ~/ widget.minuteStep) * widget.minuteStep;
                    _emit();
                    _recentre();
                  }),
                  _action(t, 'Done', t.primary, () => setState(_close)),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _heading(AppTokens t, String text) => Padding(
    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
    child: Text(
      text.toUpperCase(),
      style: TextStyle(
        fontSize: 10,
        fontWeight: FontWeight.w600,
        letterSpacing: 0.6,
        color: t.contentMuted,
      ),
    ),
  );

  Widget _column(
    List<int> values,
    int selected,
    ScrollController? controller,
    ValueChanged<int> onTap,
  ) {
    return ListView.builder(
      padding: EdgeInsets.zero,
      // Opens with the selection centred rather than at midnight, so 17:30 does
      // not require scrolling most of the way down to see what is set.
      controller: controller,
      itemCount: values.length,
      itemExtent: _rowHeight,
      itemBuilder: (BuildContext context, int i) {
        final int v = values[i];
        final bool isSelected = v == selected;
        return _Cell(
          label: v.toString().padLeft(2, '0'),
          selected: isSelected,
          onTap: () => onTap(v),
        );
      },
    );
  }

  Widget _action(AppTokens t, String label, Color colour, VoidCallback onTap) =>
      MouseRegion(
        cursor: SystemMouseCursors.click,
        child: GestureDetector(
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 6),
            child: Text(
              label,
              style: TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.w500,
                color: colour,
              ),
            ),
          ),
        ),
      );

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final (int hour, int minute) = _parsed;
    final bool errorState = widget.error != null;
    final bool hasError =
        widget.error != null && widget.error!.trim().isNotEmpty;

    final Widget field = CompositedTransformTarget(
      link: _link,
      child: MouseRegion(
        cursor: widget.enabled
            ? SystemMouseCursors.click
            : SystemMouseCursors.basic,
        child: GestureDetector(
          onTap: _overlay == null ? _open : () => setState(_close),
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            decoration: BoxDecoration(
              color: widget.enabled ? t.surface : t.surfaceSunken,
              borderRadius: BorderRadius.circular(Radii.lg),
              border: Border.all(
                color: errorState
                    ? t.danger
                    : (_overlay != null ? t.primary : t.border),
              ),
            ),
            child: Row(
              children: <Widget>[
                Expanded(
                  child: Text(
                    _format(hour, minute),
                    style: TextStyle(
                      fontSize: 13,
                      height: 1.35,
                      fontFeatures: const <FontFeature>[
                        FontFeature.tabularFigures(),
                      ],
                      color: widget.enabled ? t.content : t.contentMuted,
                    ),
                  ),
                ),
                const SizedBox(width: 6),
                Icon(LucideIcons.clock, size: 14, color: t.contentMuted),
              ],
            ),
          ),
        ),
      ),
    );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        if (widget.label != null) ...<Widget>[
          FieldLabel(label: widget.label!, required: widget.required),
          const SizedBox(height: 6),
        ],
        field,
        if (hasError) ...<Widget>[
          const SizedBox(height: 6),
          Semantics(
            liveRegion: true,
            child: Text(
              widget.error!,
              style: TextStyle(fontSize: 12, color: t.danger),
            ),
          ),
        ] else if (widget.hint != null) ...<Widget>[
          const SizedBox(height: 6),
          Text(
            widget.hint!,
            style: TextStyle(fontSize: 12, color: t.contentMuted),
          ),
        ],
      ],
    );
  }
}

/// One hour or minute. Its own widget so the hover state is local - a stateless
/// cell would have to lift `hovered` into the panel and rebuild all 84 of them.
class _Cell extends StatefulWidget {
  const _Cell({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  State<_Cell> createState() => _CellState();
}

class _CellState extends State<_Cell> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      child: GestureDetector(
        onTap: widget.onTap,
        child: Container(
          margin: const EdgeInsets.symmetric(vertical: 1),
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: widget.selected
                ? t.primary
                : (_hovered ? t.surfaceSunken : null),
            borderRadius: BorderRadius.circular(Radii.lg),
          ),
          child: Text(
            widget.label,
            style: TextStyle(
              fontSize: 13,
              fontWeight: widget.selected ? FontWeight.w500 : FontWeight.w400,
              fontFeatures: const <FontFeature>[FontFeature.tabularFigures()],
              color: widget.selected ? t.primaryContent : t.contentSecondary,
            ),
          ),
        ),
      ),
    );
  }
}
