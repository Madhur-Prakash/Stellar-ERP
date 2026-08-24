/**
 * A labelled `<select>`, matching `Input` exactly.
 *
 * It exists because hand-rolled `label` + `select` pairs were drifting: the raw markup
 * used different padding from `Input`, so on a row mixing the two the fields were
 * visibly different heights and the labels sat at different baselines. Sharing one
 * component is the only way that stays fixed.
 *
 * It also carries the accessibility wiring `Input` has - label association,
 * `aria-invalid`, `aria-describedby` - and a native chevron, since a `<select>` with
 * `appearance-none` and no replacement indicator looks like a text field.
 *
 * `optgroup` is supported through `groups`, because a flat list of sixty categories is
 * unusable and grouping them is the difference between scanning and hunting.
 */
import { forwardRef, useId, type ReactNode, type SelectHTMLAttributes } from 'react';

import { cn } from '@/lib/cn';

export interface SelectOption {
  value: string;
  label: string;
}

export interface SelectGroup {
  label: string;
  options: SelectOption[];
}

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'size'> {
  label?: string;
  error?: string | undefined;
  hint?: ReactNode;
  /** Flat options. Ignored when `groups` is given. */
  options?: SelectOption[];
  /** Grouped options, rendered as `<optgroup>`. */
  groups?: SelectGroup[];
  /** Leading entry for "nothing chosen". Omit when a value is always required. */
  placeholder?: string;
  /** Rendered to the right of the label - an "add new" affordance, usually. */
  action?: ReactNode;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, label, error, hint, options, groups, placeholder, action, id, required, ...props },
  ref,
) {
  const generatedId = useId();
  const selectId = id ?? generatedId;
  const errorId = `${selectId}-error`;
  const hintId = `${selectId}-hint`;
  const describedBy = error ? errorId : hint ? hintId : undefined;

  return (
    <div className="w-full">
      {label && (
        <div className="mb-1.5 flex items-baseline justify-between gap-2">
          <label htmlFor={selectId} className="text-content-secondary text-[13px] font-medium">
            {label}
            {required && (
              <span className="text-danger ml-0.5" aria-hidden>
                *
              </span>
            )}
          </label>
          {action}
        </div>
      )}

      <select
        ref={ref}
        id={selectId}
        required={required}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        className={cn(
          // Matched to Input: same border, radius, padding, and focus treatment.
          'border-border bg-surface text-content w-full rounded-lg border px-3 py-2 text-[13px]',
          'focus:border-primary focus:ring-primary/20 outline-none focus:ring-2',
          'disabled:bg-surface-sunken disabled:cursor-not-allowed disabled:opacity-60',
          error && 'border-danger focus:border-danger focus:ring-danger/20',
          className,
        )}
        {...props}
      >
        {placeholder !== undefined && <option value="">{placeholder}</option>}

        {groups
          ? groups
              .filter((group) => group.options.length > 0)
              .map((group) => (
                <optgroup key={group.label} label={group.label}>
                  {group.options.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </optgroup>
              ))
          : (options ?? []).map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
      </select>

      {error ? (
        <p id={errorId} role="alert" className="text-danger mt-1.5 text-[12px]">
          {error}
        </p>
      ) : hint ? (
        <p id={hintId} className="text-content-muted mt-1.5 text-[12px]">
          {hint}
        </p>
      ) : null}
    </div>
  );
});
