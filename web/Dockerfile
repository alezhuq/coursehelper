# Build context is the repo root (see docker-compose.yml `web.build.context`)
# so this stage can copy the top-level nginx/ config into the final image.

# --- Build stage ---
FROM node:22-slim AS build

WORKDIR /app

COPY web/package.json web/package-lock.json* ./
RUN npm install

COPY web/. .
RUN npm run build

# --- Dev stage (hot reload; used only by docker-compose.override.yml) ---
FROM node:22-slim AS dev
WORKDIR /app
COPY web/package.json web/package-lock.json* ./
RUN npm install
EXPOSE 80
CMD ["npm", "run", "dev", "--", "--host", "--port", "80"]

# --- Serve stage ---
FROM nginx:1.27-alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

# 127.0.0.1, not localhost: some container network configs resolve
# "localhost" to ::1 first and nginx only binds the IPv4 wildcard.
HEALTHCHECK CMD wget -q --spider http://127.0.0.1/ || exit 1
