/**
 * Inventory mutations that are not tied to one form.
 *
 * In their own module because a file that exports both components and plain functions
 * breaks React fast refresh - the whole module reloads instead of the component, losing
 * form state on every save during development.
 */
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';

import { type Product, inventoryApi } from '@/features/inventory/api';
import { ApiError } from '@/lib/api';

/**
 * Every cache a stock or product change can invalidate.
 *
 * Listed exhaustively rather than clearing everything: stock is an asset, so a movement
 * changes the balance sheet, the dashboard, and the control-account reconciliation - and
 * a screen still showing the old stock value after an adjustment reads as a bug in the
 * arithmetic rather than a stale cache.
 */
export function useInvalidateInventory() {
  const queryClient = useQueryClient();

  return () => {
    for (const key of [
      'products',
      'stock-levels',
      'stock-valuation',
      'stock-movements',
      'warehouses',
      'reorder',
      'accounts',
      'trial-balance',
      'analytics-dashboard',
      'analytics-control-checks',
    ]) {
      void queryClient.invalidateQueries({ queryKey: [key] });
    }
  };
}

/**
 * Archive or restore a product.
 *
 * The nearest thing to deletion that is safe here. A product named on a posted bill or a
 * stock movement cannot be removed - the entry would point at nothing - so it is hidden
 * from every picker instead, and the action is fully reversible.
 */
export function useArchiveProduct() {
  const invalidate = useInvalidateInventory();

  return useMutation({
    mutationFn: ({ product, archive }: { product: Product; archive: boolean }) =>
      inventoryApi.updateProduct(product.id, { is_active: !archive }),
    onSuccess: (saved) => {
      invalidate();
      toast.success(
        saved.is_active ? `${saved.name} restored` : `${saved.name} archived`,
        saved.is_active
          ? undefined
          : { description: 'Hidden from pickers. Its history and stock are untouched.' },
      );
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not update the product'),
  });
}
