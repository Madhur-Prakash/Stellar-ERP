/**
 * A hover explanation for a control that may be disabled.
 *
 * **A disabled `<button>` never shows its own `title`.** Browsers suppress pointer events on
 * disabled form controls, and `Button` makes that explicit with `disabled:pointer-events-none`
 * - so a greyed-out control can carry a perfectly good `title` that can never fire, and it
 * reads as "greyed out for no stated reason". That is the exact case this exists for: the
 * *disabled* state is when the explanation matters most and when the native tooltip is
 * guaranteed not to appear.
 *
 * The same `pointer-events-none` is what makes this work: the hover passes straight through
 * the button to this wrapper, which is not disabled and does show its tooltip. `inline-flex`
 * so wrapping does not disturb the row's layout.
 *
 * Lives here rather than beside one screen because the pattern is not specific to any of
 * them - anywhere a destructive control is conditionally blocked, the reason has to be
 * reachable by hover.
 */
import { useState, type ReactNode } from 'react';

import { cn } from '@/lib/cn';

export function Hint({
  text,
  children,
  align = 'right',
  width = 'w-80',
}: {
  /** The explanation. Also rendered for screen readers, which never get a hover. */
  text: string;
  children: ReactNode;
  /**
   * Which edge the panel is anchored to. `right` grows the panel *leftwards*, which is
   * what a control sitting hard against the right edge of a row needs - anchored left it
   * runs straight off the window, which is exactly what the browser's native one did.
   */
  align?: 'left' | 'right';
  /** Tailwind width class. Short reasons do not need the full 20rem. */
  width?: string;
}) {
  const [open, setOpen] = useState(false);

  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      {children}
      {/* Always in the DOM for a screen reader. Not a `title` on the wrapper: that would
          fire the browser's own unstyled tooltip *as well*, giving two explanations of the
          same thing a moment apart. */}
      <span className="sr-only">{text}</span>
      {open && (
        <span
          role="tooltip"
          className={cn(
            'bg-surface-raised border-border text-content-secondary absolute top-full z-50 mt-1.5',
            'rounded-lg border p-2.5 text-left text-[12px] leading-relaxed font-normal shadow-lg',
            // Rows are `whitespace-nowrap` in places; this text has to wrap.
            'whitespace-normal',
            align === 'right' ? 'right-0' : 'left-0',
            width,
          )}
        >
          {text}
        </span>
      )}
    </span>
  );
}
