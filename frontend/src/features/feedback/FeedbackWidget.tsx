/**
 * The feedback button, and the panel behind it.
 *
 * Four decisions, each of which is about getting a message at all rather than
 * getting a well-formed one:
 *
 * - **Nothing is required except the message.** The rating is optional, the
 *   category defaults, and the email is only asked for when nobody is signed in.
 *   Every extra required field is a person who closes the panel instead.
 * - **The current screen is captured automatically.** "The numbers are wrong" means
 *   something different on the dashboard than on the trial balance, and asking
 *   costs a round trip the person will not make.
 * - **It works when signed out.** The endpoint is open, because the most useful
 *   report in any product comes from somebody who could not get in.
 * - **The panel says what happens next.** A thank-you that promises a reply the
 *   product cannot deliver is worse than one that says a person will read it.
 *
 * It is also **draggable and collapsible**, both persisted. That is not a flourish:
 * this button has no correct fixed home. Bottom-right belongs to the toaster, so a
 * button there sits under every success message. Bottom-left overlapped the sidebar's
 * own footer - the organization switcher and the signed-in address - at the same
 * z-index. Whichever corner is free depends on the screen, so the person looking at it
 * decides, and collapsing it to an icon gets it out of the way entirely.
 */
import { useMutation } from '@tanstack/react-query';
import { CheckCircle2, ChevronLeft, ChevronRight, MessageSquarePlus, X } from 'lucide-react';
import { useState } from 'react';
import { useRouterState } from '@tanstack/react-router';

import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { useAuth } from '@/features/auth/AuthProvider';
import { type FeedbackKind, feedbackApi, track } from '@/features/feedback/api';
import { useDraggable } from '@/features/feedback/useDraggable';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';

const KINDS: { value: FeedbackKind; label: string }[] = [
  { value: 'problem', label: 'Something is wrong' },
  { value: 'idea', label: 'I have an idea' },
  { value: 'question', label: 'I have a question' },
  { value: 'praise', label: 'This worked well' },
];

const RATINGS = [1, 2, 3, 4, 5] as const;

export function FeedbackWidget() {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<FeedbackKind>('problem');
  const [message, setMessage] = useState('');
  const [rating, setRating] = useState<number | null>(null);
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);

  // Defaults to the right edge, clear of the sidebar, and lifted above the toast
  // stack rather than sitting under it.
  const drag = useDraggable('stellarerp.feedback.position', () => ({
    x: window.innerWidth - 168,
    y: window.innerHeight - 148,
  }));
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem('stellarerp.feedback.collapsed') === '1';
    } catch {
      return false;
    }
  });

  const toggleCollapsed = () => {
    const next = !collapsed;
    setCollapsed(next);
    try {
      window.localStorage.setItem('stellarerp.feedback.collapsed', next ? '1' : '0');
    } catch {
      // Private window. The choice holds for this session.
    }
    // The pill changes width, so its clamp bounds change with it.
    requestAnimationFrame(drag.remeasure);
  };

  const auth = useAuth();
  const routerState = useRouterState();
  // The route the person was looking at when they pressed the button, not the one
  // they navigate to afterwards.
  const screen = routerState.location.pathname;

  const submit = useMutation({
    mutationFn: () =>
      feedbackApi.submit({
        kind,
        message: message.trim(),
        ...(rating !== null ? { rating } : {}),
        screen,
        ...(!auth.isAuthenticated && email.trim() ? { contact_email: email.trim() } : {}),
      }),
    onSuccess: () => {
      setSent(true);
      track('feedback.submitted', { outcome: 'ok' });
      setMessage('');
      setRating(null);
    },
  });

  const close = () => {
    setOpen(false);
    // Reset the acknowledgement, not the draft: somebody who closed the panel
    // mid-sentence and reopens it should find their words still there.
    setSent(false);
    submit.reset();
  };

  // Which way the panel opens, from where the button ended up. Both the button and the
  // panel live in one positioned wrapper, so the panel anchors to it with `top-full` /
  // `bottom-full` and needs no measuring of its own.
  const openUp = drag.position.y > window.innerHeight / 2;
  const openLeft = drag.position.x > window.innerWidth / 2;

  return (
    <div
      className="fixed z-40"
      style={{ left: drag.position.x, top: drag.position.y }}
    >
      <button
        ref={drag.ref as React.RefObject<HTMLButtonElement>}
        type="button"
        {...drag.handlers}
        onClick={() => {
          // A drag that ends over the button would otherwise open the panel.
          if (drag.consumeDrag()) return;
          setOpen((was) => !was);
        }}
        aria-expanded={open}
        aria-label="Send feedback"
        title="Send feedback - drag to move"
        className={cn(
          'bg-surface border-border text-content-secondary hover:text-content hover:border-border-strong',
          'focus-visible:ring-primary inline-flex items-center gap-2',
          'rounded-full border py-2.5 text-[13px] font-medium shadow-lg',
          collapsed ? 'px-3' : 'pr-2 pl-3',
          // `transition-colors` only. Transitioning position would make the button
          // lag the pointer during a drag, which reads as lag rather than as easing.
          'transition-colors focus-visible:ring-2 focus-visible:outline-none',
          drag.dragging ? 'cursor-grabbing select-none' : 'cursor-grab',
        )}
      >
        <MessageSquarePlus className="size-4 shrink-0" />
        {!collapsed && <span>Feedback</span>}

        <span
          role="button"
          tabIndex={0}
          aria-label={collapsed ? 'Show the label' : 'Collapse to an icon'}
          // Stops the parent treating this as the start of a drag.
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => {
            event.stopPropagation();
            toggleCollapsed();
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault();
              event.stopPropagation();
              toggleCollapsed();
            }
          }}
          className={cn(
            'text-content-muted hover:text-content rounded-full p-0.5',
            'focus-visible:ring-primary focus-visible:ring-2 focus-visible:outline-none',
          )}
        >
          {collapsed ? <ChevronRight className="size-3.5" /> : <ChevronLeft className="size-3.5" />}
        </span>
      </button>

      {open && (
        <div
          role="dialog"
          aria-modal="false"
          aria-label="Send feedback"
          className={cn(
            'bg-surface border-border absolute z-50 rounded-xl border shadow-2xl',
            // Opens toward the middle of the viewport, so a button parked in a corner
            // does not push its own panel off-screen.
            openUp ? 'bottom-full mb-2' : 'top-full mt-2',
            openLeft ? 'right-0' : 'left-0',
            // Never wider than the viewport on a phone, and never taller than it.
            // `dvh` rather than `vh` so the mobile keyboard shrinking the viewport
            // scrolls the panel instead of pushing the Send button off-screen.
            'max-h-[calc(100dvh-6rem)] w-[calc(100vw-2rem)] max-w-sm overflow-y-auto',
          )}
        >
          <div className="border-border flex items-center justify-between border-b px-4 py-3">
            <p className="text-content text-sm font-semibold">
              {sent ? 'Thank you' : 'Tell us what happened'}
            </p>
            <button
              type="button"
              onClick={close}
              aria-label="Close"
              className="text-content-muted hover:text-content rounded p-1"
            >
              <X className="size-4" />
            </button>
          </div>

          {sent ? (
            <div className="space-y-3 px-4 py-4">
              <p className="text-content-secondary flex items-start gap-2 text-[13px] leading-relaxed">
                <CheckCircle2 className="text-success mt-0.5 size-4 shrink-0" />
                It has been recorded and a person will read it. We cannot promise a reply to every
                message, but every one is read.
              </p>
              <Button variant="outline" fullWidth onClick={close}>
                Close
              </Button>
            </div>
          ) : (
            <form
              className="space-y-3 px-4 py-4"
              onSubmit={(event) => {
                event.preventDefault();
                submit.mutate();
              }}
            >
              <Select
                label="What is this about?"
                value={kind}
                options={KINDS}
                onChange={(event) => setKind(event.target.value as FeedbackKind)}
              />

              <div>
                <label
                  htmlFor="feedback-message"
                  className="text-content-secondary mb-1.5 block text-[13px] font-medium"
                >
                  What happened?
                </label>
                <textarea
                  id="feedback-message"
                  value={message}
                  onChange={(event) => setMessage(event.target.value)}
                  rows={4}
                  maxLength={4000}
                  required
                  placeholder="As much or as little as you like. What you were doing helps most."
                  className="border-border bg-surface text-content placeholder:text-content-muted focus:border-primary focus:ring-primary/20 w-full resize-y rounded-md border px-3 py-2 text-[13px] focus:ring-2 focus:outline-none"
                />
                <p className="text-content-muted mt-1 text-[11px]">
                  Sent with the screen you are on ({screen}) so we know where to look.
                </p>
              </div>

              <div>
                <span className="text-content-secondary mb-1.5 block text-[13px] font-medium">
                  How is it going overall?{' '}
                  <span className="text-content-muted font-normal">(optional)</span>
                </span>
                <div className="flex gap-1.5">
                  {RATINGS.map((value) => (
                    <button
                      key={value}
                      type="button"
                      aria-pressed={rating === value}
                      onClick={() => setRating(rating === value ? null : value)}
                      className={cn(
                        'h-9 flex-1 rounded-md border text-[13px] font-medium transition-colors',
                        rating === value
                          ? 'border-primary bg-primary text-primary-content'
                          : 'border-border text-content-secondary hover:border-border-strong',
                      )}
                    >
                      {value}
                    </button>
                  ))}
                </div>
              </div>

              {/* Only asked when there is no account to reply to. */}
              {!auth.isAuthenticated && (
                <Input
                  label="Your email"
                  hint="Only so we can reply. Leave it blank to stay anonymous."
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="you@example.com"
                />
              )}

              {submit.isError && (
                <p className="text-danger text-[12px]">
                  {submit.error instanceof ApiError
                    ? submit.error.message
                    : 'That could not be sent. Try again in a moment.'}
                </p>
              )}

              <Button
                type="submit"
                fullWidth
                loading={submit.isPending}
                disabled={message.trim().length < 3}
              >
                Send
              </Button>
            </form>
          )}
        </div>
      )}
    </div>
  );
}
