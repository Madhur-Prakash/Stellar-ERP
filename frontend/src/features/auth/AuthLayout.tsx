import { Link } from '@tanstack/react-router';
import type { ReactNode } from 'react';

import { ThemeToggle } from '@/components/layout/ThemeToggle';

/**
 * Shell for the unauthenticated screens.
 *
 * A single centred column rather than the usual split-screen marketing panel:
 * these pages exist to get someone through a form, and a decorative half-screen
 * only pushes the fields around on smaller laptops.
 */
export function AuthLayout({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="bg-canvas relative flex min-h-dvh flex-col">
      {/* A very soft radial wash. Enough to stop a large empty viewport reading
          as unstyled, subtle enough not to compete with the form. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 overflow-hidden"
        style={{
          background:
            'radial-gradient(60rem 32rem at 50% -12rem, color-mix(in oklch, var(--primary) 14%, transparent), transparent 70%)',
        }}
      />

      <header className="relative flex items-center justify-between px-6 py-5">
        <Link to="/login" className="flex items-center gap-2">
          <span
            className="bg-primary text-primary-content flex h-7 w-7 items-center justify-center rounded-lg text-sm font-bold"
            aria-hidden
          >
            E
          </span>
          <span className="text-content text-[15px] font-semibold tracking-tight">
            Personal ERP
          </span>
        </Link>
        <ThemeToggle />
      </header>

      <main className="relative flex flex-1 items-center justify-center px-6 pb-16">
        <div className="animate-slide-up w-full max-w-[400px]">
          <div className="mb-7 text-center">
            <h1 className="text-content text-[26px] leading-tight font-semibold tracking-[-0.03em]">
              {title}
            </h1>
            {subtitle && (
              <p className="text-content-muted mt-2 text-sm leading-relaxed">{subtitle}</p>
            )}
          </div>

          <div className="bg-surface border-border rounded-2xl border p-6 shadow-lg">
            {children}
          </div>

          {footer && <div className="mt-6 text-center text-[13px]">{footer}</div>}
        </div>
      </main>
    </div>
  );
}
