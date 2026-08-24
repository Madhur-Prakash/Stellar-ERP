import { Link } from '@tanstack/react-router';

import { buttonClasses } from '@/components/ui/Button';

export function NotFoundPage() {
  return (
    <div className="bg-canvas flex min-h-dvh flex-col items-center justify-center px-6 text-center">
      <p className="text-content-muted font-mono text-[13px] font-medium">404</p>
      <h1 className="text-content mt-2 text-2xl font-semibold tracking-[-0.03em]">
        Page not found
      </h1>
      <p className="text-content-muted mt-2 max-w-sm text-[13px] leading-relaxed">
        The page you are looking for does not exist, or you may not have access to it.
      </p>
      <Link to="/" className={buttonClasses('primary', 'md', 'mt-6')}>
        Back to dashboard
      </Link>
    </div>
  );
}
