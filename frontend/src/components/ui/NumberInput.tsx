/**
 * An input that will only hold a number.
 *
 * **Not `type="number"`.** That control looks right and then misbehaves in ways that
 * matter for money: it accepts `e`, `+`, and `-` as valid characters, the scroll wheel
 * silently changes the value while the page is being scrolled, and browsers disagree
 * about what `value` reads back as while a partial number is being typed. Its spinner
 * arrows are meaningless on an amount, too.
 *
 * Instead this is a text input that filters what reaches state. Every keystroke, paste,
 * drag-drop, and autofill goes through `sanitiseDecimal`, so a letter simply never
 * appears - there is no error message to dismiss, because nothing invalid was accepted.
 * `inputMode="decimal"` still brings up the numeric keypad on a phone.
 */
import { forwardRef } from 'react';

import { Input, type InputProps } from '@/components/ui/Input';
import { type SanitiseOptions, sanitiseDecimal } from '@/lib/decimalInput';

export interface NumberInputProps
  extends Omit<InputProps, 'onChange' | 'value' | 'type' | 'inputMode'>, SanitiseOptions {
  value: string;
  /**
   * Called with the sanitised value.
   *
   * Deliberately not `onChange`: an `onChange` here would hand back an event whose
   * `target.value` had already been rewritten, which is the kind of quiet substitution
   * that is impossible to debug from a call site. A differently named prop also means the
   * type checker flags every field still using the old one.
   */
  onValueChange: (value: string) => void;
}

export const NumberInput = forwardRef<HTMLInputElement, NumberInputProps>(function NumberInput(
  { value, onValueChange, decimals, allowNegative, ...props },
  ref,
) {
  return (
    <Input
      ref={ref}
      {...props}
      // Text, not number - see the note at the top of this file.
      type="text"
      inputMode="decimal"
      // Keeps a password manager and the browser's own autofill away from a field that
      // holds an amount.
      autoComplete="off"
      value={value}
      onChange={(event) =>
        onValueChange(sanitiseDecimal(event.target.value, { decimals, allowNegative }))
      }
    />
  );
});
