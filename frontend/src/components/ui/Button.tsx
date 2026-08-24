import { Loader2 } from 'lucide-react';
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';

import { cn } from '@/lib/cn';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'destructive' | 'outline' | 'link';
export type ButtonSize = 'sm' | 'md' | 'lg' | 'icon';

/**
 * Variants as a lookup table rather than a `cva` call: one fewer dependency, and
 * the complete class string for each variant is visible in one place - which is
 * what you want when working out why a button looks wrong.
 */
const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    'bg-primary text-primary-content shadow-xs hover:bg-primary-hover active:scale-[0.98] disabled:hover:bg-primary',
  secondary:
    'bg-surface-sunken text-content border border-border hover:bg-surface-hover active:scale-[0.98]',
  outline:
    'border border-border-strong bg-transparent text-content hover:bg-surface-hover active:scale-[0.98]',
  ghost: 'bg-transparent text-content-secondary hover:bg-surface-hover hover:text-content',
  destructive:
    'bg-danger text-white shadow-xs hover:brightness-110 active:scale-[0.98] disabled:hover:brightness-100',
  link: 'bg-transparent text-primary underline-offset-4 hover:underline p-0 h-auto',
};

const SIZES: Record<ButtonSize, string> = {
  sm: 'h-8 px-3 text-[13px] gap-1.5 rounded-md',
  md: 'h-9 px-4 text-sm gap-2 rounded-md',
  lg: 'h-11 px-6 text-[15px] gap-2 rounded-lg',
  icon: 'h-9 w-9 rounded-md',
};

const BASE =
  'relative inline-flex items-center justify-center font-medium whitespace-nowrap select-none ' +
  'transition-[background-color,color,box-shadow,transform] duration-[var(--duration-fast)] ' +
  'focus-visible:ring-ring focus-visible:ring-offset-canvas focus-visible:ring-2 ' +
  'focus-visible:ring-offset-2 focus-visible:outline-none ' +
  'disabled:pointer-events-none disabled:opacity-50';

/**
 * The button class recipe, for elements that must not be a `<button>`.
 *
 * A navigation control has to render as an `<a>` - a `<Link>` nested inside a
 * `<button>` is invalid HTML, and a `<button>` that navigates loses
 * middle-click, "open in new tab", and the browser's own affordances. This lets
 * a link look identical without pretending to be a button.
 */
// Co-located with the component whose styles it mirrors; splitting them would
// guarantee they drift apart.
// eslint-disable-next-line react-refresh/only-export-components
export function buttonClasses(
  variant: ButtonVariant = 'primary',
  size: ButtonSize = 'md',
  extra?: string,
): string {
  return cn(BASE, VARIANTS[variant], SIZES[size], extra);
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Shows a spinner and blocks interaction. */
  loading?: boolean;
  leftIcon?: ReactNode;
  rightIcon?: ReactNode;
  fullWidth?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    className,
    variant = 'primary',
    size = 'md',
    loading = false,
    leftIcon,
    rightIcon,
    fullWidth,
    disabled,
    children,
    type = 'button',
    ...props
  },
  ref,
) {
  return (
    <button
      ref={ref}
      // Defaults to `button`: an unset type inside a form is `submit`, which
      // makes an unrelated button silently submit it.
      type={type}
      // `loading` must also disable, or a double-click fires the action twice.
      disabled={disabled || loading}
      // Announces the busy state; a spinner alone is invisible to a screen reader.
      aria-busy={loading || undefined}
      className={cn(BASE, VARIANTS[variant], SIZES[size], fullWidth && 'w-full', className)}
      {...props}
    >
      {loading ? <Loader2 className="h-4 w-4 shrink-0 animate-spin" aria-hidden /> : leftIcon}
      {children}
      {!loading && rightIcon}
    </button>
  );
});
