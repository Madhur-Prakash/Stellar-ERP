/**
 * A small ⓘ button that explains the thing next to it.
 *
 * Accounting software is full of terms that are precise and unfamiliar - "receivables",
 * "reversal", "control account" - and the usual answers are both bad: a manual nobody
 * reads, or a `title` attribute that never appears on a touch device and cannot hold a
 * sentence worth reading. So the explanation lives next to the number it explains.
 *
 * Implementation notes that matter for it being usable rather than decorative:
 *
 * - **`aria-expanded` and a real button**, so a screen reader announces it as
 *   expandable rather than as an unlabelled glyph.
 * - **Escape closes it, and so does a click anywhere outside.** A popover that can only
 *   be dismissed by hitting the same 14-pixel target again is a trap on a phone.
 * - **Positioned by an `align` prop rather than measured.** A tile at the right edge of
 *   the grid needs its panel to open leftwards or it is clipped by the viewport; a
 *   measuring library is not worth the weight for that.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';

import { cn } from '@/lib/cn';

export function InfoTip({
  label,
  children,
  align = 'left',
  className,
}: {
  /** What this explains, for the accessible name: "About revenue". */
  label: string;
  children: ReactNode;
  align?: 'left' | 'right';
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const wrapper = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;

    const onPointerDown = (event: PointerEvent) => {
      if (!wrapper.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };

    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  return (
    <span ref={wrapper} className={cn('relative inline-flex', className)}>
      <button
        type="button"
        aria-expanded={open}
        aria-label={`About ${label}`}
        onClick={() => setOpen((value) => !value)}
        className={cn(
          'text-content-muted hover:text-content hover:border-border-strong',
          'border-border flex h-4 w-4 shrink-0 items-center justify-center rounded-full border',
          'text-[10px] leading-none font-semibold transition-colors',
          'focus-visible:ring-primary/30 focus-visible:ring-2 focus-visible:outline-none',
          open && 'border-primary text-primary',
        )}
      >
        i
      </button>

      {open && (
        <span
          role="note"
          className={cn(
            'bg-surface-raised border-border text-content-secondary absolute top-6 z-50',
            'w-64 rounded-lg border p-3 text-left text-[12px] leading-relaxed shadow-lg',
            // Normal flow inside, so paragraphs and lists render as written.
            '[&_p]:mb-1.5 [&_p:last-child]:mb-0 [&_strong]:text-content [&_strong]:font-semibold',
            align === 'left' ? 'left-0' : 'right-0',
          )}
        >
          {children}
        </span>
      )}
    </span>
  );
}
