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
        'bg-surface border-border text-content m-auto w-[min(92vw,34rem)] rounded-xl border p-0 shadow-lg',
        'backdrop:bg-black/40 backdrop:backdrop-blur-sm',
        className,
      )}
    >
      {/* A form wrapper so Enter submits and the buttons can use `formMethod`. The
          submit handler lives on the caller's own form inside `children`. */}
      <div className="border-border border-b px-5 py-4">
        <h2 className="text-content text-[15px] font-semibold">{title}</h2>
        {description && <p className="text-content-muted mt-0.5 text-[13px]">{description}</p>}
      </div>

      <div className="max-h-[70vh] overflow-y-auto px-5 py-4">{children}</div>

      {footer && (
        <div className="border-border bg-surface-sunken/40 flex items-center justify-end gap-2 border-t px-5 py-3">
          {footer}
        </div>
      )}
    </dialog>
  );
}
