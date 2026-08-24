import { Monitor, Moon, Sun } from 'lucide-react';

import { Button } from '@/components/ui/Button';
import { useTheme } from '@/features/theme/ThemeProvider';

const ICONS = { light: Sun, dark: Moon, system: Monitor } as const;
const LABELS = {
  light: 'Light theme',
  dark: 'Dark theme',
  system: 'System theme',
} as const;

/**
 * Cycles light -> dark -> system.
 *
 * A single button rather than a dropdown: three states are few enough to cycle
 * through, the current one is always visible as the icon, and it keeps the
 * header uncluttered. The `title` and `aria-label` name the *current* mode so
 * the control is not ambiguous to a screen reader.
 */
export function ThemeToggle() {
  const { theme, cycleTheme } = useTheme();
  const Icon = ICONS[theme];

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={cycleTheme}
      title={LABELS[theme]}
      aria-label={`${LABELS[theme]}. Click to change.`}
    >
      <Icon className="h-4 w-4" />
    </Button>
  );
}
