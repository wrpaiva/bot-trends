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

## Armadilhas conhecidas (verificado em 2026-09-04)

Não são "coisas a arrumar agora", são coisas que vão te morder se você não souber:

1. **A API do Mercado Livre não é mais pública.** `/highlights` devolve 401
   `unspecified_token`, `/sites/MLB/search` devolve 403 e `/items` devolve 401. O
   `MercadoLivreCollector` não tem nenhum suporte a OAuth — só lê `ML_BASE_URL` e `ML_SITE_ID`.
   **Nenhuma coleta do ML funciona hoje**, e a Fase 2 inteira depende disso (TIE-41).
2. **O seed de categorias e o `collect_ml` discordam do schema.** `apps/bootstrap/main.py`
   grava categorias com `enabled: False` e sem `ml_category_id`; `collect_ml` filtra por
   `{"enabled": True, "ml_category_id": {"$exists": True}}`. Recém-instalado, o sistema
   devolve `{"status": "no enabled categories"}` para sempre (TIE-20).
3. **`collect_ml` devolve `status: ok` mesmo quando toda requisição falhou.** O erro é logado
   e engolido; o retorno é `{"status": "ok", "inserted": 0}`. Falha silenciosa (TIE-17).
4. **`get_db()` abre um `MongoClient` novo a cada chamada** (`infrastructure/db/mongo.py`) —
   por request e por task. Não é pool reaproveitado (TIE-7).
5. **`rank_momentum` e `reviews_velocity` estão hardcoded em `0.0`** em `tasks_trend.py`,
   ou seja 35% do score numérico é sempre zero (TIE-16).
6. **README documenta `GET /search`, que não existe** em `routes.py` (TIE-30/TIE-33).
7. `datetime.utcnow()` é usado em todo lugar (deprecado no 3.12; o projeto fixa 3.11).
   Existe `src/infrastructure/utils/datetime_utils.py` subutilizado (TIE-10).
8. **`MONGO_PASSWORD` só vale na primeira subida do volume.** `MONGO_INITDB_ROOT_PASSWORD` é
   lido apenas quando `/data/db` está vazio. Trocar a senha no `.env` com o volume
   `trends_mongo_data` já existente dá `storedKey mismatch` e o healthcheck nunca fica verde.
   Para valer: `docker compose down && docker volume rm trends_mongo_data`.
9. **`http://localhost:80` não é uma origem válida.** Na porta 80 o browser envia
   `http://localhost`, sem a porta. Com `:80` explícito em `CORS_ORIGINS` o preflight falha.
10. **Os containers `beat` e `worker` aparecem como `unhealthy` e isso é falso.** Ambos herdam
    o `HEALTHCHECK` do Dockerfile (`curl localhost:8000/health`), mas nenhum dos dois serve
    HTTP — só a API serve. Cosmético, mas polui o `docker compose ps` (TIE-38).

## Pendências de higiene do repositório

- Arquivos mortos: `src/infrastructure/db/migrations/versions/runner.py` (vazio, duplica o runner
  real em `migrations/runner.py`), `backend/docker-compose.yml` e `frontend/docker-compose.yml`
  (o compose da raiz é o oficial).
- `ruff check .` acusa ~138 erros pré-existentes no backend (a maioria `I001` e `W292`).
  Não é regressão; só nunca foi rodado. Ao mexer num arquivo, deixe-o limpo.

## Já corrigido (não reintroduzir)

- `frontend/Dockerfile` tinha um diagrama ASCII colado depois do `CMD` — quebrava o build.
- `index.html` estava em `frontend/src/`; o Vite exige na raiz de `frontend/`.
- Worker do compose não tinha `-Q`, então nenhuma task roteada para `ml`/`tiktok`/`trend` rodava.
- `.gitignore` criado e `backend/.env` removido do índice do git.
- `API_KEY`/`CORS_ORIGINS` não chegavam ao container da API.
- **`db/__init__.py` continha o código de `db/migrations/__init__.py`** e importava
  `.versions.*` de um diretório que não existia. Derrubava API, worker e migrations no mesmo
  `ModuleNotFoundError`. Coberto agora por `src/tests/test_imports_smoke.py` — não apague
  esse teste, ele é a única coisa que impede a regressão de voltar calada (TIE-40).
- **Frontend não enviava `X-API-Key`.** `api.js` agora manda o header a partir de
  `import.meta.env.VITE_API_KEY`; o compose injeta `${API_KEY}` como `VITE_API_KEY` no build
  do `web`. Fonte única de propósito: duas variáveis que precisam ser iguais divergem, e o
  sintoma é 401 (TIE-1).
- **`LLM_MODEL` vs `LLM_MODEL_NAME`.** `openai_compatible.py` lê de `settings` (não mais de
  `os.environ`), o default mora só em `config.py`, e o modelo efetivo vai para o log no
  startup do cliente (TIE-2).
- **`testpaths` apontava para `tests`**, que não existe → `pytest` saía verde sem coletar nada.
  Agora é `src/tests`. **Decisão: os testes ficam em `src/tests/`**, não migram para
  `backend/tests/` (TIE-4).
- **`poetry.lock` commitado** e o `poetry export` do Dockerfile consertado: no Poetry 2.x ele
  virou plugin, então o builder instala `poetry==2.4.2` + `poetry-plugin-export==1.10.0`,
  ambos com versão fixa (TIE-6).
- **Mongo e Redis não são mais publicados em `0.0.0.0`** — só em `127.0.0.1`, e as portas do
  host viraram configuráveis (`MONGO_HOST_PORT`, `REDIS_HOST_PORT`, `API_HOST_PORT`,
  `WEB_HOST_PORT`, `WEB_DEV_HOST_PORT`) para conviver com outros projetos na mesma máquina.

---

## Fluxo de trabalho

1. Antes de mudar comportamento, leia o teste correspondente em `src/tests/`.
2. Mudou lógica de domínio? Teste primeiro, implementação depois.
3. Rode `pytest src/tests -q` e `ruff check .` antes de dizer que terminou.
4. Descobriu uma armadilha nova ou corrigiu uma da lista acima? **Atualize este arquivo.**
5. Não faça commit nem push sem eu pedir.
