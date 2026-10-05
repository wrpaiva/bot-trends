# apps/healthcheck/main.py
"""
Healthcheck do container da API (TIE-38) — chamado pelo HEALTHCHECK da imagem:

    python -m apps.healthcheck.main

O Docker Compose não reinicia container `unhealthy`; só o que TERMINA (com
`restart: unless-stopped`). Então este script decide e age:

- Liveness (`/health`) falhou N vezes seguidas → o processo da API está
  travado: manda TERM e, se ele não sair, KILL. O container termina e o
  restart policy o sobe de novo. Sem `docker.sock` (autoheal) — nada aqui
  ganha poder sobre o Docker.
- Readiness (`/health/ready`) falhou (Mongo fora → 503) → só `unhealthy`.
  Reiniciar a API não conserta o Mongo, e reiniciar em loop pioraria.

Exige `init: true` no compose: dentro do container o PID 1 ignora SIGKILL
vindo do próprio container; com o init, o PID 1 é o tini e a API é um filho
comum. Sem init, o script só avisa.

A contagem de falhas é do PROCESSO atual (PID + instante de início, lidos do
/proc): depois de um restart ela recomeça, e uma contagem velha não mata a API
nova no meio da subida. Só biblioteca padrão: roda a cada 30 s, tem de ser leve.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

# TERM → espera → KILL: dá tempo ao desligamento limpo, mas um loop travado
# não processa TERM. Liveness (3 s) + GRACE_S tem de caber no --timeout do
# HEALTHCHECK (15 s), senão o Docker mata este script antes do KILL.
GRACE_S = 5
LIVE_TIMEOUT_S = 3


def http_status(url: str, timeout: float = 5) -> int | None:
    """Status HTTP, ou None se nem conectou (processo morto/travado)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 — URL local fixa
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _stat(proc: Path, pid: int) -> list[str] | None:
    try:
        texto = (proc / str(pid) / "stat").read_text()
    except OSError:
        return None
    # `comm` vem entre parênteses e pode conter espaço e ")": corta no último
    return texto[texto.rindex(")") + 2 :].split()


def processo_da_api(proc: Path) -> tuple[int, str] | None:
    """(pid, instante de início) do filho do init — a API. None sem init."""
    filhos = []
    for d in proc.iterdir():
        if not d.name.isdigit() or d.name == "1":
            continue
        campos = _stat(proc, int(d.name))
        # campos[1] = ppid; campos[19] = starttime (campo 22 do /proc/<pid>/stat)
        if campos and campos[1] == "1":
            filhos.append((int(d.name), campos[19]))
    return min(filhos) if filhos else None


def _ler_estado(state: Path) -> dict:
    try:
        return json.loads(state.read_text())
    except (OSError, ValueError):
        return {}


def check(
    *,
    live_url: str,
    ready_url: str,
    state: Path,
    max_falhas: int,
    get: Callable[..., int | None] = http_status,
    proc: Path = Path("/proc"),
    kill: Callable[[int, int], None] = os.kill,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """0 = saudável; 1 = unhealthy (e, se a liveness esgotou, API encerrada)."""
    alvo = processo_da_api(proc)
    dono = f"{alvo[0]}:{alvo[1]}" if alvo else "pid1"

    if get(live_url, LIVE_TIMEOUT_S) == 200:
        state.write_text(json.dumps({"processo": dono, "falhas": 0}))
        return 0 if get(ready_url) == 200 else 1

    anterior = _ler_estado(state)
    falhas = (anterior.get("falhas", 0) if anterior.get("processo") == dono else 0) + 1
    state.write_text(json.dumps({"processo": dono, "falhas": falhas}))
    print(f"liveness falhou ({falhas}/{max_falhas})", file=sys.stderr)

    if falhas >= max_falhas:
        if alvo is None:
            print(
                "API sem init (é o PID 1): não dá para reiniciar daqui — use `init: true`",
                file=sys.stderr,
            )
            return 1
        pid = alvo[0]
        print(f"encerrando a API (pid {pid}) para o restart policy subir de novo", file=sys.stderr)
        try:
            kill(pid, signal.SIGTERM)
            sleep(GRACE_S)
            kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # saiu com o TERM
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--live-url", default="http://127.0.0.1:8000/health")
    parser.add_argument("--ready-url", default="http://127.0.0.1:8000/health/ready")
    parser.add_argument("--state", type=Path, default=Path("/tmp/healthcheck-api.json"))
    parser.add_argument(
        "--max-falhas",
        type=int,
        default=3,
        help="falhas seguidas de liveness antes de encerrar a API (×30 s do HEALTHCHECK)",
    )
    args = parser.parse_args()
    return check(
        live_url=args.live_url,
        ready_url=args.ready_url,
        state=args.state,
        max_falhas=args.max_falhas,
    )


if __name__ == "__main__":
    raise SystemExit(main())
