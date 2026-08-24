import 'package:flutter/material.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../models/analytics.dart';
import '../core/format.dart';
import '../theme/app_theme.dart';
import '../theme/tokens.dart';
import 'app_card.dart';
import 'info_tip.dart';
import 'primitives.dart';

/// A figure in a card - the KPI tile.
///
/// One widget for what the web app implements four times (`StatCard` on the dashboard,
/// `FigureTile` on analytics, `StatTile` on the reports, `TotalTile` on billing). They
/// differ only in the size of the number and which of the optional parts they use, so the
/// duplication there is accidental rather than meaningful - and it is why the dashboard's
/// tiles and the analytics tiles had drifted to 24px and 22px for no reason anyone
/// intended. Parameterised here, with the sizes preserved so each screen still looks like
/// itself.
class MetricTile extends StatelessWidget {
  const MetricTile({
    super.key,
    required this.label,
    required this.value,
    this.icon,
    this.hint,
    this.hintTone,
    this.valueTone,
    this.valueSize = 24,
    this.info,
    this.delta,
    this.deltaGood = true,
    this.uppercaseLabel = false,
  });

  final String label;

  /// Null renders a skeleton, which is what holds the layout while the figure loads.
  final String? value;

  final IconData? icon;
  final String? hint;

  /// `danger` puts the hint in red - used for "₹12,000 overdue".
  final Color? hintTone;

  final Color? valueTone;
  final double valueSize;

  /// Explains the figure. Worth writing for anything an owner might misread.
  final List<Widget>? info;

  /// A decimal string from the API, or null when there is no basis for a percentage.
  final String? delta;

  /// Whether an increase is good news. Expenses going up is not.
  final bool deltaGood;

  /// The report tiles label in small caps; the dashboard tiles do not.
  final bool uppercaseLabel;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;

    // Safe to convert: this picks an arrow direction and a rounded label, not a figure
    // anyone acts on, and the server already rounded it to one decimal place.
    final double? change = delta == null ? null : double.tryParse(delta!);
    final bool positive = (change ?? 0) >= 0;
    final bool good = positive == deltaGood;

    return AppCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Expanded(
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  spacing: 6,
                  children: <Widget>[
                    Flexible(
                      child: Text(
                        uppercaseLabel ? label.toUpperCase() : label,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          fontSize: uppercaseLabel ? 11 : 12,
                          fontWeight: FontWeight.w600,
                          letterSpacing: uppercaseLabel ? 0.6 : 0,
                          color: t.contentMuted,
                        ),
                      ),
                    ),
                    if (info != null) InfoTip(label: label, children: info!),
                  ],
                ),
              ),
              if (icon != null) Icon(icon, size: 16, color: t.contentMuted),
            ],
          ),
          const SizedBox(height: 8),
          if (value == null)
            const Skeleton(width: 96, height: 28)
          else
            Text(
              value!,
              style: TextStyle(
                fontSize: valueSize,
                height: 1,
                fontWeight: FontWeight.w600,
                letterSpacing: -0.02 * valueSize,
                color: valueTone ?? t.content,
                fontFeatures: tabularFigures,
              ),
            ),
          const SizedBox(height: 8),
          SizedBox(
            height: 18,
            child: Row(
              spacing: 8,
              children: <Widget>[
                if (change != null)
                  Row(
                    mainAxisSize: MainAxisSize.min,
                    spacing: 2,
                    children: <Widget>[
                      Icon(
                        positive
                            ? LucideIcons.arrowUpRight
                            : LucideIcons.arrowDownRight,
                        size: 12,
                        color: good ? t.success : t.danger,
                      ),
                      Text(
                        '${change.abs().toStringAsFixed(1)}%',
                        style: TextStyle(
                          fontSize: 12,
                          fontWeight: FontWeight.w500,
                          color: good ? t.success : t.danger,
                          fontFeatures: tabularFigures,
                        ),
                      ),
                    ],
                  ),
                if (hint != null)
                  Expanded(
                    child: Text(
                      hint!,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 12,
                        fontWeight: hintTone != null
                            ? FontWeight.w500
                            : FontWeight.w400,
                        color: hintTone ?? t.contentMuted,
                      ),
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

/// A figure with its period-on-period change.
///
/// Two presentation rules, both about not overclaiming:
///
/// * **A percentage change with no basis is not shown as a number.** Going from ₹0 to
///   ₹50,000 is not "+100%" - it is undefined, so the tile says "no prior data".
/// * That message is skipped when the current figure is also zero: "no prior data" on an
///   empty set of books is noise, not information.
class MovementTile extends StatelessWidget {
  const MovementTile({
    super.key,
    required this.label,
    required this.movement,
    required this.currency,
    this.icon,
    this.risingIsGood = true,
    this.info,
    this.valueSize = 24,
  });

  final String label;
  final Movement? movement;
  final String currency;
  final IconData? icon;
  final bool risingIsGood;
  final List<Widget>? info;
  final double valueSize;

  @override
  Widget build(BuildContext context) {
    final bool noBasis =
        movement != null &&
        movement!.changePercent == null &&
        !isZeroMoney(movement!.current);

    return MetricTile(
      label: label,
      value: movement == null
          ? null
          : formatMoney(movement!.current, currency: currency),
      icon: icon,
      delta: movement?.changePercent,
      deltaGood: risingIsGood,
      hint: noBasis ? 'no prior data' : null,
      info: info,
      valueSize: valueSize,
    );
  }
}

/// A grid of tiles that reflows with the window.
///
/// The web app writes `sm:grid-cols-2 xl:grid-cols-4`. Reproduced as a breakpoint on the
/// available width rather than a fixed column count, so a desktop window dragged narrow
/// behaves the same way a narrow browser does.
class TileGrid extends StatelessWidget {
  const TileGrid({super.key, required this.children, this.maxColumns = 4});

  final List<Widget> children;
  final int maxColumns;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (BuildContext context, BoxConstraints constraints) {
        final double width = constraints.maxWidth;
        final int columns = width >= 1180
            ? maxColumns
            : width >= 760
            ? (maxColumns >= 2 ? 2 : 1)
            : 1;
        const double gap = 16;
        final double tileWidth = (width - gap * (columns - 1)) / columns;

        return Wrap(
          spacing: gap,
          runSpacing: gap,
          children: <Widget>[
            for (final Widget child in children)
              SizedBox(width: tileWidth, child: child),
          ],
        );
      },
    );
  }
}
