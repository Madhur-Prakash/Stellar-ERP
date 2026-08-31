/**
 * A time of day, chosen from two columns rather than typed into a native field.
 *
 * `<input type="time">` was the obvious choice and the wrong one. Three problems,
 * and only the first is cosmetic:
 *
 * - **The browser draws its own clock button**, and only that button opens the
 *   picker. The rest of the field - the part that looks like a control - does
 *   nothing but move a text caret, so the affordance and the target disagree.
 * - **Its popup is OS-rendered**, so it ignores the app's radius, spacing and
 *   theme, exactly like a native `<select>`'s option list.
 * - **It emits a change per segment.** Typing "03:30" fires at `03:00` on the way,
 *   and each of those intermediate values is a real setting that would take effect
 *   if the next keystroke never came - so saving on change is wrong and saving on
 *   blur means the value the user sees is not yet the value in force.
 *
 * Two columns instead of one list of 1,440 entries: an hour and a minute are how
 * people say a time, and a single flat list is unscannable at that length.
 *
 * Every commit here is a complete, valid time, so there is no intermediate state to
 * guard against and `onChange` can fire immediately.
 */
import { Clock } from 'lucide-react';
import { useCallback, useEffect, useLayoutEffect, useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import { cn } from '@/lib/cn';

export interface TimePickerProps {
  /** `HH:MM`, 24-hour. */
  value: string;
  onChange: (value: string) => void;
  label?: string;
  hint?: ReactNode;
  error?: string | undefined;
  disabled?: boolean;
  /** Minute granularity. 1 offers every minute; 5 or 15 shorten the column. */
  minuteStep?: number;
  className?: string;
}

const pad = (n: number) => String(n).padStart(2, '0');

/** Parses `HH:MM`, falling back to midnight rather than throwing on a bad value. */
function parse(value: string): { hour: number; minute: number } {
  const [h, m] = value.split(':');
  const hour = Number(h);
  const minute = Number(m);
  return {
    hour: Number.isInteger(hour) && hour >= 0 && hour <= 23 ? hour : 0,
    minute: Number.isInteger(minute) && minute >= 0 && minute <= 59 ? minute : 0,
  };
}

export function TimePicker({
  value,
  onChange,
  label,
  hint,
  error,
  disabled = false,
  minuteStep = 1,
  className,
}: TimePickerProps) {
  const id = useId();
  const triggerId = `${id}-trigger`;
  const errorId = `${id}-error`;
  const hintId = `${id}-hint`;

  const [open, setOpen] = useState(false);
  const [above, setAbove] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const { hour, minute } = parse(value);

  const hours = Array.from({ length: 24 }, (_, i) => i);
  const minutes = Array.from({ length: Math.ceil(60 / minuteStep) }, (_, i) => i * minuteStep);

  const close = useCallback((returnFocus = true) => {
    setOpen(false);
    if (returnFocus) triggerRef.current?.focus();
  }, []);

  const set = (nextHour: number, nextMinute: number) => {
    onChange(`${pad(nextHour)}:${pad(nextMinute)}`);
  };

  // Flip above the trigger when there is no room below, measured before paint so
  // the panel never appears in the wrong place for a frame. Recomputed on resize
  // rather than closing, because resizing is not a signal that the user is done.
  useLayoutEffect(() => {
    if (!open) return;

    const place = () => {
      const box = triggerRef.current?.getBoundingClientRect();
      if (!box) return;
      setAbove(box.bottom + 280 > window.innerHeight && box.top > 280);
    };

    place();
    window.addEventListener('resize', place);
    return () => window.removeEventListener('resize', place);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (triggerRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      close(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [close, open]);

  // Centre the chosen hour and minute in their columns, scrolling *only* the
  // columns.
  //
  // Not `scrollIntoView`: it walks up and moves every scrollable ancestor, so on
  // this page the panel would open and the layout behind it would jump. And a
  // layout effect rather than `useEffect`, because the column has no height until
  // after layout - measuring a frame too early gave `clientHeight === 0` and left
  // both columns parked at midnight regardless of what was selected.
  useLayoutEffect(() => {
    if (!open) return;
    panelRef.current?.querySelectorAll<HTMLElement>('[data-column]').forEach((column) => {
      const selected = column.querySelector<HTMLElement>('[aria-selected="true"]');
      if (!selected) return;
      column.scrollTop = selected.offsetTop - (column.clientHeight - selected.offsetHeight) / 2;
    });
  }, [open]);

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (!open) {
      if (['Enter', ' ', 'ArrowDown', 'ArrowUp'].includes(event.key)) {
        event.preventDefault();
        if (!disabled) setOpen(true);
      }
      return;
    }
    // Arrows adjust the time in place, so the whole control is usable without ever
    // reaching for the columns.
    switch (event.key) {
      case 'Escape':
        event.preventDefault();
        close();
        return;
      case 'Enter':
        event.preventDefault();
        close();
        return;
      case 'ArrowUp':
        event.preventDefault();
        set(hour, (minute + minuteStep) % 60);
        return;
      case 'ArrowDown':
        event.preventDefault();
        set(hour, (minute - minuteStep + 60) % 60);
        return;
      case 'ArrowRight':
        event.preventDefault();
        set((hour + 1) % 24, minute);
        return;
      case 'ArrowLeft':
        event.preventDefault();
        set((hour + 23) % 24, minute);
        return;
      default:
        break;
    }
  };

  // 220 / 200 / 32 are shared with app_frontend/lib/widgets/app_time_field.dart.
  // Explicit values rather than scale steps: the two have to agree to the pixel,
  // and `max-h-56` against `height: 200` is precisely how they would drift apart.
  const column = 'h-[200px] flex-1 overflow-auto p-1';
  const cell = (selected: boolean) =>
    cn(
      'flex h-8 cursor-pointer items-center justify-center rounded-lg text-[13px] tabular-nums',
      'my-px hover:bg-surface-sunken focus-visible:ring-primary focus-visible:ring-2 focus-visible:outline-none',
      selected
        ? 'bg-primary text-primary-content hover:bg-primary-hover font-medium'
        : 'text-content-secondary',
    );

  return (
    <div className={cn('w-full', className)}>
      {label && (
        <label
          htmlFor={triggerId}
          className="text-content-secondary mb-1.5 block text-[13px] font-medium"
        >
          {label}
        </label>
      )}

      <div className="relative">
        {/*
          The trigger is the whole field, not an icon inside it. That is the entire
          reason this component exists rather than a native time input.
        */}
        <button
          ref={triggerRef}
          id={triggerId}
          type="button"
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : hint ? hintId : undefined}
          disabled={disabled}
          onClick={() => (open ? close() : setOpen(true))}
          onKeyDown={onKeyDown}
          className={cn(
            // Matched to Input, Select and Listbox: same border, radius, padding.
            'border-border bg-surface text-content flex w-full items-center justify-between gap-2',
            'rounded-lg border px-3 py-2 text-left text-[13px]',
            'focus:border-primary focus:ring-primary/20 outline-none focus:ring-2',
            'disabled:bg-surface-sunken disabled:cursor-not-allowed disabled:opacity-60',
            error && 'border-danger focus:border-danger focus:ring-danger/20',
          )}
        >
          <span className="tabular-nums">
            {pad(hour)}:{pad(minute)}
          </span>
          <Clock className="text-content-muted size-4 shrink-0" aria-hidden />
        </button>

        {open && (
          <div
            ref={panelRef}
            role="dialog"
            aria-label={label ?? 'Choose a time'}
            className={cn(
              'border-border bg-surface absolute z-50 w-[220px] overflow-hidden rounded-xl border shadow-lg',
              above ? 'bottom-full mb-1.5' : 'top-full mt-1.5',
            )}
          >
            <div className="text-content-muted border-border flex border-b text-[11px] font-medium tracking-wide uppercase">
              <span className="flex-1 px-3 py-2">Hour</span>
              <span className="flex-1 px-3 py-2">Minute</span>
            </div>

            <div className="divide-border flex divide-x">
              <div className={column} data-column="hour" role="listbox" aria-label="Hour">
                {hours.map((h) => (
                  <div
                    key={h}
                    role="option"
                    aria-selected={h === hour}
                    tabIndex={-1}
                    onClick={() => set(h, minute)}
                    className={cell(h === hour)}
                  >
                    {pad(h)}
                  </div>
                ))}
              </div>

              <div className={column} data-column="minute" role="listbox" aria-label="Minute">
                {minutes.map((m) => (
                  <div
                    key={m}
                    role="option"
                    aria-selected={m === minute}
                    tabIndex={-1}
                    onClick={() => set(hour, m)}
                    className={cell(m === minute)}
                  >
                    {pad(m)}
                  </div>
                ))}
              </div>
            </div>

            <div className="border-border flex items-center justify-between gap-2 border-t px-2 py-1.5">
              <button
                type="button"
                onClick={() => {
                  const now = new Date();
                  set(now.getHours(), now.getMinutes());
                  // `set` only lifts the value; the columns are scrolled by the
                  // layout effect above, which is keyed on `open` and so does not
                  // re-run here. Scrolled explicitly rather than by widening that
                  // key, which would fight the user's own scrolling.
                  requestAnimationFrame(() =>
                    panelRef.current
                      ?.querySelectorAll<HTMLElement>('[data-column]')
                      .forEach((column) => {
                        const target = column.querySelector<HTMLElement>('[aria-selected="true"]');
                        if (!target) return;
                        column.scrollTop =
                          target.offsetTop - (column.clientHeight - target.offsetHeight) / 2;
                      }),
                  );
                }}
                className="text-content-secondary hover:text-content rounded px-1.5 py-1 text-[12px]"
              >
                Now
              </button>
              <button
                type="button"
                onClick={() => close()}
                className="text-primary rounded px-1.5 py-1 text-[12px] font-medium"
              >
                Done
              </button>
            </div>
          </div>
        )}
      </div>

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
}
