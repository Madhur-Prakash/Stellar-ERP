<div align="center">

# Personal ERP - web client

**React 19 · TypeScript · Vite 7 · Tailwind CSS v4.** The design system the desktop
client mirrors.

![React](https://img.shields.io/badge/React-19-61DAFB?style=flat-square&logo=react&logoColor=black)
![TypeScript](https://img.shields.io/badge/TypeScript-5.9-3178C6?style=flat-square&logo=typescript&logoColor=white)
![Vite](https://img.shields.io/badge/Vite-7-646CFF?style=flat-square&logo=vite&logoColor=white)
![Tailwind](https://img.shields.io/badge/Tailwind-v4-06B6D4?style=flat-square&logo=tailwindcss&logoColor=white)
![TanStack](https://img.shields.io/badge/TanStack-Router_Query_Table-FF4154?style=flat-square&logo=reactquery&logoColor=white)
![ESLint](https://img.shields.io/badge/ESLint-type--aware-4B32C3?style=flat-square&logo=eslint&logoColor=white)

[Architecture](../docs/architecture.md#frontend-architecture) · [API](../docs/api.md) · [Development](../docs/development.md#frontend-conventions) · [Security](../docs/security.md)

</div>

---

## Running it

The API has to be up first - this client has no server of its own.

```bash
make services   # from the repo root: PostgreSQL, Redis
make dev-api    # the API on :8000
npm run dev     # this, on :5173
```

Or `make up` for the whole stack in Docker.

Configuration is three `VITE_*` values read from the repo-root `.env` and
**validated with Zod at start-up** ([`lib/env.ts`](src/lib/env.ts)). A missing base
URL otherwise surfaces much later as a request to `null/api/v1/auth/login`.

> `VITE_*` values are inlined into the bundle at **build** time. Changing
> `PUBLIC_API_URL` on a deployed instance needs a rebuild, not a restart.

---

## Layout

```
src/
  components/ui/       Design-system primitives - no data fetching
  components/layout/   Shell, sidebar, command palette, theme toggle
  features/<name>/     api.ts + page components, colocated
  lib/                 HTTP client, env validation, formatting, locale
  routes/              TanStack Router tree, 404 and error boundaries
  styles/globals.css   Semantic design tokens, in oklch()
  types/api.ts         Hand-written mirrors of the backend contracts
```

One directory per screen under `features/`, mirroring `app_frontend/lib/features` in
the desktop client, so a change on one surface is easy to find on the other.

---

## Commands

```bash
npm run dev          # dev server → :5173
npm run build        # tsc -b && vite build
npm run typecheck    # tsc -b --noEmit
npm run lint         # eslint, --max-warnings 0
npm run format       # prettier
```

All four run in CI on every push - this is the one surface CI fully covers.

---

## The conventions that are not style preferences

Each of these exists because breaking it caused a real bug. The full list lives in
[Development](../docs/development.md#frontend-conventions).

| Rule | Why |
| --- | --- |
| **No token in `localStorage`** | The access token is held in memory and dies with the tab; the refresh token is an HttpOnly cookie JavaScript cannot reach. `localStorage` is readable by any XSS |
| **Refresh is single-flight** | When a token expires every in-flight request 401s at once. Independent refreshes would present the same already-rotated token, which the server correctly treats as a breach and answers by revoking the session |
| **Mutations never retry** | Configured globally. A retried `POST` can duplicate an invoice |
| **Semantic colour tokens only** | `bg-surface`, never `bg-zinc-900`. Dark mode is one set of variable overrides, and a literal colour silently breaks it |
| **A navigation control is a `<Link>`** | Use `buttonClasses()` on it. A `<button>` that navigates loses middle-click and "open in new tab"; a `<Link>` inside a `<button>` is invalid HTML |
| **Route guards read auth from router context** | Guards run in `beforeLoad`, before the component mounts. React context is unreachable there, and reading it inside the component renders the page first and redirects after - flashing content the user is not entitled to |

Type-aware ESLint is on, so `no-floating-promises` and `no-misused-promises` catch
the class of bug TypeScript alone misses. `void promise` is the explicit opt-out.

---

## The design tokens are the source of truth

[`styles/globals.css`](src/styles/globals.css) defines every colour as an `oklch()`
value against a semantic name (`--surface`, `--content-muted`). The Flutter client
reads **the same numbers** and converts them at runtime rather than keeping
hand-converted hex copies, because two independent palettes drift the first time
either is touched and a slightly wrong indigo is not something anyone catches in
review.

Dark mode is not an inversion: surfaces get *lighter* as they come forward, mirroring
how light actually behaves, and text contrast is stepped down because pure white on
near-black vibrates.
