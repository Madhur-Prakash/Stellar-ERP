import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

/**
 * Merge class names, resolving Tailwind conflicts.
 *
 * `clsx` flattens conditionals; `twMerge` then makes later utilities win over
 * earlier ones in the same group. Without it, `cn('p-2', 'p-4')` emits both and
 * the outcome depends on CSS source order - so a component's `className` prop
 * could not reliably override its own defaults.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
