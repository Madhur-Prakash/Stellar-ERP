# =============================================================================
# Personal ERP - frontend image
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
ARG VITE_APP_NAME="Personal ERP"

ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_API_V1_PREFIX=$VITE_API_V1_PREFIX \
    VITE_APP_NAME=$VITE_APP_NAME

# Runs `tsc -b && vite build`, so a type error fails the image build.
RUN npm run build

# ---- Production -------------------------------------------------------------
FROM nginx:1.27-alpine AS production

# Static assets only. No Node, no node_modules, no source maps served publicly.
COPY --from=build /app/dist /usr/share/nginx/html
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
