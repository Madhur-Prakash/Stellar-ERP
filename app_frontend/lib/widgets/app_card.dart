import 'dart:ui' show PathMetric;

import 'package:flutter/material.dart';

import '../theme/oklch.dart';
import '../theme/tokens.dart';

/// The card, from `components/ui/Card.tsx`.
///
/// `bg-surface border rounded-xl shadow-xs`, with an optional border override so a
/// card can carry a status - the danger-tinted reconciliation warning and the
/// warning-tinted reorder panel both do.
class AppCard extends StatelessWidget {
  const AppCard({
    super.key,
    required this.child,
    this.padding,
    this.borderColour,
    this.background,
    this.dashed = false,
    this.onTap,
  });

  final Widget child;

  /// `Card className="p-4"` at the call site - the tiles use it, the composed
  /// header/body cards do not.
  final EdgeInsetsGeometry? padding;

  final Color? borderColour;
  final Color? background;

  /// The upload drop zone is `border-dashed`.
  final bool dashed;

  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;

    Widget card = Container(
      padding: padding,
      decoration: BoxDecoration(
        color: background ?? t.surface,
        borderRadius: BorderRadius.circular(Radii.xl),
        boxShadow: t.shadowXs,
      ),
      child: child,
    );

    // A dashed border is not a `BoxDecoration` feature, so it is painted instead of
    // faked with an image - a `DottedBorder`-style package would be a dependency for
    // one visual detail.
    card = CustomPaint(
      painter: _CardBorderPainter(
        colour: borderColour ?? t.border,
        radius: Radii.xl,
        dashed: dashed,
      ),
      child: card,
    );

    if (onTap != null) {
      card = Material(
        color: Colors.transparent,
        borderRadius: BorderRadius.circular(Radii.xl),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(Radii.xl),
          child: card,
        ),
      );
    }

    return card;
  }
}

class _CardBorderPainter extends CustomPainter {
  const _CardBorderPainter({
    required this.colour,
    required this.radius,
    required this.dashed,
  });

  final Color colour;
  final double radius;
  final bool dashed;

  @override
  void paint(Canvas canvas, Size size) {
    final Paint paint = Paint()
      ..color = colour
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1;

    // Inset by half the stroke so the line lands inside the box, as a CSS border
    // does, rather than straddling the edge and looking half a pixel thick.
    final RRect box = RRect.fromRectAndRadius(
      Rect.fromLTWH(0.5, 0.5, size.width - 1, size.height - 1),
      Radius.circular(radius),
    );

    if (!dashed) {
      canvas.drawRRect(box, paint);
      return;
    }

    const double dash = 6;
    const double gap = 4;
    final Path path = Path()..addRRect(box);
    for (final PathMetric metric in path.computeMetrics()) {
      double distance = 0;
      while (distance < metric.length) {
        final double end = (distance + dash).clamp(0, metric.length);
        canvas.drawPath(metric.extractPath(distance, end), paint);
        distance = end + gap;
      }
    }
  }

  @override
  bool shouldRepaint(_CardBorderPainter old) =>
      old.colour != colour || old.radius != radius || old.dashed != dashed;
}

/// A card's heading row: a title, an optional description, and an action on the right.
class CardHeader extends StatelessWidget {
  const CardHeader({
    super.key,
    this.title,
    this.titleWidget,
    this.description,
    this.action,
    this.padding = const EdgeInsets.only(
      left: 20,
      right: 20,
      top: 20,
      bottom: 16,
    ),
  });

  final String? title;

  /// For a heading that carries an icon or an [InfoTip] beside its text.
  final Widget? titleWidget;

  final String? description;
  final Widget? action;
  final EdgeInsetsGeometry padding;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;

    return Padding(
      padding: padding,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        spacing: 16,
        children: <Widget>[
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                if (titleWidget != null)
                  titleWidget!
                else if (title != null)
                  Text(
                    title!,
                    style: TextStyle(
                      fontSize: 15,
                      fontWeight: FontWeight.w600,
                      color: t.content,
                      height: 1.35,
                    ),
                  ),
                if (description != null) ...<Widget>[
                  const SizedBox(height: 2),
                  Text(
                    description!,
                    style: TextStyle(
                      fontSize: 13,
                      color: t.contentMuted,
                      height: 1.45,
                    ),
                  ),
                ],
              ],
            ),
          ),
          ?action,
        ],
      ),
    );
  }
}

class CardBody extends StatelessWidget {
  const CardBody({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.only(left: 20, right: 20, bottom: 20),
  });

  final Widget child;
  final EdgeInsetsGeometry padding;

  @override
  Widget build(BuildContext context) => Padding(padding: padding, child: child);
}

class CardFooter extends StatelessWidget {
  const CardFooter({super.key, required this.children});

  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
      decoration: BoxDecoration(
        color: t.surfaceSunken.at(0.5),
        border: Border(top: BorderSide(color: t.border)),
        borderRadius: const BorderRadius.vertical(
          bottom: Radius.circular(Radii.xl),
        ),
      ),
      child: Row(spacing: 12, children: children),
    );
  }
}
