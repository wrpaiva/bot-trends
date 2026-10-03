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
│   │   └── bootstrap/    # Seed de categorias
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

Sem Python 3.11/Poetry na máquina, use a imagem de dev (tem pytest, ruff e black):
```bash
docker build --target development -t trends-dev backend
docker run --rm --user $(id -u):$(id -g) -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
  -v $PWD/backend:/app -w /app trends-dev sh -c \
  "pytest -p no:cacheprovider src/tests -q && ruff check --no-cache . && black --check ."
```
Sem o `--user`, o container grava `__pycache__` e `.ruff_cache` como root dentro de `backend/`.

Testes que precisam de Mongo real (hoje só `test_search.py`: o mongomock não implementa `$text`)
leem `MONGO_TEST_URI` e são **pulados** sem ela. O CI sobe um `mongo:7` e roda sempre. Local,
contra o `trends_mongo` do compose (cada execução cria e apaga um banco `trends_test_*`):
```bash
set -a; . ./.env; set +a
docker run --rm --user $(id -u):$(id -g) -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
  --network trends_backend \
  -e MONGO_TEST_URI="mongodb://${MONGO_USER}:${MONGO_PASSWORD}@trends_mongo:27017/?authSource=admin" \
  -v $PWD/backend:/app -w /app trends-dev pytest -p no:cacheprovider src/tests -q
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

**Toda config passa por `src/infrastructure/config.py` (`settings`).** Nenhum outro módulo lê
`os.environ` — `src/tests/test_config.py` falha se isso voltar. Variável nova entra no `Settings`
com o mesmo nome do `.env`. A API **não sobe** sem `API_KEY` (validado no `lifespan`); o worker
só avisa no log quando falta credencial opcional (`APIFY_TOKEN`, `TELEGRAM_*`, `LLM_API_KEY`).
Atenção: o pydantic lê o `backend/.env` quando você roda fora do Docker, coisa que o
`os.getenv` antigo não fazia — os valores de lá (ex.: `REDIS_URL=redis://redis:...`) passam a
valer também no seu terminal.

**Nunca** commitar segredos. Ambos os `.env` estão no `.gitignore`; os `.env.example` só recebem
placeholders.

---

## Convenções ao adicionar código

**Nova task Celery:** decore com `@shared_task(name="tasks.<nome>")`, registre a rota de fila
em `apps/worker/main.py` (`task_routes`), garanta que o worker consome essa fila
(`-Q` no comando do compose) **e**, se a task estiver num módulo novo, acrescente o módulo ao
`include=` do `Celery(...)`. Sem isso a task é publicada e nunca executa.

**Nova migração:** crie `src/infrastructure/db/migrations/versions/vNNN_<descricao>.py` seguindo
`migration_base.Migration`, registre em `migrations/__init__.py` (`get_migrations()`; o
`versions/__init__.py` é vazio) e na lista esperada de `test_imports_smoke.py`. Migrações são idempotentes e
protegidas por lock distribuído — nunca edite uma migração já aplicada, crie a próxima.

**Novo collector:** implemente em `src/infrastructure/collectors/`, use context manager
(`with Collector(...) as c`) e devolva dicts normalizados para `ProductRepo.upsert`.

**Novo endpoint:** vá em `apps/api/routes.py`. Tudo que está nesse router já exige `X-API-Key`
(injetado no `include_router`). Rotas públicas ficam em `apps/api/main.py`. Para rate limit
específico use `@limiter.limit(...)` — e nesse caso o handler **precisa** receber `request: Request`.
Toda rota entra também na **tabela de endpoints do `README.md` da raiz**: `test_docs.py` falha
se as duas divergirem (no CI; o container de dev só monta `backend/` e pula esse teste).

**Scoring:** os defaults dos pesos vivem em `ScoreWeights`/`HybridWeights` (`src/domain/scoring.py`)
e são sobrescritos por `SCORE_W_*` no `backend/.env` — calibrar não exige deploy de código.
A fórmula final é `numeric × SCORE_W_NUMERIC + llm × SCORE_W_LLM` (default 0.6/0.4), com
fallback automático se o LLM falhar. Pesos que não somam 1.0 derrubam o processo na subida.
Cada insight grava os pesos usados em `score_weights`. Mudou um **default**? Atualize os
valores dourados de `src/tests/test_scoring.py` no mesmo commit, conscientemente (TIE-22).

---

## Armadilhas conhecidas (verificado em 2026-09-04)

Não são "coisas a arrumar agora", são coisas que vão te morder se você não souber:

1. **A API do Mercado Livre não é mais pública — e o ML não tem client credentials.**
   `/highlights`, `/sites/MLB/search` e `/items` exigem `Bearer`. O OAuth está implementado
   (TIE-41, `collectors/ml_auth.py`), mas **só funciona depois que alguém cria o app no ML e roda
   `python -m apps.ml_auth.main` uma vez** (fluxo `authorization_code` no navegador). Até lá
   `collect_ml` devolve `status: error` com o motivo em `reason`. **Ainda não confirmado com
   token real:** se `/highlights` responde a token de usuário comum; se não responder, a origem
   do collector muda e a Fase 2 fica maior. O refresh token é de **uso único**: nunca renove
   fora de `MercadoLivreAuth` (ele grava o par novo com compare-and-set no Mongo, `ml_oauth`);
   um refresh "de teste" à mão invalida o token do worker.
2. **`rank_momentum` e `reviews_velocity` estão hardcoded em `0.0`** em `tasks_trend.py`,
   ou seja 35% do score numérico é sempre zero (TIE-16). E boa parte do topo do ranking é
   conteúdo sem produto (dança, meme de `#fyp`) — resolvido pelo filtro comercial (ver "Já
   corrigido"); o vínculo TikTok ↔ produto do ML continua pendente (TIE-18).
3. **`MONGO_PASSWORD` só vale na primeira subida do volume.** `MONGO_INITDB_ROOT_PASSWORD` é
   lido apenas quando `/data/db` está vazio. Trocar a senha no `.env` com o volume
   `trends_mongo_data` já existente dá `storedKey mismatch` e o healthcheck nunca fica verde.
   Para valer: `docker compose down && docker volume rm trends_mongo_data`.
4. **`http://localhost:80` não é uma origem válida.** Na porta 80 o browser envia
   `http://localhost`, sem a porta. Com `:80` explícito em `CORS_ORIGINS` o preflight falha.
5. **O LLM se ancora em qualquer número pronto que estiver no prompt.** Até 2026-09-27 todo
   insight caiu no fallback (`429` = cota esgotada na OpenAI). Em 2026-10-01, com chave nova, os
   primeiros 20 insights com LLM (prompt v2) tinham `llm_score` a ±0,4 do numérico em todos: o
   prompt mandava `numeric_score.score_0_100` e o modelo copiava. A TIE-26 (prompt v4) tirou o
   score pronto e o `previous_final_score`, mandou a referência do grupo (mediana/p90), o
   marcador comercial e o nº de leituras, e trouxe few-shot. Nos mesmos 20 vídeos: diferença
   média llm×numérico 0,2 → 4,8 (máx. 0,4 → 23), escala 25–58 → 15–75, recomendações do ponto
   de vista de quem compra. **Duas armadilhas do prompt:** `social_velocity` nunca é negativo e
   vale 0 com leitura única ("não medido", não "parado"); e com só um exemplo de `EM_QUEDA` (vídeo
   velho) o modelo marcou queda em vídeo de 5 h sem tração — daí o exemplo "novo sem tração =
   ESTAVEL". Não volte a pôr score pronto no prompt. Mexeu no prompt? Suba `PROMPT_VERSION`
   (`src/domain/llm_cache.py`); o insight grava `prompt_version`. Revalide com dados reais.
6. **A coleta do TikTok está parada desde ~2026-09-25: crédito da Apify esgotado.** O actor
   devolve `402 Payment Required` a cada ciclo. O worker com o código antigo retentava e
   devolvia `status: ok, inserted: 0` — ninguém viu por 2,7 dias (achado pelo check de saúde da
   TIE-39). Com a análise rodando sobre métricas cada vez mais velhas, o ranking congela e, em
   72 h, esvazia (último insight: 2026-09-28). **Saldo aparente não basta:** em 2026-10-01 a conta
   mostrava US$ 4,56 de US$ 5 usados (ciclo 24/09–23/10) e o actor ainda devolvia 402 — com
   pouco saldo a Apify recusa a execução. Upgrade de plano ou recarga destrava; sem isso, só na
   virada do ciclo. Saldo da conta (grátis): `GET https://api.apify.com/v2/users/me/limits`.
7. **As hashtags do TikTok definem o que o motor monitora.** `fyp` trazia conteúdo global e
   antigo (vídeos em árabe, de 2022); desde 2026-09-24 o `backend/.env` usa
   `tiktokmademebuyit,achadinhos,achadosdashopee`. **Custo:** ~US$ 0,0037 por vídeo
   (medido em 2026-09-24: US$ 0,186 por execução de 50 vídeos). Volume controlado por
   `TIKTOK_RESULTS_PER_HASHTAG` (default 10) e `TIKTOK_INTERVAL_MIN` (default 120). Antes
   disso eram 50 vídeos a cada 30 min, e uma noite consumiu US$ 3,55 dos US$ 5 do plano
   gratuito. Ao mexer em `main.py`, confira o agendamento **dentro** do container do beat —
   já aconteceu de ele continuar com a imagem antiga depois de um `up -d --build`.
8. **O backup não roda se o Docker criar `./backups`.** Sem a pasta no host, o Docker cria o
   bind mount como `root`, e o serviço `backup` roda como `BACKUP_UID` (1000): todo ciclo falha
   com `permission denied` no `mongodump`. Aconteceu em 2026-09-28 e passou 3 dias sem ninguém
   ver — o check de saúde detectou, mas sem `TELEGRAM_SYSTEM_CHAT_ID` o alerta só vai para o
   log. Crie a pasta antes do primeiro `up`. Sem sudo, devolva o dono com
   `docker run --rm -v "$PWD/backups:/b" mongo:7 chown 1000:1000 /b` e rode um ciclo com
   `docker compose exec backup bash /backup/backup.sh`. Corrigido assim em 2026-10-01 (dump de
   11.505 documentos, `restore_test.sh` com todas as contagens batendo).

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
- **`get_db()` abria um `MongoClient` novo a cada chamada.** Agora é um cliente por processo
  (`mongo.get_client()`), fechado no `lifespan` da API; no worker, `worker_process_init` descarta
  o cliente herdado do fork, porque `MongoClient` não é fork-safe. Não volte a instanciar
  `MongoClient` fora de `mongo.py` (TIE-7).
- **Arquivos mortos removidos** (`migrations/versions/runner.py`, `backend/docker-compose.yml`,
  `frontend/docker-compose.yml`) e **lint zerado**: `ruff check .` e `black --check .` passam
  no backend inteiro. O CI (`.github/workflows/ci.yml`) bloqueia qualquer regressão (TIE-9,
  TIE-11).
- **`tasks.hybrid_trend_analyze` nunca era registrada no worker.** `autodiscover_tasks` só
  importava `apps/worker/tasks.py`; o beat publicava a análise a cada 30 min e o worker
  descartava como `unregistered task` — a análise agendada nunca tinha rodado. Agora os módulos
  vão explícitos no `include=` do `Celery(...)` em `apps/worker/main.py`. **Módulo de task novo
  entra nessa lista**; `src/tests/test_worker_registro.py` falha se uma task roteada ou agendada
  não estiver registrada (roda em subprocesso de propósito — ver docstring).
- **`APIFY_TOKEN` vazava nos logs do worker.** Ia na query string (`?token=...`) e o
  `HTTPStatusError` loga a URL inteira. Agora vai no header `Authorization: Bearer` do
  `httpx.Client` e o collector lê de `settings`. `src/tests/test_tiktok_collector.py` falha se o
  token voltar para a URL ou para o log. Nunca coloque credencial em URL (TIE-3).
- **Collectors sem política de retry e `status: ok` falso.** Retentavam qualquer `HTTPError`
  (inclusive 401/404) e as tasks devolviam `{"status": "ok", "inserted": 0}` com toda requisição
  falhando. Agora `collectors/http_policy.py` centraliza: retry só em 429/5xx/timeout/rede,
  `Retry-After` respeitado (teto de 60 s), `RateLimiter` por collector
  (`ML_MAX_REQUESTS_PER_MINUTE`, `APIFY_MAX_REQUESTS_PER_MINUTE`; `0` desliga). Cada collector
  expõe `errors`, e as tasks devolvem `status` `ok`/`partial`/`error` + `errors`. **Collector
  novo usa `retry_transient` e `RateLimiter` e conta `errors`**, senão a task volta a mentir
  (TIE-17).
- **Seed de categorias sem saída.** O seed default gravava categorias sem `ml_category_id`, então
  elas não apareciam em `GET /categories`, `POST /categories/enable` não as achava e `collect_ml`
  devolvia `no enabled categories` para sempre. Agora o seed traz os IDs de primeiro nível do
  MLB (ainda não conferidos na API — ver TIE-41), casa por `ml_category_id` (a árvore do ML não
  duplica o default) e grava `enabled`/`keywords`/`key` só no insert: **rodar o bootstrap de novo
  não desabilita o que o usuário habilitou**. `apps/tools/find_categories.py` foi removido —
  era o mesmo que `GET /categories?query=` (TIE-20).
- **Datas naive (`datetime.utcnow()`).** Todas as datas agora são aware em UTC:
  `src/infrastructure/utils/datetime_utils.utcnow()` é o único jeito de pegar "agora", o
  `MongoClient` é criado com `tz_aware=True, tzinfo=UTC` (o que vem do banco também é aware) e
  `ensure_utc()` normaliza valor naive legado. O ruff barra `datetime.utcnow()` (`DTZ003`).
  **Em teste com mongomock, use `mongomock.MongoClient(tz_aware=True)`** — sem isso as datas
  voltam naive e a comparação com `utcnow()` quebra. Efeito colateral bom: a API passou a
  mandar `+00:00`, e o `CurveChart` deixou de exibir o horário deslocado em 3 h (TIE-10).
- **Config lida de `os.environ` em 7 módulos**, cada um com o próprio default (TIE-8). Agora é
  tudo `settings`, com `API_KEY`, `CORS_ORIGINS` e as flags `BOOTSTRAP_*` na classe; a chave
  da API é comparada com `secrets.compare_digest` e lida a cada requisição.
- **`/rankings/latest` e `/insights/latest` sempre vazios.** Filtravam
  `window_from >= agora - hours`, mas a task grava `window_from = t_análise - hours`, sempre
  anterior — nenhum insight passava e o dashboard nunca mostrou ranking. Agora filtram por `ts`
  (momento da análise). Coberto em `test_routes.py`, cujo helper grava insight do jeito que a
  task grava (TIE-12).
- **Lock de migração estourava `DuplicateKeyError`** quando outra instância o segurava (o upsert
  tentava inserir um segundo `global`). Agora devolve `False` e o runner dá a mensagem clara
  (TIE-12).
- **Token do Telegram vazava no traceback.** A API do Telegram exige o token na URL e o
  `HTTPStatusError` do httpx carrega a URL. `TelegramNotifier.send` agora levanta
  `TelegramError` sem URL e sem encadear a exceção original (`from None`) (TIE-12).
- **Resposta do LLM sem validação.** Classificação alucinada ou score 150 iam direto para o banco.
  Agora `src/infrastructure/llm/schema.py` (pydantic, fora do `domain/` de propósito) restringe
  classificação/risco aos `Literal` do domínio, clampa score em 0–100 e confidence em 0–1, e
  transforma qualquer outra falha em `LLMResponseError`. O engine loga WARNING e cai no fallback
  numérico. **Campo novo na resposta do LLM entra no `LLMResponse`, não em `obj[...]` no
  cliente** (TIE-24).
- **Alerta repetido a cada 30 min, e uma falha derrubava a análise.** Agora a regra mora em
  `src/domain/alerting.py` (um alerta por produto por `ALERT_COOLDOWN_HOURS`, realerta antes só
  se a classificação subir de faixa; limiar em `ALERT_THRESHOLD`), com estado na collection
  `alert_state` (sobrevive a restart; só é gravado se o envio deu certo). `TelegramError` num
  produto não para os outros, e **Telegram ou LLM sem config não derrubam mais a task**: sem
  Telegram a análise roda sem alertas, sem LLM cai no score numérico. A task devolve
  `alerts_sent`/`alerts_suppressed`/`alerts_failed` (TIE-31).
- **`GET /search` documentado e inexistente.** Agora existe: índice de texto em `products.title`
  com stemming em português (migração `v005` — **rode `apps.migrate.main` em quem já está no ar**,
  sem o índice a rota devolve 503), ordenado por relevância, paginado (`page`, `limit` ≤ 50) e
  com o último score de cada produto (TIE-30).
- **READMEs desatualizados e duplicados.** O `backend/README.md` era cópia do da raiz e os dois
  mentiam (fórmula de score, filas, schemas, "rollback", `/search` inexistente). Agora o da raiz
  é a referência, o do backend cobre só desenvolvimento e o do frontend explica o build.
  `test_docs.py` amarra a tabela de endpoints às rotas reais e o `backend/.env.example` aos
  campos do `Settings` (TIE-33).
- **Health falso e API refém do Redis.** `/health` (liveness) não depende de nada e fica fora do
  rate limit; `/health/ready` checa Mongo (503 se fora), Redis e migrações (`degraded`, 200 —
  503 aí travaria a primeira subida, que migra depois do `up`). O `HEALTHCHECK` da imagem usa a
  readiness; o `worker` tem healthcheck próprio (`celery inspect ping`) e o `beat` tem
  `disable: true` — antes os dois herdavam o curl HTTP e apareciam `unhealthy` à toa. Com o
  Redis fora, o limiter cai para contador em memória (`in_memory_fallback_enabled`). **Não use
  `swallow_errors`**: no slowapi 0.1.10 ele quebra o middleware. Compose não reinicia container
  `unhealthy` — só os que terminam (TIE-38).
- **Nada era logado.** Agora `src/infrastructure/logging_setup.py` põe API, worker e beat em JSON
  (`LOG_FORMAT=text` para ler no terminal), com **redação na string final**: valores de
  credenciais do `settings`, senha em URI, `Bearer`, `token=` e `/bot<token>` viram `***`.
  Toda task loga `task.inicio`/`task.fim` (duração + contagens do retorno) via sinais do Celery;
  toda chamada externa loga `http.chamada` (status, latência, path sem query) via
  `http_log_hooks`. **Cliente httpx novo recebe `event_hooks=http_log_hooks("<servico>")`.** Os
  loggers `httpx`/`httpcore` ficam em WARNING: em INFO eles imprimem a URL inteira, token
  incluído (TIE-13).
- **Score absoluto saturado e cego para nichos.** Views/engajamento/velocidades agora viram
  percentil dentro da categoria (`src/domain/percentile.py`, `SCORE_NORMALIZATION=percentile`),
  com fallback para o pool global e depois para o absoluto (`SCORE_PERCENTILE_MIN_GROUP`). Em
  2026-09-27, nos 501 produtos reais: no modo absoluto dezenas empatavam em exatamente 40,0 (as
  faixas de 300 mil views / 20 mil de engajamento são baixas para TikTok); com percentil só 4 do
  top 10 se mantêm. **A escala subiu** (média 16 → 45, máximo 52 → 72); o `ALERT_THRESHOLD`
  foi recalibrado de 85 (inalcançável: teto ~80,5 com `rank`/`reviews` zerados) para 60 em
  2026-10-03, com replay das leituras reais — racional no README, "Alertas". No mesmo dia a
  classificação do fallback sem LLM foi de 75/85 para 60/66 (`fallback_classification` em
  `scoring.py`). **Ligou a TIE-16? O teto sobe: recalibre os dois.** Enquanto o ML não coleta, todo
  produto tem `category=None` e o percentil é, na prática, global (TIE-21).
- **Upsert de produto com corrida e id de vídeo instável.** O `ProductRepo.upsert` lia e depois
  gravava: a coleta que perdia uma corrida devolvia um UUID nunca gravado (métricas órfãs, série
  partida). Agora é `find_one_and_update` atômico, com uma nova tentativa em `DuplicateKeyError`.
  O TikTok usava a URL como id quando faltava `id`; agora extrai o id numérico da URL e descarta
  o item sem nenhum dos dois. **Chave de dedupe = `(source, source_product_id)`**; `product_id` é
  o UUID interno; `canonical_id` é para casar entre fontes (TIE-18), não para dedupe; **não existe
  campo `uuid`**. Em 2026-09-27: 0 duplicados nos 501 produtos reais (TIE-19).
- **LLM chamado a cada 30 min mesmo com métricas paradas** (até 2.400/dia). O engine agora
  consulta `LLMCache` (Redis, `src/infrastructure/llm/cache.py`) com chave das **métricas brutas**
  do produto (`src/domain/llm_cache.py`) — não do prompt, porque com percentil os componentes
  mudam quando outros produtos mudam. Só resposta válida é cacheada; Redis fora = miss.
  **Mudou o prompt ou o schema de resposta? Suba `PROMPT_VERSION`** em `llm_cache.py`, senão o
  cache devolve análise do prompt antigo por até `LLM_CACHE_TTL_HOURS` (TIE-25).
- **Listagens sem paginação real.** `/rankings/latest` e `/insights/latest` agora paginam por
  cursor opaco (`apps/api/pagination.py`), com desempate estável (`product_id` no ranking, `_id`
  nos insights) para não repetir nem pular item entre páginas. Índice `(window_hours, ts)` em
  `trend_insights` (migração `v006`), conferido com `explain()` no Mongo real. O dashboard tem
  "Carregar mais" (TIE-32).
- **Filtro de período do dashboard sempre vazio fora de 72 h.** A API exigia
  `window_hours == hours`, mas o beat analisa sempre com janela de 72 h — 24/48/168 h davam lista
  vazia. Agora `hours` é só recência e `window_hours` é filtro opcional explícito (índices `ts` e
  `(window_hours, ts)` na v006). O dashboard ganhou ordenação por coluna, estados explícitos de
  carregando/vazio/erro e mensagens de erro acionáveis (rede/CORS, 401, 429, 5xx com `detail`);
  a lógica fica em `frontend/src/ranking.js` e tem testes vitest (`npm test`, no CI) (TIE-28).
- **Drawer de produto frágil.** Um 404 no insight (produto ainda não analisado) derrubava o
  `Promise.all` e escondia até a curva; métrica ausente (preço no TikTok) era plotada como 0; o
  eixo só tinha `HH:MM` numa janela de 72 h; dados do produto anterior ficavam na tela. Agora
  curva e insight carregam independentes (`allSettled`, 404 → "ainda não analisado"), lacunas em
  vez de zero, rótulo `dd/MM HH:mm`, breakdown numérico/IA/final com os pesos do insight e aviso
  quando o LLM caiu. Lógica em `frontend/src/drawer.js`, com testes (TIE-29).
- **Falha prolongada queimava cota a cada ciclo.** `src/infrastructure/circuit_breaker.py`: um
  circuito por serviço (`mercadolivre`, `apify`, `llm`), estado no Redis (`breaker:<serviço>`)
  compartilhado entre processos, fail-open se o Redis cair. Coleta com circuito aberto devolve
  `circuit_open`; o LLM é embrulhado em `BreakerLLMClient` (resposta fora do schema **não**
  conta como falha). Com o 429 atual, o ciclo passa de 50 chamadas para 5. **Serviço externo
  novo ganha seu breaker** e entra em `SERVICES` (TIE-37).
- **Falha silenciosa do sistema.** `apps/worker/tasks_health.py` roda a cada 15 min (fila
  `celery`) e checa: coleta parada por fonte ativa, análise parada, taxa de fallback do LLM,
  filas do Celery e backup (collection `backups`). Alertas vão para
  `TELEGRAM_SYSTEM_CHAT_ID` — **separado** do chat de tendências; sem ele, só log `ERROR`. Cada
  check tem cooldown (`HEALTH_ALERT_COOLDOWN_HOURS`) e avisa "✅ normalizado" quando some. No
  primeiro teste contra os dados reais acusou TikTok parado há 64 h, ML nunca coletado e LLM 100%
  em fallback (TIE-39).
- **Não havia backup.** Serviço `backup` (imagem `mongo:7`) roda `infra/backup/backup.sh`:
  dump atômico, `.counts.json` como gabarito, retenção com mínimo garantido, registro em
  `backups` (alimenta o check de saúde). `restore_test.sh` restaura num Mongo **descartável** e
  confere contagens — em 2026-09-27 restaurou 10.781 documentos com todas as contagens batendo.
  **O `mongodump` 100.x escreve o namespace entre crases** (`` done dumping `trends.metrics` ``):
  a 1ª versão do script assumia sem crase e gerava gabarito vazio — o stub do teste usa o
  formato real. Destino fora da máquina ainda depende de escolha do usuário (TIE-35).
- **O score media popularidade, não tendência.** `views_24h`/`engagement_24h` eram o TOTAL
  acumulado do vídeo (o nome "24h" é histórico e ficou), `social_velocity` era 0 em 84% dos
  insights e o preço ausente dava 10 pontos de graça: o topo era um vídeo de 4 anos. Agora
  `src/domain/trend_signals.py` calcula views/engajamento **por hora de vida** (piso 6 h) e
  aceleração entre leituras (piso de 1.000 views; leitura única = 0), vídeo > 30 dias sai da
  análise, preço ausente = 0,5. A coleta grava `published_at`, `has_shop_product`, `language` e
  `saves`; vídeos antigos ganham a data via `apps.backfill.tiktok_published`. **Duas ideias
  foram testadas com os 501 vídeos reais e reprovadas** — frescor como sinal social (punha vídeo
  de 0 dia com 61 views/h no topo) e aceleração sem piso de volume (100→365 views venciam 226 mil
  views/h no percentil). Mexeu no motor? Revalide com dados reais, não só com os testes.
- **Métricas do TikTok sempre zeradas.** O `clockworks~tiktok-scraper` passou a devolver
  `playCount`/`diggCount`/`commentCount`/`shareCount` no primeiro nível do item (`stats` vem
  `None`), e `_normalize_item` só lia `stats`. Agora aceita os dois formatos; o teste usa um
  item real do actor. Se o actor mudar de novo, o sintoma é `views`/`engagement` = 0.
- **Conteúdo sem produto disputava o ranking.** `src/domain/commercial.py` decide se um vídeo
  do TikTok vende algo: produto do TikTok Shop **ou** marca de venda no texto (loja, "link na
  bio", "comenta QUERO", R$, código de produto da Shopee, "achadinho"). Sem isso, fica fora da
  análise (`TREND_REQUIRE_COMMERCIAL`, `skipped_no_product`; insight grava `commercial_marker`).
  **Não troque por `has_shop_product` puro:** nos 501 vídeos reais ele perderia 79 dos 97 das
  hashtags de compra (afiliado da Shopee manda para a bio). **`isAd` não serve**: marcou 21
  vídeos de `#fyp` sem produto. Texto normalizado com leet (`L!nks`, `Bl0`), porque afiliado
  escreve assim para escapar do filtro da plataforma. Mexeu nos marcadores? Revalide com os
  vídeos reais (os datasets da Apify ainda têm os 501) (TIE-18, parte 1).
- **Collector do ML sem autenticação.** Agora `MercadoLivreAuth` (Bearer, renovação 5 min antes
  de vencer, uma renovação por coleta em 401, `invalid_grant` relê o Mongo antes de desistir —
  outro worker pode ter renovado) e `MLAuthError` interrompe a coleta inteira com `auth_error`.
  Sem `ML_CLIENT_ID`/`ML_CLIENT_SECRET` a task não mexe no circuit breaker (é config, não falha
  do serviço). `ML_CLIENT_SECRET` está na redação do log, e `APP_USR-...`/`TG-...` são mascarados
  por padrão — os tokens moram no Mongo, não no `settings` (TIE-41).
- **Índice de texto recusava vídeo em árabe.** O índice da v005 usava o `language_override`
  padrão do Mongo: o campo `language` do documento escolhe o stemming. A coleta passou a gravar
  `language` com o idioma do vídeo, e idioma não suportado (`ar`, `ms`, `un`...) fazia a escrita
  falhar com `language override unsupported` — derrubou o backfill em produção e derrubaria a
  coleta. A v007 aponta o override para um campo que nunca é gravado. **O mongomock não
  reproduz isso**: teste de índice de texto vai em `test_search.py`, contra Mongo real.

---

## Fluxo de trabalho

1. Antes de mudar comportamento, leia o teste correspondente em `src/tests/`.
2. Mudou lógica de domínio? Teste primeiro, implementação depois.
3. Rode `pytest src/tests -q` e `ruff check .` antes de dizer que terminou. O CI roda com
   `--cov` e falha abaixo de `fail_under` (em `pyproject.toml`, hoje 85%, só código de
   produção). Subiu a cobertura? Suba o piso; nunca o baixe para passar.
4. Descobriu uma armadilha nova ou corrigiu uma da lista acima? **Atualize este arquivo.**
5. Não faça commit nem push sem eu pedir.
