# Trends Intelligence Engine — Instruções para o Agente

Motor híbrido de detecção de tendências: coleta Mercado Livre + TikTok, calcula score
matemático + LLM, expõe rankings via API REST e alerta no Telegram.

Responda em **português**. Comentários e docstrings do código também em português.

---

## Estrutura do repositório

```
bot-trends/
├── backend/              # Python 3.11 · FastAPI · Celery · MongoDB · Poetry
│   ├── apps/             # Entrypoints (executáveis)
│   │   ├── api/          # FastAPI: main.py (app+middlewares) · routes.py (endpoints)
│   │   ├── worker/       # Celery: main.py (app+beat) · tasks.py (coleta) · tasks_trend.py (análise)
│   │   ├── migrate/      # Runner de migrações Mongo
│   │   ├── bootstrap/    # Seed de categorias
│   │   └── tools/        # Scripts utilitários avulsos
│   └── src/              # Core (Clean Architecture)
│       ├── domain/       # Entidades + regras puras. SEM I/O, SEM libs externas.
│       ├── application/  # Casos de uso (HybridTrendEngine, prompt_builder)
│       ├── infrastructure/  # Mongo, LLM, collectors, telegram, security
│       └── tests/        # pytest
├── frontend/             # React 18 · Vite 5 · chart.js (dashboard)
└── docker-compose.yml    # Orquestração raiz (mongo, redis, api, worker, beat, web)
```

**Regra de dependência (não violar):** `domain` ← `application` ← `infrastructure`/`apps`.
`domain/` nunca importa pymongo, httpx, celery ou fastapi. Se precisar de I/O no domínio,
declare a interface em `src/domain/interfaces.py` e implemente em `infrastructure/`.

---

## Comandos

Rodar tudo:
```bash
docker compose up -d --build              # produção
docker compose --profile dev up           # com frontend em hot-reload (:5173)
docker compose logs -f worker
```

Setup inicial do banco (nesta ordem):
```bash
docker compose run --rm api python -m apps.migrate.main      # indexes + collections
docker compose run --rm api python -m apps.bootstrap.main    # categorias
curl http://localhost:8000/health
```

Backend local:
```bash
cd backend
poetry install && poetry shell
pytest src/tests -q          # NOTA: use o caminho explícito, ver "Armadilhas"
ruff check . && black .
```

Rodar uma análise manual:
```bash
docker compose run --rm api python -c \
  "from apps.worker.tasks_trend import hybrid_trend_analyze; hybrid_trend_analyze(hours=24, limit_products=5)"
```

---

## Configuração (atenção: são DOIS arquivos .env)

| Arquivo | Consumido por | Contém |
|---|---|---|
| `.env` (raiz) | `docker-compose.yml` (interpolação) | `MONGO_USER/PASSWORD`, `REDIS_PASSWORD`, `API_KEY`, `CORS_ORIGINS`, `VITE_API_BASE` |
| `backend/.env` | `env_file:` dos serviços api/worker/beat | credenciais de app: `LLM_*`, `APIFY_TOKEN`, `TELEGRAM_*`, `ML_*` |

O compose **sobrescreve** `MONGO_URI`, `REDIS_URL`, `API_KEY` e `CORS_ORIGINS` do `backend/.env`
via bloco `environment:` do serviço `api`. Não confie no valor que está no arquivo — o efetivo
vem do compose. `API_KEY` usa `${API_KEY:?}`: sem ela no `.env` da raiz, o compose se recusa a
subir (falha rápido em vez de devolver 500 em runtime).

**Leitura de config no código é inconsistente hoje:** existe `src/infrastructure/config.py`
(pydantic `Settings`), mas quase todo o código lê `os.environ` direto. Ao mexer em config,
prefira migrar para `settings`, e mantenha o nome da variável idêntico nos dois lugares.

**Nunca** commitar segredos. Ambos os `.env` estão no `.gitignore`; os `.env.example` só recebem
placeholders.

---

## Convenções ao adicionar código

**Nova task Celery:** decore com `@shared_task(name="tasks.<nome>")`, registre a rota de fila
em `apps/worker/main.py` (`task_routes`) **e** garanta que o worker consome essa fila
(`-Q` no comando do compose). Sem isso a task é publicada e nunca executa.

**Nova migração:** crie `src/infrastructure/db/migrations/versions/vNNN_<descricao>.py` seguindo
`migration_base.Migration`, registre em `versions/__init__.py`. Migrações são idempotentes e
protegidas por lock distribuído — nunca edite uma migração já aplicada, crie a próxima.

**Novo collector:** implemente em `src/infrastructure/collectors/`, use context manager
(`with Collector(...) as c`) e devolva dicts normalizados para `ProductRepo.upsert`.

**Novo endpoint:** vá em `apps/api/routes.py`. Tudo que está nesse router já exige `X-API-Key`
(injetado no `include_router`). Rotas públicas ficam em `apps/api/main.py`. Para rate limit
específico use `@limiter.limit(...)` — e nesse caso o handler **precisa** receber `request: Request`.

**Scoring:** os pesos vivem em `src/domain/scoring.py`. A fórmula final é
`0.6 * numeric + 0.4 * llm`, com fallback automático se o LLM falhar. Alterou peso? Atualize
`src/tests/test_scoring.py` no mesmo commit.

---

## Armadilhas conhecidas (verificado em 2026-08-25)

Não são "coisas a arrumar agora", são coisas que vão te morder se você não souber:

1. **Frontend não envia `X-API-Key`.** `frontend/src/api.js` faz `fetch` sem header, e todas as
   rotas do router exigem a chave → dashboard toma 401. É o próximo bug a matar.
2. **`LLM_MODEL` vs `LLM_MODEL_NAME`.** O código lê `LLM_MODEL`; o `backend/.env` define
   `LLM_MODEL_NAME`. O modelo configurado é silenciosamente ignorado (cai no default).
3. **`pytest` sozinho não acha os testes**: `testpaths = ["tests"]` no pyproject, mas os testes
   estão em `src/tests/`. Use `pytest src/tests`.
4. **`get_db()` abre um `MongoClient` novo a cada chamada** (`infrastructure/db/mongo.py`) —
   por request e por task. Não é pool reaproveitado.
5. **`rank_momentum` e `reviews_velocity` estão hardcoded em `0.0`** em `tasks_trend.py`,
   ou seja 35% do score numérico é sempre zero.
6. **README documenta `GET /search`, que não existe** em `routes.py`.
7. `datetime.utcnow()` é usado em todo lugar (deprecado no 3.12; o projeto fixa 3.11).
   Existe `src/infrastructure/utils/datetime_utils.py` subutilizado.

## Pendências de higiene do repositório

- Não existe `poetry.lock` commitado → builds não reprodutíveis, e o `poetry export` do
  Dockerfile depende de plugin no Poetry 2.x.
- Arquivos mortos: `src/infrastructure/db/migrations/versions/runner.py` (vazio, duplica o runner
  real em `migrations/runner.py`), `backend/docker-compose.yml` e `frontend/docker-compose.yml`
  (o compose da raiz é o oficial).

## Já corrigido (não reintroduzir)

- `frontend/Dockerfile` tinha um diagrama ASCII colado depois do `CMD` — quebrava o build.
- `index.html` estava em `frontend/src/`; o Vite exige na raiz de `frontend/`.
- Worker do compose não tinha `-Q`, então nenhuma task roteada para `ml`/`tiktok`/`trend` rodava.
- `.gitignore` criado e `backend/.env` removido do índice do git.
- `API_KEY`/`CORS_ORIGINS` não chegavam ao container da API.

---

## Fluxo de trabalho

1. Antes de mudar comportamento, leia o teste correspondente em `src/tests/`.
2. Mudou lógica de domínio? Teste primeiro, implementação depois.
3. Rode `pytest src/tests -q` e `ruff check .` antes de dizer que terminou.
4. Descobriu uma armadilha nova ou corrigiu uma da lista acima? **Atualize este arquivo.**
5. Não faça commit nem push sem eu pedir.
