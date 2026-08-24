/**
 * A compact data table for list screens.
 *
 * Deliberately simpler than TanStack Table: these screens need alignment, a
 * loading state, an empty state, and numeric columns that line up. They do not
 * need client-side sorting or column resizing - the server paginates and sorts,
 * because a business with 40,000 invoices cannot ship them all to the browser to
 * sort them there.
 *
 * Numeric columns are right-aligned and tabular-figured, so digits form vertical
 * columns. Money in a proportional font is genuinely harder to scan.
 */
import type { ReactNode } from 'react';

import { EmptyState } from '@/components/ui/EmptyState';
import { Skeleton } from '@/components/ui/Skeleton';
import { cn } from '@/lib/cn';

export interface Column<T> {
  /** Header label. */
  header: string;
  /** Cell renderer. */
  cell: (row: T) => ReactNode;
  /** Right-align and use tabular figures. For money and quantities. */
  numeric?: boolean;
  /** Hide below the `sm` breakpoint - for columns that are nice-to-have. */
  hideOnMobile?: boolean;
  /**
   * Applied to the header *and* every cell in the column.
   *
   * Both, because the only thing a caller wants from a column-level class is to style
   * the column: a left border set on the header alone draws a rule that stops after the
   * first row, which reads as a rendering fault rather than a divider.
   */
  className?: string;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  isLoading?: boolean;
  onRowClick?: (row: T) => void;
  empty?: { title: string; description?: string; action?: ReactNode };
  /** Rendered as a footer row - totals, usually. */
  footer?: ReactNode;
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  isLoading,
  onRowClick,
  empty,
  footer,
}: DataTableProps<T>) {
  if (isLoading) {
    return (
      <div className="space-y-2 p-4">
        {[0, 1, 2, 3, 4].map((index) => (
          <Skeleton key={index} className="h-9 w-full" />
        ))}
      </div>
    );
  }

  if (rows.length === 0 && empty) {
    return <EmptyState title={empty.title} description={empty.description} action={empty.action} />;
  }

  return (
    // The wrapper scrolls, not the page: a wide table must never make the whole
    // document scroll horizontally.
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr className="border-border border-b">
            {columns.map((column) => (
              <th
                key={column.header}
                scope="col"
                className={cn(
                  'text-content-muted px-3 py-2 text-left text-[11px] font-semibold tracking-wide uppercase',
                  column.numeric && 'text-right',
                  column.hideOnMobile && 'hidden sm:table-cell',
                  column.className,
                )}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={rowKey(row)}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={cn(
                'border-border/60 border-b last:border-0',
                onRowClick && 'hover:bg-surface-sunken cursor-pointer',
              )}
            >
              {columns.map((column) => (
                <td
                  key={column.header}
                  className={cn(
                    'text-content px-3 py-2.5 align-middle',
                    column.numeric && 'text-right tabular-nums',
                    column.hideOnMobile && 'hidden sm:table-cell',
                    column.className,
                  )}
                >
                  {column.cell(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
        {footer && (
          <tfoot>
            <tr className="border-border border-t-2 font-semibold">{footer}</tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}

/** Consistent page heading with an optional action slot. */
export function PageHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-content text-[19px] font-semibold tracking-tight">{title}</h1>
        {description && <p className="text-content-muted mt-0.5 text-[13px]">{description}</p>}
      </div>
      {action}
    </div>
  );
}

/** Server-driven pagination controls. */
export function Pagination({
  page,
  totalPages,
  totalItems,
  onChange,
}: {
  page: number;
  totalPages: number;
  totalItems: number;
  onChange: (page: number) => void;
}) {
  if (totalPages <= 1) return null;

  return (
    <div className="border-border flex items-center justify-between border-t px-3 py-2.5">
      <p className="text-content-muted text-[12px]">
        Page {page} of {totalPages} · {totalItems} total
      </p>
      <div className="flex gap-1.5">
        <button
          type="button"
          onClick={() => onChange(page - 1)}
          disabled={page <= 1}
          className="border-border text-content hover:bg-surface-sunken rounded-md border px-2.5 py-1 text-[12px] disabled:opacity-40"
        >
          Previous
        </button>
        <button
          type="button"
          onClick={() => onChange(page + 1)}
          disabled={page >= totalPages}
          className="border-border text-content hover:bg-surface-sunken rounded-md border px-2.5 py-1 text-[12px] disabled:opacity-40"
        >
          Next
        </button>
      </div>
    </div>
  );
}
