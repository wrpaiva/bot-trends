# Backend — Trends Intelligence Engine

Python 3.11 · FastAPI · Celery · MongoDB · Poetry. Visão geral, setup com Docker e referência
da API ficam no [README da raiz](../README.md) — este arquivo cobre só o desenvolvimento.

## Rodando os testes

Com Poetry:

```bash
poetry install
poetry run pytest src/tests                    # caminho explícito
poetry run pytest src/tests --cov=src --cov=apps
poetry run ruff check . && poetry run black --check .
```

Sem Python 3.11/Poetry na máquina, pela imagem de dev (a partir da raiz do repositório):

```bash
docker build --target development -t trends-dev backend
docker run --rm --user $(id -u):$(id -g) -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
  -v $PWD/backend:/app -w /app trends-dev sh -c \
  "pytest -p no:cacheprovider src/tests -q && ruff check --no-cache . && black --check ."
```

- **Cobertura mínima:** `fail_under` em `pyproject.toml`, medida só sobre código de produção.
- **Testes com Mongo real:** `test_search.py` precisa de `$text`, que o mongomock não
  implementa. Ele lê `MONGO_TEST_URI` e é pulado sem ela; o CI sobe um `mongo:7`. Cada
  execução cria e apaga um banco `trends_test_*`.
- **Testes de documentação:** `test_docs.py` compara a tabela de endpoints do README da raiz
  com as rotas do app. Só roda com o repositório inteiro visível (no CI, sempre).

## Configuração

Toda variável de ambiente é lida em `src/infrastructure/config.py` (`settings`) — nenhum outro
módulo lê `os.environ`, e um teste garante isso. Variável nova entra no `Settings` **e** no
`.env.example` (também verificado por teste).

## Convenções

- **Dependência entre camadas:** `domain` ← `application` ← `infrastructure`/`apps`. O
  `domain/` não importa pymongo, httpx, pydantic, celery nem fastapi.
- **Datas:** sempre `utcnow()` de `src/infrastructure/utils/datetime_utils.py` (aware, UTC).
  O ruff barra `datetime.utcnow()`.
- **Collector novo:** use `retry_transient` e `RateLimiter` de `collectors/http_policy.py` e
  conte falhas em `errors` — a task usa isso para não reportar `ok` numa coleta que falhou.
- **Task nova:** `@shared_task(name="tasks.<nome>")`, rota de fila em `apps/worker/main.py`,
  fila no `-Q` do compose e módulo no `include=` do `Celery(...)`.
- **Migração nova:** `src/infrastructure/db/migrations/versions/vNNN_<descricao>.py`,
  registrada em `versions/__init__.py`. Nunca edite uma migração já aplicada.
- **Endpoint novo:** em `apps/api/routes.py` (já exige `X-API-Key`) **e** na tabela de
  endpoints do README da raiz — o `test_docs.py` falha se esquecer.
