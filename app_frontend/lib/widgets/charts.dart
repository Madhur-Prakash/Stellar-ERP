import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';

import '../core/format.dart';
import '../theme/app_theme.dart';
import '../theme/tokens.dart';

/// The charts.
///
/// **`double` appears here and almost nowhere else in the app, and every datum carries
/// its original decimal string alongside the number that was plotted.** A pixel position
/// does not need exact decimal arithmetic; a figure someone reads does. So the geometry
/// is approximate and every number on screen is exact - the tooltips format from
/// [ChartPoint.exact], never from the plotted value.
///
/// **Bars, not pie charts, for balances.** A cash account can hold a negative balance -
/// which is itself a signal worth seeing - and a negative value has no possible pie
/// slice. A pie would have to drop it, hide it, or plot its absolute value, and all three
/// are lies about the books. The donut is used only for expense composition, where the
/// values are non-negative by construction.
///
/// **Horizontal gridlines only.** Vertical grid lines add clutter without helping anyone
/// read a value off a time axis.

/// A plotted value and the exact figure behind it.
class ChartPoint {
  const ChartPoint({
    required this.label,
    required this.value,
    required this.exact,
  });

  final String label;

  /// For geometry only.
  final double value;

  /// The decimal string the API sent. What the tooltip prints.
  final String exact;
}

/// One series of a multi-series chart.
class ChartSeries {
  const ChartSeries({
    required this.name,
    required this.colour,
    required this.points,
  });

  final String name;
  final Color colour;
  final List<ChartPoint> points;
}

/// Animation shared by every chart here, so they feel like one system.
const Duration chartAnimation = Duration(milliseconds: 600);
const Curve chartCurve = Curves.easeOut;

// =============================================================================
// Shared decoration
// =============================================================================
FlGridData _grid(AppTokens t, {bool horizontal = true}) => FlGridData(
  show: true,
  drawVerticalLine: !horizontal,
  drawHorizontalLine: horizontal,
  getDrawingHorizontalLine: (_) => FlLine(color: t.border, strokeWidth: 1),
  getDrawingVerticalLine: (_) => FlLine(color: t.border, strokeWidth: 1),
);

/// An axis tick label, at the 11px the web app uses.
Widget _tick(String text, AppTokens t, {TextAlign align = TextAlign.center}) =>
    Text(
      text,
      textAlign: align,
      style: TextStyle(fontSize: 11, color: t.contentMuted),
    );

/// The vertical money axis, abbreviated - 1.2K, 3.4M, 1.1Cr.
AxisTitles _moneyAxis(AppTokens t) => AxisTitles(
  sideTitles: SideTitles(
    showTitles: true,
    reservedSize: 48,
    getTitlesWidget: (double value, TitleMeta meta) => Padding(
      padding: const EdgeInsets.only(right: 6),
      child: _tick(formatCompact(value), t, align: TextAlign.right),
    ),
  ),
);

const AxisTitles _hiddenAxis = AxisTitles(
  sideTitles: SideTitles(showTitles: false),
);

/// The category axis along the bottom, thinned so labels never collide.
///
/// Twelve months on a narrow panel would overlap, and a chart with unreadable labels is
/// worse than one with fewer of them - so every other label is dropped once the series
/// grows past what fits.
AxisTitles _categoryAxis(
  AppTokens t,
  List<String> labels, {
  double reserved = 28,
  double angle = 0,
}) {
  final int step = labels.length > 8 ? (labels.length / 7).ceil() : 1;
  return AxisTitles(
    sideTitles: SideTitles(
      showTitles: true,
      reservedSize: reserved,
      interval: 1,
      getTitlesWidget: (double value, TitleMeta meta) {
        final int index = value.round();
        if (index < 0 || index >= labels.length) return const SizedBox.shrink();
        if (index % step != 0 && index != labels.length - 1) {
          return const SizedBox.shrink();
        }
        final Widget label = Padding(
          padding: const EdgeInsets.only(top: 6),
          child: _tick(labels[index], t),
        );
        return angle == 0
            ? label
            : Transform.rotate(
                angle: angle,
                alignment: Alignment.topCenter,
                child: label,
              );
      },
    ),
  );
}

/// The tooltip container, matching the CSS `TOOLTIP_STYLE`.
///
/// fl_chart's tooltip is a painted box rather than a widget, so the shadow the web app
/// uses cannot be reproduced exactly - the border and radius can, and they are what carry
/// the shape.
BarTouchTooltipData _barTooltip(AppTokens t, GetBarTooltipItem builder) =>
    BarTouchTooltipData(
      getTooltipColor: (_) => t.surfaceRaised,
      tooltipBorder: BorderSide(color: t.border),
      tooltipBorderRadius: BorderRadius.circular(Radii.lg),
      tooltipPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      getTooltipItem: builder,
    );

TextStyle _tooltipTitle(AppTokens t) =>
    TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: t.content);

TextStyle _tooltipBody(AppTokens t, {Color? colour}) => TextStyle(
  fontSize: 12,
  color: colour ?? t.contentSecondary,
  fontFeatures: tabularFigures,
);

// =============================================================================
// Area chart - the dashboard's revenue and expenses
// =============================================================================
/// Two filled areas over a shared time axis.
///
/// Used on the dashboard for revenue against expenses. Areas rather than bars because the
/// reading is a shape over time, and the fill makes the gap between the two series - which
/// is the profit - legible without a third series.
class MoneyAreaChart extends StatelessWidget {
  const MoneyAreaChart({
    super.key,
    required this.series,
    required this.currency,
    this.height = 280,
  });

  final List<ChartSeries> series;
  final String currency;
  final double height;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final List<String> labels = series.isEmpty
        ? const <String>[]
        : series.first.points.map((ChartPoint p) => p.label).toList();

    return SizedBox(
      height: height,
      child: LineChart(
        duration: chartAnimation,
        curve: chartCurve,
        LineChartData(
          gridData: _grid(t),
          borderData: FlBorderData(show: false),
          titlesData: FlTitlesData(
            topTitles: _hiddenAxis,
            rightTitles: _hiddenAxis,
            leftTitles: _moneyAxis(t),
            bottomTitles: _categoryAxis(t, labels),
          ),
          lineTouchData: LineTouchData(
            touchTooltipData: LineTouchTooltipData(
              getTooltipColor: (_) => t.surfaceRaised,
              tooltipBorder: BorderSide(color: t.border),
              tooltipBorderRadius: BorderRadius.circular(Radii.lg),
              tooltipPadding: const EdgeInsets.symmetric(
                horizontal: 10,
                vertical: 8,
              ),
              getTooltipItems: (List<LineBarSpot> spots) => spots.map((
                LineBarSpot spot,
              ) {
                final ChartSeries line = series[spot.barIndex];
                // The exact decimal off the datum, not the float that was plotted.
                final String exact = line.points[spot.spotIndex].exact;
                return LineTooltipItem(
                  '${line.name}  ${formatMoney(exact, currency: currency)}',
                  _tooltipBody(t, colour: line.colour),
                );
              }).toList(),
            ),
          ),
          lineBarsData: <LineChartBarData>[
            for (final ChartSeries line in series)
              LineChartBarData(
                isCurved: true,
                curveSmoothness: 0.25,
                preventCurveOverShooting: true,
                color: line.colour,
                barWidth: 2,
                dotData: const FlDotData(show: false),
                belowBarData: BarAreaData(
                  show: true,
                  gradient: LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: <Color>[
                      line.colour.withValues(alpha: 0.28),
                      line.colour.withValues(alpha: 0),
                    ],
                  ),
                ),
                spots: <FlSpot>[
                  for (int index = 0; index < line.points.length; index++)
                    FlSpot(index.toDouble(), line.points[index].value),
                ],
              ),
          ],
        ),
      ),
    );
  }
}

// =============================================================================
// Grouped bars - analytics by month, cash movement by day
// =============================================================================
class GroupedBarChart extends StatelessWidget {
  const GroupedBarChart({
    super.key,
    required this.series,
    required this.currency,
    this.height = 300,
    this.tooltipBuilder,
  });

  final List<ChartSeries> series;
  final String currency;
  final double height;

  /// Overrides the default one-line tooltip. The cash-movement chart uses it to list the
  /// individual entries behind a day's bar - a total with no way to see what it is
  /// composed of invites the question it cannot answer.
  final String Function(int groupIndex)? tooltipBuilder;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final List<String> labels = series.isEmpty
        ? const <String>[]
        : series.first.points.map((ChartPoint p) => p.label).toList();
    final int groups = labels.length;

    return SizedBox(
      height: height,
      child: BarChart(
        duration: chartAnimation,
        curve: chartCurve,
        BarChartData(
          gridData: _grid(t),
          borderData: FlBorderData(show: false),
          alignment: BarChartAlignment.spaceAround,
          titlesData: FlTitlesData(
            topTitles: _hiddenAxis,
            rightTitles: _hiddenAxis,
            leftTitles: _moneyAxis(t),
            bottomTitles: _categoryAxis(t, labels),
          ),
          barTouchData: BarTouchData(
            touchTooltipData: _barTooltip(t, (
              BarChartGroupData group,
              int groupIndex,
              BarChartRodData rod,
              int rodIndex,
            ) {
              if (tooltipBuilder != null) {
                return BarTooltipItem(
                  tooltipBuilder!(groupIndex),
                  _tooltipBody(t),
                );
              }
              final ChartSeries line = series[rodIndex];
              final String exact = line.points[groupIndex].exact;
              return BarTooltipItem(
                '${line.name}\n',
                _tooltipTitle(t),
                children: <TextSpan>[
                  TextSpan(
                    text: formatMoney(exact, currency: currency),
                    style: _tooltipBody(t, colour: line.colour),
                  ),
                ],
              );
            }),
          ),
          barGroups: <BarChartGroupData>[
            for (int group = 0; group < groups; group++)
              BarChartGroupData(
                x: group,
                barsSpace: 3,
                barRods: <BarChartRodData>[
                  for (final ChartSeries line in series)
                    BarChartRodData(
                      toY: line.points[group].value,
                      color: line.colour,
                      width: groups > 12 ? 8 : 14,
                      borderRadius: const BorderRadius.vertical(
                        top: Radius.circular(3),
                      ),
                    ),
                ],
              ),
          ],
        ),
      ),
    );
  }
}

// =============================================================================
// Horizontal bars - where your money is
// =============================================================================
/// One bar per account, longest first, with the colour carrying the account type.
///
/// Horizontal because the labels are account names: rotated vertical labels are
/// genuinely harder to read, and the chart grows down the page rather than squeezing
/// sideways as accounts are added.
class HorizontalBarChart extends StatelessWidget {
  const HorizontalBarChart({
    super.key,
    required this.points,
    required this.colours,
    required this.currency,
    this.subtitles = const <String>[],
  });

  final List<ChartPoint> points;

  /// One colour per point, so a negative balance can be flagged individually.
  final List<Color> colours;

  final String currency;

  /// Shown in the tooltip under the amount - the account type, for these.
  final List<String> subtitles;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;

    return SizedBox(
      height: (points.length * 42).clamp(180, 900).toDouble(),
      child: BarChart(
        duration: chartAnimation,
        curve: chartCurve,
        BarChartData(
          gridData: _grid(t, horizontal: false),
          borderData: FlBorderData(show: false),
          alignment: BarChartAlignment.spaceAround,
          titlesData: FlTitlesData(
            topTitles: _hiddenAxis,
            rightTitles: _hiddenAxis,
            leftTitles: AxisTitles(
              sideTitles: SideTitles(
                showTitles: true,
                reservedSize: 168,
                interval: 1,
                getTitlesWidget: (double value, TitleMeta meta) {
                  final int index = value.round();
                  if (index < 0 || index >= points.length) {
                    return const SizedBox.shrink();
                  }
                  return Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: Text(
                      points[index].label,
                      textAlign: TextAlign.right,
                      overflow: TextOverflow.ellipsis,
                      maxLines: 2,
                      style: TextStyle(fontSize: 11, color: t.contentMuted),
                    ),
                  );
                },
              ),
            ),
            bottomTitles: AxisTitles(
              sideTitles: SideTitles(
                showTitles: true,
                reservedSize: 26,
                getTitlesWidget: (double value, TitleMeta meta) => Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: _tick(formatCompact(value), t),
                ),
              ),
            ),
          ),
          barTouchData: BarTouchData(
            touchTooltipData: _barTooltip(t, (
              BarChartGroupData group,
              int groupIndex,
              BarChartRodData rod,
              int rodIndex,
            ) {
              // The account code is deliberately dropped: it is internal numbering, and
              // nobody reading "where is my money" needs to know Sales Revenue is 4100.
              // The type is kept, because it is what the bar's colour means.
              final String subtitle = groupIndex < subtitles.length
                  ? subtitles[groupIndex]
                  : '';
              return BarTooltipItem(
                '${formatMoney(points[groupIndex].exact, currency: currency)}\n',
                _tooltipTitle(t),
                children: <TextSpan>[
                  TextSpan(text: subtitle, style: _tooltipBody(t)),
                ],
              );
            }),
          ),
          barGroups: <BarChartGroupData>[
            for (int index = 0; index < points.length; index++)
              BarChartGroupData(
                x: index,
                barRods: <BarChartRodData>[
                  BarChartRodData(
                    toY: points[index].value,
                    color: colours[index],
                    width: 20,
                    borderRadius: const BorderRadius.horizontal(
                      right: Radius.circular(4),
                    ),
                  ),
                ],
              ),
          ],
        ),
      ),
    );
  }
}

// =============================================================================
// Donut - what you spent it on
// =============================================================================
/// A donut, not a full pie: the hole gives the arcs room and they are easier to compare
/// than wedges converging on a point.
///
/// Safe here and deliberately not used for balances - expense totals are non-negative by
/// construction, so every value has a real slice.
class DonutChart extends StatefulWidget {
  const DonutChart({
    super.key,
    required this.points,
    required this.colours,
    required this.currency,
    required this.total,
  });

  final List<ChartPoint> points;
  final List<Color> colours;
  final String currency;

  /// The exact sum, for the share percentages.
  final String total;

  @override
  State<DonutChart> createState() => _DonutChartState();
}

class _DonutChartState extends State<DonutChart> {
  int _touched = -1;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final double total = chartValue(widget.total);

    return Column(
      children: <Widget>[
        SizedBox(
          height: 220,
          child: PieChart(
            duration: chartAnimation,
            curve: chartCurve,
            PieChartData(
              sectionsSpace: 2,
              centerSpaceRadius: 56,
              pieTouchData: PieTouchData(
                touchCallback:
                    (FlTouchEvent event, PieTouchResponse? response) {
                      setState(() {
                        _touched =
                            response?.touchedSection?.touchedSectionIndex ?? -1;
                      });
                    },
              ),
              sections: <PieChartSectionData>[
                for (int index = 0; index < widget.points.length; index++)
                  PieChartSectionData(
                    value: widget.points[index].value,
                    color: widget.colours[index % widget.colours.length],
                    radius: _touched == index ? 40 : 34,
                    showTitle: false,
                  ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 12),
        // The legend doubles as the value readout, which is how a slice's amount and its
        // share are read on a desktop without hovering every arc in turn.
        Wrap(
          spacing: 16,
          runSpacing: 6,
          children: <Widget>[
            for (int index = 0; index < widget.points.length; index++)
              _LegendEntry(
                colour: widget.colours[index % widget.colours.length],
                label: widget.points[index].label,
                detail: total > 0
                    ? '${formatMoney(widget.points[index].exact, currency: widget.currency)}'
                          ' · ${(widget.points[index].value / total * 100).toStringAsFixed(1)}%'
                    : formatMoney(
                        widget.points[index].exact,
                        currency: widget.currency,
                      ),
                emphasised: _touched == index,
                tokens: t,
              ),
          ],
        ),
      ],
    );
  }
}

class _LegendEntry extends StatelessWidget {
  const _LegendEntry({
    required this.colour,
    required this.label,
    required this.detail,
    required this.emphasised,
    required this.tokens,
  });

  final Color colour;
  final String label;
  final String detail;
  final bool emphasised;
  final AppTokens tokens;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      spacing: 6,
      children: <Widget>[
        Container(
          width: 8,
          height: 8,
          decoration: BoxDecoration(color: colour, shape: BoxShape.circle),
        ),
        Text(
          label,
          style: TextStyle(
            fontSize: 11,
            color: emphasised ? tokens.content : tokens.contentSecondary,
            fontWeight: emphasised ? FontWeight.w600 : FontWeight.w400,
          ),
        ),
        Text(
          detail,
          style: TextStyle(
            fontSize: 11,
            color: tokens.contentMuted,
            fontFeatures: tabularFigures,
          ),
        ),
      ],
    );
  }
}

/// A legend row for the multi-series charts.
class ChartLegend extends StatelessWidget {
  const ChartLegend({super.key, required this.series});

  final List<ChartSeries> series;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    return Padding(
      padding: const EdgeInsets.only(top: 12),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.center,
        spacing: 20,
        children: <Widget>[
          for (final ChartSeries line in series)
            Row(
              mainAxisSize: MainAxisSize.min,
              spacing: 6,
              children: <Widget>[
                Container(
                  width: 8,
                  height: 8,
                  decoration: BoxDecoration(
                    color: line.colour,
                    shape: BoxShape.circle,
                  ),
                ),
                Text(
                  line.name,
                  style: TextStyle(fontSize: 11, color: t.contentSecondary),
                ),
              ],
            ),
        ],
      ),
    );
  }
}

// =============================================================================
// Waterfall - how income became profit
// =============================================================================
/// One step of a waterfall.
class WaterfallStep {
  const WaterfallStep({
    required this.label,
    required this.from,
    required this.to,
    required this.exact,
    required this.runningTotal,
    required this.kind,
  });

  final String label;

  /// Where the floating bar starts and ends. A step that takes the running total down
  /// has `from` above `to`.
  final double from;
  final double to;

  final String exact;
  final String runningTotal;
  final WaterfallKind kind;
}

enum WaterfallKind { up, down, total }

/// A waterfall: income, then each cost stepping down, ending on net profit.
///
/// fl_chart's rods accept a `fromY`, so this needs no invisible lifter bar - the web
/// version has to stack a transparent rod under each visible one because Recharts has no
/// floating bar. Same picture, one fewer trick.
class WaterfallChart extends StatelessWidget {
  const WaterfallChart({
    super.key,
    required this.steps,
    required this.currency,
    this.height = 320,
  });

  final List<WaterfallStep> steps;
  final String currency;
  final double height;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;

    Color colour(WaterfallStep step) => switch (step.kind) {
      WaterfallKind.up => t.success,
      WaterfallKind.down => t.danger,
      // The total is coloured by its own sign: a loss should not look like a win.
      WaterfallKind.total => isNegativeMoney(step.exact) ? t.danger : t.primary,
    };

    return SizedBox(
      height: height,
      child: BarChart(
        duration: chartAnimation,
        curve: chartCurve,
        BarChartData(
          gridData: _grid(t),
          borderData: FlBorderData(show: false),
          alignment: BarChartAlignment.spaceAround,
          // Zero is the reference a waterfall is read against, so it is drawn.
          extraLinesData: ExtraLinesData(
            horizontalLines: <HorizontalLine>[
              HorizontalLine(y: 0, color: t.contentMuted, strokeWidth: 1),
            ],
          ),
          titlesData: FlTitlesData(
            topTitles: _hiddenAxis,
            rightTitles: _hiddenAxis,
            leftTitles: _moneyAxis(t),
            bottomTitles: _categoryAxis(
              t,
              steps.map((WaterfallStep step) => step.label).toList(),
              reserved: 64,
              angle: -0.52,
            ),
          ),
          barTouchData: BarTouchData(
            touchTooltipData: _barTooltip(t, (
              BarChartGroupData group,
              int groupIndex,
              BarChartRodData rod,
              int rodIndex,
            ) {
              final WaterfallStep step = steps[groupIndex];
              final String prefix = switch (step.kind) {
                WaterfallKind.down => '−',
                WaterfallKind.up => '+',
                WaterfallKind.total => '',
              };
              final String suffix = step.kind == WaterfallKind.total
                  ? ''
                  : '\nrunning total '
                        '${formatMoney(step.runningTotal, currency: currency)}';
              return BarTooltipItem(
                '${step.label}\n',
                _tooltipTitle(t),
                children: <TextSpan>[
                  TextSpan(
                    text:
                        '$prefix${formatMoney(step.exact, currency: currency)}$suffix',
                    style: _tooltipBody(t, colour: colour(step)),
                  ),
                ],
              );
            }),
          ),
          barGroups: <BarChartGroupData>[
            for (int index = 0; index < steps.length; index++)
              BarChartGroupData(
                x: index,
                barRods: <BarChartRodData>[
                  BarChartRodData(
                    fromY: steps[index].from,
                    toY: steps[index].to,
                    color: colour(steps[index]),
                    width: 30,
                    borderRadius: BorderRadius.circular(3),
                  ),
                ],
              ),
          ],
        ),
      ),
    );
  }
}

// =============================================================================
// Composed trend - income and spending as lines, profit as an area
// =============================================================================
/// Lines for income and spending because this is a series over time and direction is the
/// primary reading. Profit is filled instead, so a loss shows as area below the axis - a
/// shaded region reads faster than a line dipping under a gridline.
class ComposedTrendChart extends StatelessWidget {
  const ComposedTrendChart({
    super.key,
    required this.income,
    required this.expenses,
    required this.profit,
    required this.currency,
    this.height = 280,
  });

  final ChartSeries income;
  final ChartSeries expenses;
  final ChartSeries profit;
  final String currency;
  final double height;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final List<String> labels = income.points
        .map((ChartPoint point) => point.label)
        .toList();
    final List<ChartSeries> order = <ChartSeries>[profit, income, expenses];

    return Column(
      children: <Widget>[
        SizedBox(
          height: height,
          child: LineChart(
            duration: chartAnimation,
            curve: chartCurve,
            LineChartData(
              gridData: _grid(t),
              borderData: FlBorderData(show: false),
              titlesData: FlTitlesData(
                topTitles: _hiddenAxis,
                rightTitles: _hiddenAxis,
                leftTitles: _moneyAxis(t),
                bottomTitles: _categoryAxis(t, labels),
              ),
              lineTouchData: LineTouchData(
                touchTooltipData: LineTouchTooltipData(
                  getTooltipColor: (_) => t.surfaceRaised,
                  tooltipBorder: BorderSide(color: t.border),
                  tooltipBorderRadius: BorderRadius.circular(Radii.lg),
                  tooltipPadding: const EdgeInsets.symmetric(
                    horizontal: 10,
                    vertical: 8,
                  ),
                  getTooltipItems: (List<LineBarSpot> spots) => spots.map((
                    LineBarSpot spot,
                  ) {
                    final ChartSeries line = order[spot.barIndex];
                    return LineTooltipItem(
                      '${line.name}  '
                      '${formatMoney(line.points[spot.spotIndex].exact, currency: currency)}',
                      _tooltipBody(t, colour: line.colour),
                    );
                  }).toList(),
                ),
              ),
              lineBarsData: <LineChartBarData>[
                // Profit first, so its fill sits behind the two lines.
                LineChartBarData(
                  isCurved: true,
                  curveSmoothness: 0.25,
                  preventCurveOverShooting: true,
                  color: profit.colour,
                  barWidth: 2,
                  dotData: const FlDotData(show: false),
                  belowBarData: BarAreaData(
                    show: true,
                    gradient: LinearGradient(
                      begin: Alignment.topCenter,
                      end: Alignment.bottomCenter,
                      colors: <Color>[
                        profit.colour.withValues(alpha: 0.25),
                        profit.colour.withValues(alpha: 0),
                      ],
                    ),
                  ),
                  spots: <FlSpot>[
                    for (int index = 0; index < profit.points.length; index++)
                      FlSpot(index.toDouble(), profit.points[index].value),
                  ],
                ),
                for (final ChartSeries line in <ChartSeries>[income, expenses])
                  LineChartBarData(
                    isCurved: true,
                    curveSmoothness: 0.25,
                    preventCurveOverShooting: true,
                    color: line.colour,
                    barWidth: 2,
                    dotData: FlDotData(
                      show: true,
                      getDotPainter: (_, _, _, _) => FlDotCirclePainter(
                        radius: 3,
                        color: line.colour,
                        strokeWidth: 0,
                      ),
                    ),
                    spots: <FlSpot>[
                      for (int index = 0; index < line.points.length; index++)
                        FlSpot(index.toDouble(), line.points[index].value),
                    ],
                  ),
              ],
            ),
          ),
        ),
        ChartLegend(series: <ChartSeries>[income, expenses, profit]),
      ],
    );
  }
}
