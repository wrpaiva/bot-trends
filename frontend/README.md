# Frontend — dashboard

React 18 · Vite 5 · chart.js. Mostra o ranking (`/rankings/latest`) e, por produto, o último
insight e a curva histórica. Visão geral e setup no [README da raiz](../README.md).

## Como fala com a API (TIE-27)

O dashboard chama `/api/...` **na mesma origem** e não manda chave nenhuma. Quem fala com a API
é o servidor, que põe o header `X-API-Key`:

| Modo | Proxy | De onde vem a chave |
|---|---|---|
| Produção (`web`) | nginx, `nginx/default.conf.template` → `api:8000` | `API_KEY` no ambiente do container, aplicada na subida (`envsubst`) |
| Desenvolvimento (`web-dev`) | proxy do Vite (`vite.config.js`) → `API_PROXY_TARGET` | `API_KEY` no ambiente do Node — sem prefixo `VITE_`, nunca vai para o bundle |

A chave não está no bundle, na imagem nem no build: mudou a `API_KEY`, basta recriar o container
(`docker compose up -d web`). Sem CORS no caminho, porque é a mesma origem. O CI faz o build com
uma chave-canário no ambiente e falha se ela aparecer no `dist`.

> Isso esconde a chave, não autentica quem acessa: quem alcança o dashboard usa a API pelo
> proxy. Antes de expor na internet, controle de acesso no proxy com TLS (TIE-34).

## Rodando

```bash
docker compose up -d --build web          # nginx, porta WEB_HOST_PORT (default 80)
docker compose --profile dev up           # hot-reload, porta WEB_DEV_HOST_PORT (default 5173)
```

Sem Docker:

```bash
npm ci
API_KEY=... API_PROXY_TARGET=http://localhost:8000 npm run dev   # proxy /api → API
npm run build                             # o CI roda este
```

Testes (vitest) da lógica da tela — ordenação, estados de carregando/vazio/erro, mensagens de
erro da API — rodam no CI:

```bash
npm test
```

O `index.html` tem que ficar na raiz de `frontend/` — o Vite não o encontra em `src/`.
