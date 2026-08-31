/**
 * A pointer-dragged, viewport-clamped, persisted position for one floating element.
 *
 * Written for the feedback button, which has to sit somewhere and has no good
 * default: bottom-right is the toaster's, bottom-left is the sidebar's own footer,
 * and which corner is free depends on the screen. Letting the person move it is
 * cheaper than guessing, and the guess was already wrong twice.
 *
 * Five things this handles that a naive `onMouseMove` does not:
 *
 * - **Clamped on every write, and again on resize.** An element dragged to the right
 *   edge of a wide window is off-screen on a narrow one, and a control you cannot
 *   reach is worse than one in the wrong place. Position is stored in pixels and
 *   re-clamped rather than stored as a fraction, so it stays where it was put on the
 *   size it was put there.
 * - **A drag is not a click.** Without a movement threshold every drag ends by firing
 *   the button. `movedRef` is read once by the click handler and cleared.
 * - **Pointer capture, not document listeners.** `setPointerCapture` keeps the drag
 *   alive when the pointer outruns the element, which it always does, and releases
 *   cleanly if the gesture is cancelled.
 * - **Storage that cannot throw.** `localStorage` raises in a private window and in
 *   some embedded webviews, so every access is guarded and a failure degrades to the
 *   default position rather than taking the widget out.
 * - **No ref reads during render, and no `setState` in an effect.** The initial
 *   position is resolved in the `useState` initialiser against an *estimated* size
 *   rather than a measured one - measuring needs the DOM, which needs an effect, and
 *   a `setState` there is a cascading render. The estimate only has to be close
 *   enough to keep the button on screen; the first drag or resize clamps it exactly.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

export interface Point {
  x: number;
  y: number;
}

/** Pixels a pointer must travel before the gesture counts as a drag, not a press. */
const DRAG_THRESHOLD = 4;

/** Keeps the element off the very edge, and clear of a rounded viewport corner. */
const MARGIN = 12;

/** Used before the element has been measured - see the note on render-time reads. */
const ESTIMATED = { width: 150, height: 42 };

interface Size {
  width: number;
  height: number;
}

function clamp(point: Point, size: Size): Point {
  const maxX = Math.max(MARGIN, window.innerWidth - size.width - MARGIN);
  const maxY = Math.max(MARGIN, window.innerHeight - size.height - MARGIN);
  return {
    x: Math.min(Math.max(point.x, MARGIN), maxX),
    y: Math.min(Math.max(point.y, MARGIN), maxY),
  };
}

function read(key: string): Point | null {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (
      typeof parsed === 'object' &&
      parsed !== null &&
      typeof (parsed as Partial<Point>).x === 'number' &&
      typeof (parsed as Partial<Point>).y === 'number'
    ) {
      return { x: (parsed as Point).x, y: (parsed as Point).y };
    }
    return null;
  } catch {
    return null;
  }
}

function write(key: string, point: Point): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(point));
  } catch {
    // A private window refuses to store. The position still works for this session.
  }
}

export interface Draggable {
  /** Attach to the element being positioned, for size-aware clamping. */
  ref: React.RefObject<HTMLElement | null>;
  position: Point;
  dragging: boolean;
  /** Spread onto the drag handle. */
  handlers: {
    onPointerDown: (event: React.PointerEvent) => void;
    onPointerMove: (event: React.PointerEvent) => void;
    onPointerUp: (event: React.PointerEvent) => void;
    onPointerCancel: (event: React.PointerEvent) => void;
  };
  /**
   * True when the gesture that just ended was a drag. Reading it clears the flag, so
   * a click handler can call it once to decide whether to act.
   */
  consumeDrag: () => boolean;
  /** Re-clamp after the element changes size - collapsing shrinks it. */
  remeasure: () => void;
}

export function useDraggable(storageKey: string, fallback: () => Point): Draggable {
  const ref = useRef<HTMLElement | null>(null);

  const [position, setPosition] = useState<Point>(() =>
    clamp(read(storageKey) ?? fallback(), ESTIMATED),
  );

  const [dragging, setDragging] = useState(false);

  // The live position, so the pointer handlers can clamp and persist without reading
  // state that may be a render behind.
  const latest = useRef<Point>(position);
  const grabOffset = useRef<Point>({ x: 0, y: 0 });
  const startRef = useRef<Point>({ x: 0, y: 0 });
  const movedRef = useRef(false);

  const apply = useCallback((next: Point) => {
    latest.current = next;
    setPosition(next);
  }, []);

  const measure = useCallback((): Size => {
    const rect = ref.current?.getBoundingClientRect();
    return rect ? { width: rect.width, height: rect.height } : ESTIMATED;
  }, []);

  const remeasure = useCallback(() => {
    apply(clamp(latest.current, measure()));
  }, [apply, measure]);

  useEffect(() => {
    window.addEventListener('resize', remeasure);
    return () => window.removeEventListener('resize', remeasure);
  }, [remeasure]);

  const onPointerDown = useCallback((event: React.PointerEvent) => {
    // Left button, touch or pen only - a right-click is a context menu, not a grab.
    if (event.button !== 0) return;
    const rect = event.currentTarget.getBoundingClientRect();
    grabOffset.current = { x: event.clientX - rect.left, y: event.clientY - rect.top };
    startRef.current = { x: event.clientX, y: event.clientY };
    movedRef.current = false;
    setDragging(true);
    event.currentTarget.setPointerCapture(event.pointerId);
  }, []);

  const onPointerMove = useCallback(
    (event: React.PointerEvent) => {
      if (!dragging) return;
      const travelled =
        Math.abs(event.clientX - startRef.current.x) + Math.abs(event.clientY - startRef.current.y);
      if (travelled > DRAG_THRESHOLD) movedRef.current = true;

      const rect = event.currentTarget.getBoundingClientRect();
      apply(
        clamp(
          { x: event.clientX - grabOffset.current.x, y: event.clientY - grabOffset.current.y },
          { width: rect.width, height: rect.height },
        ),
      );
    },
    [apply, dragging],
  );

  const end = useCallback(
    (event: React.PointerEvent) => {
      if (!dragging) return;
      setDragging(false);
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
      write(storageKey, latest.current);
    },
    [dragging, storageKey],
  );

  const consumeDrag = useCallback(() => {
    const was = movedRef.current;
    movedRef.current = false;
    return was;
  }, []);

  return {
    ref,
    position,
    dragging,
    handlers: { onPointerDown, onPointerMove, onPointerUp: end, onPointerCancel: end },
    consumeDrag,
    remeasure,
  };
}
