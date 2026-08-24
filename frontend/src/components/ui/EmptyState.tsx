import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';

import { cn } from '@/lib/cn';

/**
 * The empty state for a list or table.
 *
 * A real component, not an afterthought, because "no data" is the *first* thing
 * every user of a new ERP sees. It should explain what belongs here and offer
 * the action that creates it - an empty grid with no explanation reads as a bug.
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn('flex flex-col items-center justify-center px-6 py-14 text-center', className)}
    >
      {Icon && (
        <div
          className="bg-surface-sunken text-content-muted mb-4 flex h-12 w-12 items-center justify-center rounded-xl"
          aria-hidden
        >
          <Icon className="h-5 w-5" />
        </div>
      )}
      <h3 className="text-content text-[15px] font-semibold">{title}</h3>
      {description && (
        <p className="text-content-muted mt-1.5 max-w-sm text-[13px] leading-relaxed">
          {description}
        </p>
      )}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
