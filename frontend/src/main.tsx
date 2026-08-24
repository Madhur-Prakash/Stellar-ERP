import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { App } from '@/App';
import '@/styles/globals.css';

const container = document.getElementById('root');

if (!container) {
  // Fail loudly. A missing mount point means index.html and this file disagree,
  // and a silent no-op would present as a blank white page with no clue why.
  throw new Error('Root element #root not found in index.html');
}

createRoot(container).render(
  // StrictMode double-invokes effects in development to surface impure ones. It
  // is worth the noise: it is what catches a subscription that never cleans up.
  <StrictMode>
    <App />
  </StrictMode>,
);
