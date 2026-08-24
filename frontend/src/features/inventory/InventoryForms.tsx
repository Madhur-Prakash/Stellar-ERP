/**
 * Inventory write forms — product, warehouse, stock adjustment, transfer.
 *
 * **There is no delete, and that is deliberate.** A product named on a posted bill or a
 * stock movement cannot be removed without leaving a ledger entry pointing at nothing, so
 * the backend has no delete endpoint. Archiving (`is_active: false`) hides it from every
 * picker while the history stays intact and reversible, which is what "delete" actually
 * means for a product that has been traded.
 *
 * **A stock adjustment writes to the ledger, so it asks for a reason.** Correcting stock
 * up or down changes the value of your inventory and posts the difference to an expense
 * account — it is a write-off with no commercial document behind it, which is exactly what
 * an auditor looks for. A blank reason makes that unanswerable months later.
 */
import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { NumberInput } from '@/components/ui/NumberInput';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { type Product, inventoryApi } from '@/features/inventory/api';
import { useInvalidateInventory } from '@/features/inventory/hooks';
import { ApiError } from '@/lib/api';
import { formatMoney } from '@/lib/format';

// ---------------------------------------------------------------------------
// Product
// ---------------------------------------------------------------------------
interface ProductDraft {
  name: string;
  sku: string;
  barcode: string;
  hsn_code: string;
  unit: string;
  tax_rate: string;
  sale_price: string;
  purchase_price: string;
  reorder_level: string;
}

const BLANK_PRODUCT: ProductDraft = {
  name: '',
  sku: '',
  barcode: '',
  hsn_code: '',
  unit: 'pcs',
  tax_rate: '18',
  sale_price: '',
  purchase_price: '',
  reorder_level: '0',
};

function draftFrom(product: Product): ProductDraft {
  return {
    name: product.name,
    sku: product.sku,
    barcode: product.barcode ?? '',
    hsn_code: product.hsn_code ?? '',
    unit: product.unit,
    tax_rate: product.tax_rate,
    sale_price: product.sale_price,
    purchase_price: product.purchase_price,
    reorder_level: product.reorder_level,
  };
}

export function ProductFormModal({
  open,
  onClose,
  product,
}: {
  open: boolean;
  onClose: () => void;
  /** Present to edit, absent to create. */
  product?: Product | undefined;
}) {
  const invalidate = useInvalidateInventory();
  const editing = product !== undefined;
  const [draft, setDraft] = useState<ProductDraft>(product ? draftFrom(product) : BLANK_PRODUCT);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const save = useMutation({
    mutationFn: () => {
      const numeric = {
        tax_rate: draft.tax_rate || '0',
        sale_price: draft.sale_price || '0',
        purchase_price: draft.purchase_price || '0',
        reorder_level: draft.reorder_level || '0',
      };
      const optional = {
        ...(draft.barcode.trim() ? { barcode: draft.barcode.trim() } : {}),
        ...(draft.hsn_code.trim() ? { hsn_code: draft.hsn_code.trim() } : {}),
      };

      if (editing) {
        // `sku` is deliberately not sent: the update schema does not accept it, because a
        // code already printed on a label or quoted on a bill should not silently change.
        return inventoryApi.updateProduct(product.id, {
          name: draft.name.trim(),
          unit: draft.unit.trim() || 'pcs',
          ...numeric,
          ...optional,
        });
      }
      return inventoryApi.createProduct({
        name: draft.name.trim(),
        ...(draft.sku.trim() ? { sku: draft.sku.trim() } : {}),
        unit: draft.unit.trim() || 'pcs',
        ...numeric,
        ...optional,
      });
    },
    onSuccess: (saved) => {
      invalidate();
      toast.success(editing ? `${saved.name} updated` : `${saved.name} added`, {
        description: `SKU ${saved.sku}`,
      });
      onClose();
    },
    onError: (error) => {
      if (error instanceof ApiError) {
        setFieldErrors(error.fieldErrors);
        toast.error(error.message);
      } else {
        toast.error('Could not save the product');
      }
    },
  });

  const canSave = draft.name.trim() !== '';

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={editing ? `Edit ${product.name}` : 'New product'}
      description={
        editing
          ? 'The SKU cannot be changed — it may already be printed on a label or quoted on a bill.'
          : 'Only a name is required. A SKU is generated if you leave it blank.'
      }
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={save.isPending}>
            Cancel
          </Button>
          <Button onClick={() => save.mutate()} disabled={!canSave || save.isPending}>
            {save.isPending ? 'Saving…' : editing ? 'Save changes' : 'Add product'}
          </Button>
        </>
      }
    >
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (canSave) save.mutate();
        }}
      >
        <Input
          label="Name"
          required
          autoFocus
          placeholder="Widget Assembly A"
          value={draft.name}
          error={fieldErrors['name']}
          onChange={(event) => setDraft({ ...draft, name: event.target.value })}
        />

        <div className="grid gap-3 sm:grid-cols-2">
          <Input
            label="SKU"
            placeholder="Generated if blank"
            value={draft.sku}
            disabled={editing}
            error={fieldErrors['sku']}
            hint={editing ? 'Fixed once the product exists.' : undefined}
            onChange={(event) => setDraft({ ...draft, sku: event.target.value })}
          />
          <Input
            label="Barcode"
            placeholder="8901234567890"
            value={draft.barcode}
            error={fieldErrors['barcode']}
            onChange={(event) => setDraft({ ...draft, barcode: event.target.value })}
          />
        </div>

        <div className="grid gap-3 sm:grid-cols-3">
          <Input
            label="Unit"
            placeholder="pcs"
            value={draft.unit}
            onChange={(event) => setDraft({ ...draft, unit: event.target.value })}
          />
          <NumberInput
            label="GST %"
            value={draft.tax_rate}
            error={fieldErrors['tax_rate']}
            onValueChange={(tax_rate) => setDraft({ ...draft, tax_rate })}
          />
          <Input
            label="HSN code"
            placeholder="8483"
            value={draft.hsn_code}
            hint="For the GST return"
            onChange={(event) => setDraft({ ...draft, hsn_code: event.target.value })}
          />
        </div>

        <div className="grid gap-3 sm:grid-cols-3">
          <NumberInput
            label="Sale price"
            value={draft.sale_price}
            error={fieldErrors['sale_price']}
            onValueChange={(sale_price) => setDraft({ ...draft, sale_price })}
          />
          <NumberInput
            label="Purchase price"
            value={draft.purchase_price}
            error={fieldErrors['purchase_price']}
            onValueChange={(purchase_price) => setDraft({ ...draft, purchase_price })}
          />
          <NumberInput
            label="Reorder at"
            value={draft.reorder_level}
            hint="Flags a shortfall"
            onValueChange={(reorder_level) => setDraft({ ...draft, reorder_level })}
          />
        </div>

        <button type="submit" className="hidden" aria-hidden tabIndex={-1} />
      </form>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Warehouse
// ---------------------------------------------------------------------------
export function WarehouseFormModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const invalidate = useInvalidateInventory();
  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const [isDefault, setIsDefault] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const create = useMutation({
    mutationFn: () =>
      inventoryApi.createWarehouse({
        code: code.trim().toUpperCase(),
        name: name.trim(),
        is_default: isDefault,
      }),
    onSuccess: (saved) => {
      invalidate();
      toast.success(`${saved.name} added`);
      onClose();
    },
    onError: (error) => {
      if (error instanceof ApiError) {
        setFieldErrors(error.fieldErrors);
        toast.error(error.message);
      } else {
        toast.error('Could not add the warehouse');
      }
    },
  });

  const canSave = code.trim() !== '' && name.trim() !== '';

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New location"
      description="A shop, a godown, a van — anywhere stock physically sits."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={create.isPending}>
            Cancel
          </Button>
          <Button onClick={() => create.mutate()} disabled={!canSave || create.isPending}>
            {create.isPending ? 'Adding…' : 'Add location'}
          </Button>
        </>
      }
    >
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (canSave) create.mutate();
        }}
      >
        <div className="grid gap-3 sm:grid-cols-[8rem_1fr]">
          <Input
            label="Code"
            required
            autoFocus
            placeholder="MAIN"
            value={code}
            error={fieldErrors['code']}
            onChange={(event) => setCode(event.target.value.toUpperCase())}
          />
          <Input
            label="Name"
            required
            placeholder="Main shop"
            value={name}
            error={fieldErrors['name']}
            onChange={(event) => setName(event.target.value)}
          />
        </div>

        <label className="text-content-secondary flex items-center gap-2 text-[13px]">
          <input
            type="checkbox"
            checked={isDefault}
            onChange={(event) => setIsDefault(event.target.checked)}
            className="accent-primary"
          />
          Use this by default for new stock
        </label>

        <button type="submit" className="hidden" aria-hidden tabIndex={-1} />
      </form>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Stock adjustment
// ---------------------------------------------------------------------------
export function StockAdjustModal({
  open,
  onClose,
  product,
}: {
  open: boolean;
  onClose: () => void;
  product?: Product | undefined;
}) {
  const invalidate = useInvalidateInventory();
  const [productId, setProductId] = useState(product?.id ?? '');
  const [warehouseId, setWarehouseId] = useState('');
  const [direction, setDirection] = useState<'increase' | 'decrease'>('decrease');
  const [quantity, setQuantity] = useState('');
  const [reason, setReason] = useState('');

  const { data: products } = useQuery({
    queryKey: ['products', 'all'],
    queryFn: () => inventoryApi.products({ page_size: 200 }),
  });
  const { data: warehouses } = useQuery({
    queryKey: ['warehouses'],
    queryFn: () => inventoryApi.warehouses(),
  });

  const adjust = useMutation({
    mutationFn: () =>
      inventoryApi.adjust({
        product_id: productId,
        // The API takes a signed delta; the form asks a direction and a positive number,
        // because "-5" typed into a quantity box is easy to get backwards.
        quantity_delta: direction === 'decrease' ? `-${quantity}` : quantity,
        reason: reason.trim(),
        ...(warehouseId ? { warehouse_id: warehouseId } : {}),
      }),
    onSuccess: (movement) => {
      invalidate();
      toast.success('Stock adjusted', {
        // Formatted the way the tables format it, so the toast and the row it just
        // changed do not disagree about the same number.
        description: `${movement.product_name} — now ${formatMoney(movement.balance_after).replace('₹', '')} on hand`,
      });
      onClose();
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not adjust stock'),
  });

  const canSave = productId !== '' && Number(quantity) > 0 && reason.trim().length >= 3;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Adjust stock"
      description="For a stock take, breakage, or theft — anything that changes stock with no bill behind it."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={adjust.isPending}>
            Cancel
          </Button>
          <Button onClick={() => adjust.mutate()} disabled={!canSave || adjust.isPending}>
            {adjust.isPending ? 'Adjusting…' : 'Adjust stock'}
          </Button>
        </>
      }
    >
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (canSave) adjust.mutate();
        }}
      >
        <Select
          label="Product"
          required
          value={productId}
          onChange={(event) => setProductId(event.target.value)}
          placeholder="Choose…"
          options={(products?.items ?? []).map((item) => ({
            value: item.id,
            label: `${item.name} (${item.sku})`,
          }))}
        />

        <div className="grid gap-3 sm:grid-cols-2">
          <Select
            label="Location"
            value={warehouseId}
            onChange={(event) => setWarehouseId(event.target.value)}
            placeholder="Default"
            options={(warehouses ?? []).map((item) => ({ value: item.id, label: item.name }))}
          />
          <Select
            label="Direction"
            value={direction}
            onChange={(event) => setDirection(event.target.value as 'increase' | 'decrease')}
            options={[
              { value: 'decrease', label: 'Reduce stock' },
              { value: 'increase', label: 'Increase stock' },
            ]}
          />
        </div>

        <NumberInput
          label="Quantity"
          required
          placeholder="0"
          value={quantity}
          onValueChange={setQuantity}
          hint="A positive number. The direction above decides the sign."
        />

        <Input
          label="Reason"
          required
          placeholder="Stock take 29 July — two units damaged"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          hint="Required. This writes off value with no document behind it, so the reason is the only record of why."
        />

        <button type="submit" className="hidden" aria-hidden tabIndex={-1} />
      </form>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Transfer
// ---------------------------------------------------------------------------
export function StockTransferModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const invalidate = useInvalidateInventory();
  const [productId, setProductId] = useState('');
  const [fromId, setFromId] = useState('');
  const [toId, setToId] = useState('');
  const [quantity, setQuantity] = useState('');

  const { data: products } = useQuery({
    queryKey: ['products', 'all'],
    queryFn: () => inventoryApi.products({ page_size: 200 }),
  });
  const { data: warehouses } = useQuery({
    queryKey: ['warehouses'],
    queryFn: () => inventoryApi.warehouses(),
  });

  const transfer = useMutation({
    mutationFn: () =>
      inventoryApi.transfer({
        product_id: productId,
        from_warehouse_id: fromId,
        to_warehouse_id: toId,
        quantity,
      }),
    onSuccess: (movements) => {
      invalidate();
      toast.success('Stock transferred', {
        description: `${movements.length} movements recorded — no effect on total value.`,
      });
      onClose();
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not transfer stock'),
  });

  const sameLocation = fromId !== '' && fromId === toId;
  const canSave =
    productId !== '' && fromId !== '' && toId !== '' && !sameLocation && Number(quantity) > 0;

  const options = (warehouses ?? []).map((item) => ({ value: item.id, label: item.name }));

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Move stock"
      description="Between your own locations. The total value of your stock does not change."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={transfer.isPending}>
            Cancel
          </Button>
          <Button onClick={() => transfer.mutate()} disabled={!canSave || transfer.isPending}>
            {transfer.isPending ? 'Moving…' : 'Move stock'}
          </Button>
        </>
      }
    >
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (canSave) transfer.mutate();
        }}
      >
        <Select
          label="Product"
          required
          value={productId}
          onChange={(event) => setProductId(event.target.value)}
          placeholder="Choose…"
          options={(products?.items ?? []).map((item) => ({
            value: item.id,
            label: `${item.name} (${item.sku})`,
          }))}
        />

        <div className="grid gap-3 sm:grid-cols-2">
          <Select
            label="From"
            required
            value={fromId}
            onChange={(event) => setFromId(event.target.value)}
            placeholder="Choose…"
            options={options}
          />
          <Select
            label="To"
            required
            value={toId}
            onChange={(event) => setToId(event.target.value)}
            placeholder="Choose…"
            options={options}
            error={sameLocation ? 'Pick a different location' : undefined}
          />
        </div>

        <NumberInput
          label="Quantity"
          required
          placeholder="0"
          value={quantity}
          onValueChange={setQuantity}
        />

        <p className="text-content-muted text-[12px]">
          A transfer records two movements — out of one location, into the other — and no ledger
          entry, because nothing was bought, sold, or lost. Only the location changed.
        </p>

        <button type="submit" className="hidden" aria-hidden tabIndex={-1} />
      </form>
    </Modal>
  );
}
