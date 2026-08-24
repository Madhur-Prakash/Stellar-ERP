# =============================================================================
# Stellar ERP - frontend image
#
# `development` runs the Vite dev server. `production` builds static assets and
# serves them from Nginx: the built output is plain files, so there is no reason
# to ship Node into production.
# =============================================================================

# ---- Dependencies -----------------------------------------------------------
FROM node:24-alpine AS deps

WORKDIR /app

# Lockfile only, before the source, so this layer survives code changes.
COPY package.json package-lock.json ./

# `npm ci` installs exactly the lockfile - it fails if package.json and the
# lockfile disagree, rather than quietly resolving something else.
RUN --mount=type=cache,target=/root/.npm \
    npm ci

# ---- Development ------------------------------------------------------------
FROM deps AS development

COPY . .

ENV VITE_DOCKER=true

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

# ---- Build ------------------------------------------------------------------
FROM deps AS build

COPY . .

# VITE_* values are inlined into the bundle at build time, so they must be
# present now - supplying them at container start would have no effect.
ARG VITE_API_BASE_URL=http://localhost:8000
ARG VITE_API_V1_PREFIX=/api/v1
ARG VITE_APP_NAME="Stellar ERP"

# The chain the browser reads. These must match the backend's STELLAR_NETWORK,
# SOROBAN_CONTRACT_ID and SOROBAN_RPC_URL. The verifier's whole claim is that it
# checks the contract *without* asking this API, so they cannot be supplied at
# runtime - and a mismatch means the browser reads a different chain than the
# server writes to. Empty is a valid answer: the Trust screen then says sealing is
# not configured rather than guessing.
ARG VITE_STELLAR_NETWORK=testnet
ARG VITE_SOROBAN_CONTRACT_ID=""
ARG VITE_SOROBAN_RPC_URL=""

# Error tracking in the browser. Empty means nothing is reported anywhere.
ARG VITE_SENTRY_DSN=""

ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_API_V1_PREFIX=$VITE_API_V1_PREFIX \
    VITE_APP_NAME=$VITE_APP_NAME \
    VITE_STELLAR_NETWORK=$VITE_STELLAR_NETWORK \
    VITE_SOROBAN_CONTRACT_ID=$VITE_SOROBAN_CONTRACT_ID \
    VITE_SOROBAN_RPC_URL=$VITE_SOROBAN_RPC_URL \
    VITE_SENTRY_DSN=$VITE_SENTRY_DSN

# Runs `tsc -b && vite build`, so a type error fails the image build.
RUN npm run build

# ---- Production -------------------------------------------------------------
FROM nginx:1.27-alpine AS production

# Static assets only. No Node, no node_modules.
COPY --from=build /app/dist /usr/share/nginx/html

# And genuinely no source maps, rather than a comment saying so. The build emits
# them with `sourcemap: 'hidden'`, so nothing links to them and an error tracker
# can still be given them at release time; this deletes them from what is served.
# They were previously excluded by an edge rule that was deleted along with the
# edge, which is how they became fetchable - see "what this does not solve" in
# docs/security-audit.md.
RUN find /usr/share/nginx/html -name '*.map' -delete
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY security-headers.conf /etc/nginx/security-headers.conf

# nginx:alpine ships an unprivileged `nginx` user but runs as root to bind :80.
# Switching to port 8080 lets the process drop root entirely.
RUN sed -i 's/listen\s*80;/listen 8080;/' /etc/nginx/conf.d/default.conf \
    && chown -R nginx:nginx /usr/share/nginx/html /var/cache/nginx /var/log/nginx \
    && touch /var/run/nginx.pid && chown nginx:nginx /var/run/nginx.pid

USER nginx

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q --spider http://localhost:8080/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
