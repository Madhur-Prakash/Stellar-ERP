import 'package:flutter/material.dart';

import '../theme/app_theme.dart';
import '../theme/oklch.dart';
import '../theme/tokens.dart';
import 'app_button.dart';
import 'primitives.dart';

/// One column of an [AppDataTable].
class AppColumn<T> {
  const AppColumn({
    required this.header,
    required this.cell,
    this.numeric = false,
    this.hideOnNarrow = false,
    this.flex,
    this.fixedWidth,
  });

  /// Header label. Empty for an action column.
  final String header;

  /// Cell renderer.
  final Widget Function(T row) cell;

  /// Right-align and use tabular figures. For money and quantities.
  final bool numeric;

  /// Dropped below the `sm` breakpoint - for columns that are nice-to-have.
  final bool hideOnNarrow;

  /// Share of the leftover width. Null makes the column size to its content.
  final int? flex;

  final double? fixedWidth;
}

/// A compact data table for list screens.
///
/// Deliberately simpler than a full grid: these screens need alignment, a loading
/// state, an empty state, and numeric columns that line up. They do not need
/// client-side sorting or column resizing - the server paginates and sorts, because a
/// business with 40,000 invoices cannot ship them all to the client to sort them there.
///
/// **Numeric columns are right-aligned and tabular-figured**, so digits form vertical
/// columns. Money in a proportional font is genuinely harder to scan, and two totals
/// that must agree are compared by eye down a column.
///
/// Built on `Table` rather than a `ListView` of `Row`s because a table is what this is:
/// the cells in a column must share a width decided by all of them, which a per-row
/// layout cannot do. A content column takes the leftover space; a numeric one sizes to
/// its widest figure.
class AppDataTable<T> extends StatelessWidget {
  const AppDataTable({
    super.key,
    required this.columns,
    required this.rows,
    required this.rowKey,
    this.isLoading = false,
    this.onRowTap,
    this.empty,
    this.footer,
  });

  final List<AppColumn<T>> columns;
  final List<T> rows;
  final String Function(T row) rowKey;
  final bool isLoading;
  final void Function(T row)? onRowTap;

  /// What to show when there are no rows. Omit to render an empty table body.
  final EmptyState? empty;

  /// A footer row - totals, usually. Cells must match the visible column count.
  final List<Widget>? footer;

  /// The `sm` breakpoint, below which `hideOnNarrow` columns drop.
  static const double _narrowBreakpoint = 640;

  @override
  Widget build(BuildContext context) {
    if (isLoading) {
      return const Padding(
        padding: EdgeInsets.all(16),
        child: Column(
          spacing: 8,
          children: <Widget>[
            Skeleton(height: 36),
            Skeleton(height: 36),
            Skeleton(height: 36),
            Skeleton(height: 36),
            Skeleton(height: 36),
          ],
        ),
      );
    }

    if (rows.isEmpty && empty != null) return empty!;

    return LayoutBuilder(
      builder: (BuildContext context, BoxConstraints constraints) {
        final bool narrow = constraints.maxWidth < _narrowBreakpoint;
        final List<AppColumn<T>> visible = columns
            .where((AppColumn<T> column) => !(narrow && column.hideOnNarrow))
            .toList(growable: false);

        return _Table<T>(
          columns: visible,
          rows: rows,
          rowKey: rowKey,
          onRowTap: onRowTap,
          footer: footer,
        );
      },
    );
  }
}

class _Table<T> extends StatelessWidget {
  const _Table({
    required this.columns,
    required this.rows,
    required this.rowKey,
    required this.onRowTap,
    required this.footer,
  });

  final List<AppColumn<T>> columns;
  final List<T> rows;
  final String Function(T row) rowKey;
  final void Function(T row)? onRowTap;
  final List<Widget>? footer;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;

    final Map<int, TableColumnWidth> widths = <int, TableColumnWidth>{
      for (int index = 0; index < columns.length; index++)
        index: switch (columns[index]) {
          AppColumn<T>(fixedWidth: final double width?) => FixedColumnWidth(
            width,
          ),
          AppColumn<T>(flex: final int flex?) => FlexColumnWidth(
            flex.toDouble(),
          ),
          AppColumn<T>(numeric: true) => const IntrinsicColumnWidth(),
          // A content column with no flex set takes the leftover space, which is what
          // makes the description column absorb a wide window rather than leaving a
          // gap at the right edge.
          _ => const FlexColumnWidth(),
        },
    };

    return Table(
      columnWidths: widths,
      defaultVerticalAlignment: TableCellVerticalAlignment.middle,
      children: <TableRow>[
        TableRow(
          decoration: BoxDecoration(
            border: Border(bottom: BorderSide(color: t.border)),
          ),
          children: <Widget>[
            for (final AppColumn<T> column in columns)
              Padding(
                padding: const EdgeInsets.symmetric(
                  horizontal: 12,
                  vertical: 8,
                ),
                child: Text(
                  column.header.toUpperCase(),
                  textAlign: column.numeric ? TextAlign.right : TextAlign.left,
                  style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w600,
                    letterSpacing: 0.44,
                    color: t.contentMuted,
                  ),
                ),
              ),
          ],
        ),
        for (int index = 0; index < rows.length; index++)
          TableRow(
            key: ValueKey<String>(rowKey(rows[index])),
            decoration: BoxDecoration(
              border: index == rows.length - 1
                  ? null
                  : Border(bottom: BorderSide(color: t.border.at(0.6))),
            ),
            children: <Widget>[
              for (final AppColumn<T> column in columns)
                _Cell<T>(column: column, row: rows[index], onTap: onRowTap),
            ],
          ),
        if (footer != null)
          TableRow(
            decoration: BoxDecoration(
              border: Border(top: BorderSide(color: t.border, width: 2)),
            ),
            children: <Widget>[
              for (int index = 0; index < columns.length; index++)
                index < footer!.length
                    ? footer![index]
                    : const SizedBox.shrink(),
            ],
          ),
      ],
    );
  }
}

class _Cell<T> extends StatefulWidget {
  const _Cell({required this.column, required this.row, required this.onTap});

  final AppColumn<T> column;
  final T row;
  final void Function(T row)? onTap;

  @override
  State<_Cell<T>> createState() => _CellState<T>();
}

class _CellState<T> extends State<_Cell<T>> {
  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;

    Widget cell = Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      child: DefaultTextStyle.merge(
        style: TextStyle(
          fontSize: 13,
          color: t.content,
          height: 1.4,
          fontFeatures: widget.column.numeric ? tabularFigures : null,
        ),
        textAlign: widget.column.numeric ? TextAlign.right : TextAlign.left,
        child: Align(
          alignment: widget.column.numeric
              ? Alignment.centerRight
              : Alignment.centerLeft,
          child: widget.column.cell(widget.row),
        ),
      ),
    );

    if (widget.onTap != null) {
      cell = MouseRegion(
        cursor: SystemMouseCursors.click,
        child: GestureDetector(
          onTap: () => widget.onTap!(widget.row),
          // `HitTestBehavior.opaque` so the whole cell is clickable, not just the
          // glyphs inside it.
          behavior: HitTestBehavior.opaque,
          child: cell,
        ),
      );
    }

    return cell;
  }
}

/// Server-driven pagination controls.
class Pagination extends StatelessWidget {
  const Pagination({
    super.key,
    required this.page,
    required this.totalPages,
    required this.totalItems,
    required this.onChanged,
  });

  final int page;
  final int totalPages;
  final int totalItems;
  final ValueChanged<int> onChanged;

  @override
  Widget build(BuildContext context) {
    if (totalPages <= 1) return const SizedBox.shrink();

    final AppTokens t = context.tokens;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        border: Border(top: BorderSide(color: t.border)),
      ),
      child: Row(
        children: <Widget>[
          Text(
            'Page $page of $totalPages · $totalItems total',
            style: TextStyle(fontSize: 12, color: t.contentMuted),
          ),
          const Spacer(),
          AppButton(
            onPressed: page <= 1 ? null : () => onChanged(page - 1),
            variant: AppButtonVariant.secondary,
            size: AppButtonSize.sm,
            label: 'Previous',
          ),
          const SizedBox(width: 6),
          AppButton(
            onPressed: page >= totalPages ? null : () => onChanged(page + 1),
            variant: AppButtonVariant.secondary,
            size: AppButtonSize.sm,
            label: 'Next',
          ),
        ],
      ),
    );
  }
}

/// A right-aligned total for a table footer, matching a numeric cell's metrics.
class FooterCell extends StatelessWidget {
  const FooterCell(this.text, {super.key, this.numeric = true});

  final String text;
  final bool numeric;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      child: Text(
        text,
        textAlign: numeric ? TextAlign.right : TextAlign.left,
        style: TextStyle(
          fontSize: 13,
          fontWeight: FontWeight.w600,
          color: t.content,
          fontFeatures: numeric ? tabularFigures : null,
        ),
      ),
    );
  }
}
