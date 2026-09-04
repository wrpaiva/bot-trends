# ===========================================
# 🏗️ Stage 1: Builder
# ===========================================
FROM python:3.11-slim AS builder

WORKDIR /app

# Instalar poetry. Versões fixas de propósito: `poetry export` deixou de ser
# embutido no Poetry 2.x e virou plugin — sem ele este RUN falha.
RUN pip install --no-cache-dir poetry==2.4.2 poetry-plugin-export==1.10.0

# Copiar apenas arquivos de dependências (cache layer)
COPY pyproject.toml poetry.lock ./

# Exportar requirements.txt (mais leve que poetry em runtime)
RUN poetry export -f requirements.txt --output requirements.txt --without-hashes

# ===========================================
# 🚀 Stage 2: Production
# ===========================================
FROM python:3.11-slim AS production

WORKDIR /app

# Criar usuário não-root para segurança
RUN groupadd -r appgroup && useradd -r -g appgroup appuser

# Instalar dependências do sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copiar requirements e instalar
COPY --from=builder /app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código fonte
COPY . .

# Ajustar permissões
RUN chown -R appuser:appgroup /app

# Usar usuário não-root
USER appuser

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ===========================================
# 🔧 Stage 3: Development
# ===========================================
FROM python:3.11-slim AS development

WORKDIR /app

# Instalar poetry
RUN pip install --no-cache-dir poetry==2.4.2

COPY pyproject.toml poetry.lock ./

# Instalar com dev dependencies
RUN poetry config virtualenvs.create false && \
    poetry install --no-root

COPY . .

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

EXPOSE 8000

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
