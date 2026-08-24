import { forwardRef, useId, type InputHTMLAttributes, type ReactNode } from 'react';

import { cn } from '@/lib/cn';

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> {
  label?: string;
  /** Validation message. Its presence switches the field into the error state. */
  error?: string | undefined;
  /** Guidance below the field, hidden while an error is shown. */
  hint?: string;
  leftIcon?: ReactNode;
  rightSlot?: ReactNode;
}

/**
 * A labelled text input.
 *
 * The accessibility wiring is why this is a component and not a styled
 * `<input>`: label association, `aria-invalid`, and `aria-describedby` pointing
 * at whichever of hint/error is currently rendered. Repeating that at every call
 * site is how forms end up unusable with a screen reader.
 */
export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, label, error, hint, leftIcon, rightSlot, id, required, ...props },
  ref,
) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const errorId = `${inputId}-error`;
  const hintId = `${inputId}-hint`;
  const describedBy = error ? errorId : hint ? hintId : undefined;

  return (
    <div className="w-full">
      {label && (
        <label
          htmlFor={inputId}
          className="text-content-secondary mb-1.5 block text-[13px] font-medium"
        >
          {label}
          {required && (
            <span className="text-danger ml-0.5" aria-hidden>
              *
            </span>
          )}
        </label>
      )}

      <div className="relative">
        {leftIcon && (
          <span
            className="text-content-muted pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 [&>svg]:h-4 [&>svg]:w-4"
            aria-hidden
          >
            {leftIcon}
          </span>
        )}

        <input
          ref={ref}
          id={inputId}
          required={required}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          className={cn(
            'bg-surface text-content h-9 w-full rounded-md border text-sm',
            'transition-[border-color,box-shadow] duration-[var(--duration-fast)]',
            'focus:ring-ring/25 focus:border-primary focus:ring-2 focus:outline-none',
            'disabled:bg-surface-sunken disabled:cursor-not-allowed disabled:opacity-60',
            leftIcon ? 'pl-9' : 'pl-3',
            rightSlot ? 'pr-10' : 'pr-3',
            error ? 'border-danger focus:border-danger focus:ring-danger/25' : 'border-border',
            className,
          )}
          {...props}
        />

        {rightSlot && (
          <span className="absolute top-1/2 right-1 -translate-y-1/2">{rightSlot}</span>
        )}
      </div>

      {error ? (
        // `role="alert"` so the message is announced the moment it appears,
        // instead of only being found if the user navigates onto it.
        <p id={errorId} role="alert" className="text-danger mt-1.5 text-[13px]">
          {error}
        </p>
      ) : hint ? (
        <p id={hintId} className="text-content-muted mt-1.5 text-[13px]">
          {hint}
        </p>
      ) : null}
    </div>
  );
});
