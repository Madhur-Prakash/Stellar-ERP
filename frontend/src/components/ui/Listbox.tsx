/**
 * A dropdown whose open list we actually control.
 *
 * `Select` is a native `<select>`, and that is the right default: it is accessible
 * for free, it uses the platform's own touch and keyboard behaviour, and it never
 * gets trapped inside an overflow container. But **its option list is drawn by the
 * operating system**, so nothing about it responds to CSS - not the corner radius,
 * not the padding, not the theme. On Windows the list renders square and bright
 * white, which next to this app's rounded, theme-aware surfaces looks like a piece
 * of a different program, and in dark mode it is glaring rather than merely
 * inconsistent.
 *
 * So this exists for the cases where the open list is on screen long enough to
 * matter. It is not a replacement for `Select` and should not become one: a native
 * control is better everywhere the list is incidental, and a form of forty fields
 * does not want forty popovers.
 *
 * What it reimplements, because a div is not a `<select>` and the browser will not
 * do any of it for you:
 *
 * - `role="listbox"` with `aria-activedescendant`, so a screen reader announces the
 *   focused option rather than silence.
 * - Arrow keys, Home/End, Enter, Space, Escape, and type-ahead on a printable key.
 * - Focus returned to the trigger on close - otherwise focus lands on `<body>` and
 *   the next Tab starts from the top of the page.
 * - Close on outside pointerdown. Not on scroll: the panel is positioned absolutely
 *   inside the trigger'''s wrapper, so it already tracks the trigger, and a
 *   close-on-scroll handler shut the list the instant it opened - scrolling the
 *   active option into view fired the very event that closed it.
 */
import { Check, ChevronDown } from 'lucide-react';
import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';

import { cn } from '@/lib/cn';

export interface ListboxOption {
  value: string;
  label: string;
  /** Second line, for an option whose label alone is ambiguous. */
  detail?: string;
  disabled?: boolean;
}

export interface ListboxProps {
  value: string;
  onChange: (value: string) => void;
  options: ListboxOption[];
  label?: string;
  hint?: ReactNode;
  error?: string | undefined;
  disabled?: boolean;
  placeholder?: string;
  className?: string;
  /** Accessible name when there is no visible `label`. */
  'aria-label'?: string;
}

export function Listbox({
  value,
  onChange,
  options,
  label,
  hint,
  error,
  disabled = false,
  placeholder = 'Select…',
  className,
  'aria-label': ariaLabel,
}: ListboxProps) {
  const id = useId();
  const triggerId = `${id}-trigger`;
  const listId = `${id}-list`;
  const errorId = `${id}-error`;
  const hintId = `${id}-hint`;

  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [above, setAbove] = useState(false);

  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  // Type-ahead buffer. A ref rather than state: it must not cause a render, and a
  // stale closure over it would break the second keystroke of every search.
  const typed = useRef({ query: '', at: 0 });

  const selectedIndex = useMemo(
    () => options.findIndex((option) => option.value === value),
    [options, value],
  );
  const selected = selectedIndex >= 0 ? options[selectedIndex] : undefined;

  const close = useCallback(
    (returnFocus = true) => {
      setOpen(false);
      if (returnFocus) triggerRef.current?.focus();
    },
    [],
  );

  const commit = useCallback(
    (index: number) => {
      const option = options[index];
      if (!option || option.disabled) return;
      onChange(option.value);
      close();
    },
    [close, onChange, options],
  );

  // Opening always starts from the current selection, not from wherever the list
  // was left last time - an arrow key should move relative to what is chosen.
  const openList = useCallback(() => {
    if (disabled) return;
    setActive(selectedIndex >= 0 ? selectedIndex : 0);
    setOpen(true);
  }, [disabled, selectedIndex]);

  // Flip above the trigger when there is not room below. Measured after layout and
  // before paint, so the panel never appears in the wrong place for a frame.
  //
  // Recomputed on resize rather than closing the list, because a window resize is
  // not a signal that the user is done choosing.
  useLayoutEffect(() => {
    if (!open) return;

    const place = () => {
      const trigger = triggerRef.current;
      if (!trigger) return;
      const box = trigger.getBoundingClientRect();
      const estimated = Math.min(options.length * 40 + 16, 320);
      setAbove(box.bottom + estimated > window.innerHeight && box.top > estimated);
    };

    place();
    window.addEventListener('resize', place);
    return () => window.removeEventListener('resize', place);
  }, [open, options.length]);

  useEffect(() => {
    if (!open) return;
    const list = listRef.current;
    const option = list?.querySelector<HTMLElement>('[data-active="true"]');
    if (!list || !option) return;

    // Scrolled by hand rather than with `scrollIntoView`, which walks up and
    // scrolls every ancestor that can move - including the page. Keeping it inside
    // the panel is what stops opening the list from shifting the layout behind it.
    const listBox = list.getBoundingClientRect();
    const optionBox = option.getBoundingClientRect();
    if (optionBox.top < listBox.top) {
      list.scrollTop -= listBox.top - optionBox.top;
    } else if (optionBox.bottom > listBox.bottom) {
      list.scrollTop += optionBox.bottom - listBox.bottom;
    }
  }, [open, active]);

  useEffect(() => {
    if (!open) return;

    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (triggerRef.current?.contains(target) || listRef.current?.contains(target)) return;
      // No focus return here: the pointer has already moved focus somewhere the
      // user chose, and yanking it back to the trigger would fight them.
      close(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [close, open]);

  const step = useCallback(
    (from: number, direction: 1 | -1) => {
      const count = options.length;
      for (let i = 1; i <= count; i += 1) {
        const next = (from + direction * i + count * count) % count;
        if (!options[next]?.disabled) return next;
      }
      return from;
    },
    [options],
  );

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (!open) {
      if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) {
        event.preventDefault();
        openList();
      }
      return;
    }

    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault();
        setActive((index) => step(index, 1));
        return;
      case 'ArrowUp':
        event.preventDefault();
        setActive((index) => step(index, -1));
        return;
      case 'Home':
        event.preventDefault();
        setActive(step(-1, 1));
        return;
      case 'End':
        event.preventDefault();
        setActive(step(options.length, -1));
        return;
      case 'Enter':
      case ' ':
        event.preventDefault();
        commit(active);
        return;
      case 'Escape':
        event.preventDefault();
        close();
        return;
      case 'Tab':
        // Tab commits nothing and closes: a dropdown that swallowed Tab would trap
        // keyboard users on this field.
        close(false);
        return;
      default:
        break;
    }

    if (event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey) {
      const now = Date.now();
      // A second keystroke within a second extends the search rather than starting
      // a new one, so "se" finds "September" instead of stopping at every "e".
      typed.current.query =
        now - typed.current.at < 1000 ? typed.current.query + event.key : event.key;
      typed.current.at = now;

      const query = typed.current.query.toLowerCase();
      const found = options.findIndex(
        (option) => !option.disabled && option.label.toLowerCase().startsWith(query),
      );
      if (found >= 0) setActive(found);
    }
  };

  const panel = cn(
    'border-border bg-surface absolute z-50 max-h-80 w-full overflow-auto rounded-xl border p-1 shadow-lg',
    above ? 'bottom-full mb-1.5' : 'top-full mt-1.5',
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
        <button
          ref={triggerRef}
          id={triggerId}
          type="button"
          role="combobox"
          aria-controls={open ? listId : undefined}
          aria-expanded={open}
          aria-haspopup="listbox"
          aria-label={ariaLabel}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : hint ? hintId : undefined}
          disabled={disabled}
          onClick={() => (open ? close() : openList())}
          onKeyDown={onKeyDown}
          className={cn(
            // Matched to Select and Input: same border, radius, padding, focus ring.
            'border-border bg-surface text-content flex w-full items-center justify-between gap-2',
            'rounded-lg border px-3 py-2 text-left text-[13px]',
            'focus:border-primary focus:ring-primary/20 outline-none focus:ring-2',
            'disabled:bg-surface-sunken disabled:cursor-not-allowed disabled:opacity-60',
            error && 'border-danger focus:border-danger focus:ring-danger/20',
          )}
        >
          <span className={cn('truncate', !selected && 'text-content-muted')}>
            {selected?.label ?? placeholder}
          </span>
          <ChevronDown
            className={cn(
              'text-content-muted size-4 shrink-0 transition-transform',
              open && 'rotate-180',
            )}
            aria-hidden
          />
        </button>

        {open && (
          <ul
            ref={listRef}
            id={listId}
            role="listbox"
            aria-labelledby={label ? triggerId : undefined}
            aria-activedescendant={`${id}-option-${active}`}
            tabIndex={-1}
            className={panel}
          >
            {options.map((option, index) => {
              const isSelected = option.value === value;
              const isActive = index === active;
              return (
                <li
                  key={option.value}
                  id={`${id}-option-${index}`}
                  role="option"
                  aria-selected={isSelected}
                  aria-disabled={option.disabled || undefined}
                  data-active={isActive}
                  // Pointer *move*, not enter: entering fires once on open if the
                  // cursor happens to be over an option, which silently moves the
                  // keyboard position away from the selection.
                  onPointerMove={() => !option.disabled && setActive(index)}
                  onClick={() => commit(index)}
                  className={cn(
                    'flex cursor-pointer items-start gap-2 rounded-lg px-2.5 py-2 text-[13px]',
                    isActive && 'bg-surface-sunken',
                    isSelected && 'text-primary font-medium',
                    option.disabled && 'cursor-not-allowed opacity-50',
                  )}
                >
                  <Check
                    className={cn('mt-0.5 size-3.5 shrink-0', !isSelected && 'invisible')}
                    aria-hidden
                  />
                  <span className="min-w-0">
                    <span className="block">{option.label}</span>
                    {option.detail && (
                      <span className="text-content-muted block text-[12px]">{option.detail}</span>
                    )}
                  </span>
                </li>
              );
            })}
          </ul>
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
