# tests/test_worker_registro.py
"""
Toda task roteada ou agendada precisa estar registrada no worker.

`autodiscover_tasks(["apps.worker"])` só importava `apps/worker/tasks.py`, e
`tasks.hybrid_trend_analyze` (em `tasks_trend.py`) nunca era registrada: o beat
publicava a cada 30 min e o worker descartava com "unregistered task".

O registro é verificado num subprocesso porque `@shared_task` registra a task em
qualquer app assim que o módulo é importado — e o `test_imports_smoke` importa
`tasks_trend` direto. No mesmo processo este teste passaria mesmo com o bug.
"""

import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]

# Reproduz o que o `celery worker` faz ao subir: importa o app e carrega os
# módulos de task configurados (include/imports + autodiscover).
_SCRIPT = """
import json
from apps.worker.main import celery_app

celery_app.loader.import_default_modules()
print(json.dumps({
    "registradas": sorted(celery_app.tasks.keys()),
    "roteadas": sorted(celery_app.conf.task_routes),
    "agendadas": sorted(e["task"] for e in celery_app.conf.beat_schedule.values()),
}))
"""


def _estado_do_worker() -> dict:
    saida = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(saida.stdout.strip().splitlines()[-1])


def test_tasks_roteadas_estao_registradas():
    estado = _estado_do_worker()
    faltando = set(estado["roteadas"]) - set(estado["registradas"])
    assert not faltando, f"roteadas mas não registradas no worker: {sorted(faltando)}"


def test_tasks_agendadas_no_beat_estao_registradas():
    estado = _estado_do_worker()
    faltando = set(estado["agendadas"]) - set(estado["registradas"])
    assert not faltando, f"agendadas no beat mas não registradas: {sorted(faltando)}"
