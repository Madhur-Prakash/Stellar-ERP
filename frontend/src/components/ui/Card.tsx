import type { HTMLAttributes, ReactNode } from 'react';

import { cn } from '@/lib/cn';

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('bg-surface border-border rounded-xl border shadow-xs', className)}
      {...props}
    />
  );
}

/**
 * `title` is omitted from the passthrough props on purpose: the DOM's own
 * `title` attribute is typed `string`, and intersecting it with `ReactNode`
 * yields `string & ReactNode`, which rejects any element passed as a heading.
 */
export function CardHeader({
  className,
  title,
  description,
  action,
  ...props
}: Omit<HTMLAttributes<HTMLDivElement>, 'title'> & {
  title?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div
      className={cn('flex items-start justify-between gap-4 px-5 pt-5 pb-4', className)}
      {...props}
    >
      <div className="min-w-0">
        {title && <h3 className="text-content truncate text-[15px] font-semibold">{title}</h3>}
        {description && <p className="text-content-muted mt-0.5 text-[13px]">{description}</p>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

export function CardBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('px-5 pb-5', className)} {...props} />;
}

export function CardFooter({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'border-border bg-surface-sunken/50 flex items-center gap-3 rounded-b-xl border-t px-5 py-3.5',
        className,
      )}
      {...props}
    />
  );
}
