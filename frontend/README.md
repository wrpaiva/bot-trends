# Frontend — dashboard

React 18 · Vite 5 · chart.js. Mostra o ranking (`/rankings/latest`) e, por produto, o último
insight e a curva histórica. Visão geral e setup no [README da raiz](../README.md).

## Configuração (em tempo de build)

| Variável | Origem | Uso |
|---|---|---|
| `VITE_API_BASE` | `.env` da raiz | URL da API vista pelo **browser** (default `http://localhost:8000`) |
| `VITE_API_KEY` | `API_KEY` do `.env` da raiz, injetada pelo compose | Enviada no header `X-API-Key` |

Ambas são embutidas no bundle pelo Vite: mudou alguma, rode `docker compose build web` —
recriar o container não basta. A origem do dashboard precisa estar em `CORS_ORIGINS`.

> A `API_KEY` fica visível no JavaScript servido ao browser. Serve para uso local/privado;
> expor o dashboard na internet pede autenticação de verdade (TIE-27).

## Rodando

```bash
docker compose up -d --build web          # nginx, porta WEB_HOST_PORT (default 80)
docker compose --profile dev up           # hot-reload, porta WEB_DEV_HOST_PORT (default 5173)
```

Sem Docker:

```bash
npm ci
VITE_API_BASE=http://localhost:8000 VITE_API_KEY=... npm run dev
npm run build                             # o CI roda este
```

Testes (vitest) da lógica da tela — ordenação, estados de carregando/vazio/erro, mensagens de
erro da API — rodam no CI:

```bash
npm test
```

O `index.html` tem que ficar na raiz de `frontend/` — o Vite não o encontra em `src/`.
