import 'package:flutter/material.dart';

import '../theme/oklch.dart';
import '../theme/tokens.dart';

/// Badge tones, from `components/ui/Badge.tsx`.
///
/// Each pairs a saturated foreground with a tinted background and a faint border, so
/// the badge reads as a chip in both themes rather than as a block of colour in one
/// and a smudge in the other.
enum BadgeTone { neutral, primary, success, warning, danger, info }

class AppBadge extends StatelessWidget {
  const AppBadge(
    this.label, {
    super.key,
    this.tone = BadgeTone.neutral,
    this.dot = false,
    this.tooltip,
    this.icon,
  });

  final String label;
  final BadgeTone tone;

  /// A leading dot in the current colour. Used where the badge reports a live state
  /// - Active, Balanced, Enabled - rather than a category.
  final bool dot;

  final String? tooltip;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final (Color foreground, Color background, Color border) = switch (tone) {
      BadgeTone.neutral => (t.contentSecondary, t.surfaceSunken, t.border),
      BadgeTone.primary => (t.primary, t.primary.at(0.10), t.primary.at(0.20)),
      BadgeTone.success => (t.success, t.successBg, t.success.at(0.20)),
      BadgeTone.warning => (t.warning, t.warningBg, t.warning.at(0.20)),
      BadgeTone.danger => (t.danger, t.dangerBg, t.danger.at(0.20)),
      BadgeTone.info => (t.info, t.infoBg, t.info.at(0.20)),
    };

    Widget badge = Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(Radii.full),
        border: Border.all(color: border),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        spacing: 6,
        children: <Widget>[
          if (dot)
            Container(
              width: 6,
              height: 6,
              decoration: BoxDecoration(
                color: foreground,
                shape: BoxShape.circle,
              ),
            ),
          if (icon != null) Icon(icon, size: 12, color: foreground),
          Text(
            label,
            style: TextStyle(
              fontSize: 11,
              fontWeight: FontWeight.w500,
              color: foreground,
              height: 1.35,
            ),
          ),
        ],
      ),
    );

    if (tooltip != null) badge = Tooltip(message: tooltip!, child: badge);
    return badge;
  }
}
