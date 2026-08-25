# ===========================================
# 🏗️ Stage 1: Build
# ===========================================
FROM node:20-alpine AS builder

WORKDIR /app

# Copiar apenas arquivos de dependências primeiro (cache layer)
COPY package.json package-lock.json* yarn.lock* pnpm-lock.yaml* ./

# Instalar dependências
RUN npm ci --silent || npm install --silent

# Copiar código fonte
COPY . .

# Build de produção
ARG VITE_API_BASE=http://localhost:8000
ENV VITE_API_BASE=$VITE_API_BASE

RUN npm run build

# ===========================================
# 🚀 Stage 2: Production (nginx)
# ===========================================
FROM nginx:alpine AS production

# Configuração nginx otimizada para SPA
COPY --from=builder /app/dist /usr/share/nginx/html

# Configuração customizada do nginx
RUN echo 'server { \
    listen 80; \
    listen [::]:80; \
    server_name _; \
    root /usr/share/nginx/html; \
    index index.html; \
    \
    # Gzip \
    gzip on; \
    gzip_vary on; \
    gzip_min_length 1024; \
    gzip_types text/plain text/css text/xml text/javascript application/javascript application/json application/xml; \
    \
    # Cache para assets estáticos \
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2)$ { \
        expires 1y; \
        add_header Cache-Control "public, immutable"; \
    } \
    \
    # SPA fallback \
    location / { \
        try_files $uri $uri/ /index.html; \
    } \
    \
    # Health check \
    location /health { \
        access_log off; \
        return 200 "ok"; \
        add_header Content-Type text/plain; \
    } \
}' > /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost/health || exit 1

CMD ["nginx", "-g", "daemon off;"]

# ===========================================
# 🔧 Stage 3: Development
# ===========================================
FROM node:20-alpine AS development

WORKDIR /app

COPY package.json package-lock.json* yarn.lock* pnpm-lock.yaml* ./
RUN npm ci --silent || npm install --silent

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev"]
