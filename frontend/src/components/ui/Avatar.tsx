import { useState } from 'react';

import { cn } from '@/lib/cn';

const SIZES = {
  xs: 'h-6 w-6 text-[10px]',
  sm: 'h-7 w-7 text-[11px]',
  md: 'h-8 w-8 text-xs',
  lg: 'h-10 w-10 text-sm',
  xl: 'h-14 w-14 text-base',
} as const;

export function Avatar({
  src,
  name,
  initials,
  size = 'md',
  className,
}: {
  src?: string | null;
  name: string;
  /** Server-computed initials; derived from `name` when absent. */
  initials?: string;
  size?: keyof typeof SIZES;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);

  const fallback =
    initials ??
    name
      .trim()
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0])
      .join('')
      .toUpperCase();

  // A broken image URL would otherwise render the browser's placeholder icon,
  // which looks like a rendering failure. Fall back to initials instead.
  const showImage = src && !failed;

  return (
    <span
      className={cn(
        'relative inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full',
        'bg-primary/12 text-primary font-semibold select-none',
        SIZES[size],
        className,
      )}
    >
      {showImage ? (
        <img
          src={src}
          alt={name}
          loading="lazy"
          onError={() => setFailed(true)}
          className="h-full w-full object-cover"
        />
      ) : (
        // The initials are decorative - the accessible name comes from the
        // surrounding control, so announcing "PS" as well is just noise.
        <span aria-hidden>{fallback}</span>
      )}
    </span>
  );
}
