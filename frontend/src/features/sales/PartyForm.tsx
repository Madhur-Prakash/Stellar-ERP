/**
 * Create a customer or a supplier.
 *
 * **One component for both**, because they are the same form: a name, tax
 * registration, contact details, and payment terms. The two endpoints differ only in
 * which extra fields they accept, and duplicating this would mean the GSTIN hint and
 * the state-code explanation drift apart between the two screens.
 *
 * Only `name` is required. Everything else is optional on the server too, and asking
 * a shopkeeper for a GSTIN and a credit limit before they can raise their first
 * invoice is how software gets abandoned at step one. The GSTIN is worth prompting
 * for, though - it is what derives the place of supply, so **without it every invoice
 * is treated as intra-state** and the CGST/SGST vs IGST split may be wrong.
 */
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { NumberInput } from '@/components/ui/NumberInput';
import { Modal } from '@/components/ui/Modal';
import { inventoryApi } from '@/features/inventory/api';
import { salesApi } from '@/features/sales/api';
import { ApiError } from '@/lib/api';

export type PartyKind = 'customer' | 'supplier';

interface Draft {
  name: string;
  gstin: string;
  email: string;
  phone: string;
  city: string;
  payment_terms_days: string;
}

const BLANK: Draft = {
  name: '',
  gstin: '',
  email: '',
  phone: '',
  city: '',
  payment_terms_days: '30',
};

/** A GSTIN is 15 characters: 2-digit state code, 10-char PAN, then 3 more. */
const GSTIN_LENGTH = 15;

/**
 * The part of the response this form uses.
 *
 * `Customer` and `Supplier` are not assignable to one another - a supplier has `city`
 * where a customer has `billing_city` - so the mutation is typed to what both
 * genuinely share rather than to a union that every consumer would have to narrow.
 */
interface CreatedParty {
  id: string;
  name: string;
  code: string;
}

export function PartyFormModal({
  kind,
  open,
  onClose,
  onCreated,
}: {
  kind: PartyKind;
  open: boolean;
  onClose: () => void;
  /** Receives the new record's id, so a caller mid-form can select it immediately. */
  onCreated?: (id: string, name: string) => void;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Draft>(BLANK);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const label = kind === 'customer' ? 'customer' : 'supplier';

  const create = useMutation<CreatedParty>({
    mutationFn: () => {
      const common = {
        name: draft.name.trim(),
        ...(draft.gstin.trim() ? { gstin: draft.gstin.trim().toUpperCase() } : {}),
        ...(draft.email.trim() ? { email: draft.email.trim() } : {}),
        ...(draft.phone.trim() ? { phone: draft.phone.trim() } : {}),
        payment_terms_days: Number(draft.payment_terms_days) || 0,
      };
      const city = draft.city.trim();

      // **The city field is named differently on each side.** A customer has a
      // billing address and a separate shipping one, so its column is
      // `billing_city`; a supplier has one address and uses `city`. Both request
      // schemas are `extra="forbid"`, so sending the wrong key is a 422 - and
      // TypeScript cannot catch it here, because excess-property checking does not
      // see through a conditional spread.
      return kind === 'customer'
        ? salesApi.createCustomer({ ...common, ...(city ? { billing_city: city } : {}) })
        : inventoryApi.createSupplier({ ...common, ...(city ? { city } : {}) });
    },
    onSuccess: (created) => {
      // Both list views and every dropdown that reads them.
      void queryClient.invalidateQueries({
        queryKey: [kind === 'customer' ? 'customers' : 'suppliers'],
      });
      toast.success(`${created.name} added`, {
        description: created.code ? `Code ${created.code}` : undefined,
      });
      onCreated?.(created.id, created.name);
      setDraft(BLANK);
      setFieldErrors({});
      onClose();
    },
    onError: (error) => {
      if (error instanceof ApiError) {
        setFieldErrors(error.fieldErrors);
        toast.error(error.message);
      } else {
        toast.error(`Could not add the ${label}`);
      }
    },
  });

  const gstin = draft.gstin.trim();
  const gstinLooksWrong = gstin.length > 0 && gstin.length !== GSTIN_LENGTH;

  const submit = () => {
    if (draft.name.trim() === '') {
      setFieldErrors({ name: 'A name is required' });
      return;
    }
    create.mutate();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={kind === 'customer' ? 'New customer' : 'New supplier'}
      description={
        kind === 'customer'
          ? 'Only a name is required. A GSTIN lets the correct GST split be applied.'
          : 'Only a name is required. A GSTIN lets input GST be claimed correctly.'
      }
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={create.isPending}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={create.isPending || draft.name.trim() === ''}>
            {create.isPending ? 'Adding…' : `Add ${label}`}
          </Button>
        </>
      }
    >
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <Input
          label="Name"
          required
          autoFocus
          placeholder={kind === 'customer' ? 'Sharma Enterprises' : 'Mumbai Wholesale Traders'}
          value={draft.name}
          error={fieldErrors['name']}
          onChange={(event) => setDraft({ ...draft, name: event.target.value })}
        />

        <Input
          label="GSTIN"
          placeholder="27AABCU9603R1ZM"
          value={draft.gstin}
          error={fieldErrors['gstin']}
          hint={
            gstinLooksWrong
              ? `A GSTIN is ${GSTIN_LENGTH} characters - this is ${gstin.length}.`
              : 'Optional. Its first two digits are the state, which decides CGST/SGST versus IGST.'
          }
          onChange={(event) => setDraft({ ...draft, gstin: event.target.value.toUpperCase() })}
        />

        <div className="grid gap-3 sm:grid-cols-2">
          <Input
            label="Email"
            type="email"
            placeholder="accounts@example.com"
            value={draft.email}
            error={fieldErrors['email']}
            onChange={(event) => setDraft({ ...draft, email: event.target.value })}
          />
          <Input
            label="Phone"
            placeholder="022 2345 6789"
            value={draft.phone}
            onChange={(event) => setDraft({ ...draft, phone: event.target.value })}
          />
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <Input
            label="City"
            placeholder="Mumbai"
            value={draft.city}
            onChange={(event) => setDraft({ ...draft, city: event.target.value })}
          />
          <NumberInput
            label="Payment terms"
            // Whole days: "30.5 days until due" is not a thing anyone means.
            decimals={0}
            value={draft.payment_terms_days}
            hint="Days until due"
            onValueChange={(payment_terms_days) => setDraft({ ...draft, payment_terms_days })}
          />
        </div>

        {/* Submit on Enter without a visible duplicate button. */}
        <button type="submit" className="hidden" aria-hidden tabIndex={-1} />
      </form>
    </Modal>
  );
}
