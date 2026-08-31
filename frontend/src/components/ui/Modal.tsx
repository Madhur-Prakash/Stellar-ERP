/**
 * A modal dialog, built on the platform's `<dialog>` element.
 *
 * `<dialog showModal()>` rather than a hand-rolled overlay, because the browser
 * gives four things correctly that are routinely got wrong by hand: focus is moved
 * into the dialog and trapped there, the rest of the page becomes inert to both
 * pointer and screen reader, Escape closes it, and the backdrop is a real
 * pseudo-element rather than a sibling div with a z-index guess.
 *
 * The one thing it does not give for free is a backdrop click, because the
 * `::backdrop` is not a hit-testable child - so that is computed from the click
 * coordinates against the dialog's own box.
 */
import { useEffect, useRef, type ReactNode } from 'react';

import { cn } from '@/lib/cn';

export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  className,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;

    if (open && !dialog.open) {
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  return (
    <dialog
      ref={ref}
      // `cancel` fires for Escape. Prevented and routed through `onClose` so the
      // parent's state always agrees with whether the dialog is actually open -
      // letting the browser close it directly leaves `open` true and the dialog
      // impossible to reopen.
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        // A click lands on the dialog element itself when it hits the backdrop,
        // because ::backdrop is not a child. Compare against the box to tell them
        // apart.
        if (event.target !== event.currentTarget) return;
        const box = event.currentTarget.getBoundingClientRect();
        const inside =
          event.clientX >= box.left &&
          event.clientX <= box.right &&
          event.clientY >= box.top &&
          event.clientY <= box.bottom;
        if (!inside) onClose();
      }}
      className={cn(
        'bg-surface border-border text-content m-auto rounded-xl border p-0 shadow-lg',
        // Sized against the *viewport*, both ways. `dvh` rather than `vh` because the
        // mobile browser's toolbars and the on-screen keyboard both change the visible
        // height, and `vh` measures the tallest of those - which is how a dialog ends up
        // with its footer, and so its submit button, below the fold on a phone.
        'max-h-[calc(100dvh-1.5rem)] w-[calc(100vw-1.5rem)] max-w-[34rem] overflow-hidden',
        'backdrop:bg-black/40 backdrop:backdrop-blur-sm',
        className,
      )}
    >
      {/* The flex column lives on a child rather than on the dialog itself: an author
          `display` on `<dialog>` overrides the UA's `dialog:not([open]) { display: none }`
          and the dialog would render while closed. `max-h-[inherit]` passes the cap down. */}
      <div className="flex max-h-[inherit] flex-col">
        <div className="border-border shrink-0 border-b px-4 py-4 sm:px-5">
          <h2 className="text-content text-[15px] font-semibold">{title}</h2>
          {description && <p className="text-content-muted mt-0.5 text-[13px]">{description}</p>}
        </div>

        {/* Only this scrolls, so the title stays visible and the footer stays reachable. */}
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-5">{children}</div>

        {footer && (
          <div className="border-border bg-surface-sunken/40 flex shrink-0 flex-wrap items-center justify-end gap-2 border-t px-4 py-3 sm:px-5">
            {footer}
          </div>
        )}
      </div>
    </dialog>
  );
}
