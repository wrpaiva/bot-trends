# A chave da API NÃO entra no build (TIE-27): o bundle chama /api na mesma
# origem e o nginx põe a X-API-Key em runtime (nginx/default.conf.template).

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
RUN npm run build

# ===========================================
# 🚀 Stage 2: Production (nginx)
# ===========================================
FROM nginx:alpine AS production

# Configuração nginx otimizada para SPA
COPY --from=builder /app/dist /usr/share/nginx/html

# Proxy /api → api:8000 com a X-API-Key do ambiente (envsubst na subida)
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
ENV NGINX_ENVSUBST_FILTER=^API_KEY$

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
