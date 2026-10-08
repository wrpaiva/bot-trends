# Deploy em VPS (TIE-34)

Produção = o `docker-compose.yml` de sempre **mais** o override `docker-compose.prod.yml`:

- só o **Caddy** é publicado no host (80 e 443). Mongo, Redis, API e o nginx do dashboard ficam
  nas redes internas do Docker;
- o Caddy emite e **renova sozinho** o certificado TLS (Let's Encrypt) e exige **login (basic
  auth)** para o dashboard e para `/api`. É o controle de acesso que a TIE-27 deixou pendente: o
  nginx do `web` injeta a `X-API-Key`, então sem login quem achasse o domínio usaria a API;
- `CORS_ORIGINS` vazio à força (o dashboard é mesma origem).

```
Internet ──443──▶ Caddy (TLS + basic auth) ──▶ web/nginx ──/api──▶ api ──▶ mongo/redis
                       └─ /health aberto (uptime)              (X-API-Key injetada)
```

## 1. Pré-requisitos

- VPS Linux com **Docker Engine** e o plugin **Compose ≥ 2.24** (o override usa `!reset`):
  `docker compose version`.
- **Domínio** com registro `A` (e `AAAA`, se a VPS tiver IPv6) apontando para a VPS. O Let's
  Encrypt valida pelo DNS + porta 80: sem isso o certificado não sai.
- **Firewall** liberando só o necessário: 22/tcp (SSH), 80/tcp, 443/tcp e 443/udp (HTTP/3).
  Ex. com ufw: `ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443 && ufw enable`.
  Atenção: o Docker publica portas por fora do ufw — por isso o override não publica nada além
  do Caddy.

## 2. Docker sobe no boot

```bash
sudo systemctl enable --now docker
```

Todos os serviços têm `restart: unless-stopped`: depois de um reboot, o Docker sobe e os
containers voltam sozinhos (exceto os que você parou com `docker compose stop`). Confira uma vez
com `sudo reboot` e, na volta, `docker compose ps`.

## 3. Código e segredos

```bash
git clone https://github.com/wrpaiva/bot-trends.git && cd bot-trends
```

Os segredos **não estão no repositório** (os dois `.env` estão no `.gitignore`). Crie-os **na
VPS** a partir dos exemplos, ou copie do seu computador por SSH — nunca por commit:

```bash
cp .env.example .env && cp backend/.env.example backend/.env    # e preencha
# ou, do seu computador:  scp .env vps:~/bot-trends/.env && scp backend/.env vps:~/bot-trends/backend/.env
chmod 600 .env backend/.env
```

No `.env` da raiz, além do de sempre (`MONGO_*`, `REDIS_PASSWORD`, `API_KEY` — gere com
`openssl rand -hex 32`):

| Variável | Valor |
|---|---|
| `COMPOSE_FILE` | `docker-compose.yml:docker-compose.prod.yml` — assim todo `docker compose ...` na VPS já usa o override |
| `DOMAIN` | `trends.seudominio.com` |
| `ACME_EMAIL` | e-mail que recebe avisos do Let's Encrypt (certificado perto de vencer, etc.) |
| `BASIC_AUTH_USER` | usuário do login |
| `BASIC_AUTH_HASH` | hash bcrypt da senha, **entre aspas simples** (abaixo) |
| `CORS_ORIGINS` | vazio |

Hash da senha (o comando pergunta a senha, que não fica no histórico do shell):

```bash
docker run --rm -it caddy:2.10.0 caddy hash-password
```

Cole no `.env` **entre aspas simples**: `BASIC_AUTH_HASH='$2a$14$...'`. Sem as aspas o compose
tenta expandir cada `$` do hash como variável e o login nunca bate.

> `MONGO_PASSWORD` só vale na primeira subida do volume (armadilha 3 do `CLAUDE.md`): defina antes
> do primeiro `up`.

## 4. Subir

```bash
mkdir -p backups                       # antes do 1º up: senão o Docker cria como root (armadilha 8)
docker compose build && docker compose up -d   # com COMPOSE_FILE no .env, já é o de produção
docker compose run --rm api python -m apps.migrate.main
docker compose run --rm api python -m apps.bootstrap.main
```

O primeiro acesso a `https://$DOMAIN` pode levar alguns segundos: é o Caddy emitindo o
certificado.

## 5. Conferir

```bash
curl -sI https://$DOMAIN/health                      # 200, aberto (monitor de uptime)
curl -s -o /dev/null -w '%{http_code}\n' https://$DOMAIN/          # 401 sem login
curl -s -o /dev/null -w '%{http_code}\n' -u usuario:senha https://$DOMAIN/api/health/ready   # 200
curl -s -o /dev/null -w '%{http_code}\n' http://$DOMAIN/            # 308 → https
sudo ss -ltnp | grep -E ':(80|443|27017|6379|8000) '                # só 80 e 443 (docker-proxy do Caddy)
docker compose logs caddy | grep -i certificate                     # "certificate obtained successfully"
```

No browser: `https://$DOMAIN` pede usuário e senha e abre o dashboard.

## 6. Observabilidade

Prometheus e Grafana (`--profile observability`) ficam só em `127.0.0.1` da VPS. Acesse por túnel
SSH, sem abrir porta:

```bash
docker compose --profile observability up -d prometheus grafana       # na VPS
ssh -L 3000:127.0.0.1:3000 -L 9090:127.0.0.1:9090 vps                 # no seu computador
# depois: http://localhost:3000 (Grafana) e http://localhost:9090 (Prometheus)
```

## 7. Atualizar e voltar atrás

```bash
git pull
docker compose build && docker compose up -d   # não `up -d --build`: ver nota abaixo
docker compose run --rm api python -m apps.migrate.main     # idempotente; só aplica as novas
```

Para voltar a uma versão anterior: `git checkout <commit>`, `docker compose build` e `docker compose up -d`.
`up -d --build` num comando só não serve: no Compose 2.37.1 (Ubuntu 24.04) ele constrói a imagem
nova e mantém o container antigo rodando (2026-10-08). Confira com
`docker inspect -f '{{.Image}}' trends_api` contra `docker image inspect -f '{{.Id}}' bot-trends-api`.
**Migrações não têm rollback** (só andam para frente): se a versão nova migrou o banco, restaure
o backup (`infra/backup/`, seção "Backup e restauração" do README) em vez de só trocar o código.

## 8. Certificado

O Caddy renova sozinho, ~30 dias antes de vencer. Os certificados e a conta ACME ficam no volume
`trends_caddy_data` — **não apague** esse volume: reemitir à toa esbarra no limite de emissões do
Let's Encrypt. Se a renovação falhar, o aviso vai para o `ACME_EMAIL` e aparece em
`docker compose logs caddy`.

## Testar sem VPS

Com `DOMAIN=localhost` o Caddy usa a própria CA local em vez do Let's Encrypt. Foi assim que
este setup foi validado (2026-10-05): login obrigatório em `/` e `/api` (401 sem senha ou com
senha errada, 200 com a certa), `/health` aberto, HTTP → HTTPS (308), cabeçalhos HSTS /
`nosniff` / `X-Frame-Options`, e o hash bcrypt chegando intacto ao container. Para não colidir
com outra coisa na 80/443 da máquina, use `CADDY_HTTP_PORT`/`CADDY_HTTPS_PORT` (o redirect
HTTP → HTTPS assume a 443).
