# src/tests/test_backup.py
"""
Backup do MongoDB (TIE-35): infra/backup/backup.sh e restore_test.sh.

Os scripts rodam de verdade (bash), com `mongodump`/`mongosh`/`mongorestore`
trocados por stubs no PATH. Ficam na raiz do repositório: num container que
só monta `backend/`, estes testes são pulados; no CI rodam sempre.
"""

import datetime as dt
import json
import os
import pathlib
import stat
import subprocess

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[3]
BACKUP = RAIZ / "infra" / "backup" / "backup.sh"
RESTORE = RAIZ / "infra" / "backup" / "restore_test.sh"

pytestmark = pytest.mark.skipif(not (RAIZ / "infra").exists(), reason="infra/ fora do container")

SENHA = "senha-super-secreta"


def _stub(bindir: pathlib.Path, nome: str, corpo: str) -> None:
    arq = bindir / nome
    arq.write_text("#!/usr/bin/env bash\n" + corpo)
    arq.chmod(arq.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def ambiente(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    destino = tmp_path / "backups"
    destino.mkdir()
    log = tmp_path / "mongosh.log"

    # mongodump: escreve o arquivo pedido em --archive=... e loga como o real
    _stub(
        bindir,
        "mongodump",
        r"""
if [ -n "$DUMP_FALHA" ]; then
  echo "Failed: error connecting to db server: server selection timeout ($MONGO_URI)" >&2
  exit 1
fi
for a in "$@"; do case "$a" in --archive=*) out="${a#--archive=}";; esac; done
echo "conteudo" > "$out"
# Formato REAL do mongodump 100.x (com crases), copiado da saída de produção:
# a primeira versão do script assumia sem crases e gerava contagens vazias.
echo "2026-09-27T22:16:28.311+0000	done dumping \`trends.metrics\` (5 documents)" >&2
echo "2026-09-27T22:16:28.311+0000	done dumping \`trends.products\` (2 documents)" >&2
""",
    )
    # mongosh: só registra o que mandaram gravar
    _stub(bindir, "mongosh", f'echo "$@" >> "{log}"\n')

    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "MONGO_URI": f"mongodb://trends:{SENHA}@mongo:27017/?authSource=admin",
        "MONGO_DB": "trends",
        "BACKUP_DIR": str(destino),
        "BACKUP_RETENTION_DAYS": "14",
        "BACKUP_KEEP_MIN": "3",
        "BACKUP_RECORD": "1",
    }
    return {"env": env, "dir": destino, "log": log, "bin": bindir}


def _roda(script, amb, **extra):
    return subprocess.run(
        ["bash", str(script)], env={**amb["env"], **extra}, capture_output=True, text=True
    )


def _arquivos(amb):
    return sorted(p.name for p in amb["dir"].iterdir())


# --- backup.sh ------------------------------------------------------------------


def test_backup_gera_arquivo_contagens_e_registro_ok(ambiente):
    r = _roda(BACKUP, ambiente)
    assert r.returncode == 0, r.stderr

    (archive,) = (n for n in _arquivos(ambiente) if n.endswith(".archive.gz"))
    assert archive.startswith("trends-") and not archive.endswith(".tmp")
    contagens = json.loads(
        (ambiente["dir"] / archive.replace(".archive.gz", ".counts.json")).read_text()
    )
    assert contagens == {"metrics": 5, "products": 2}

    registro = ambiente["log"].read_text()
    assert "status: 'ok'" in registro and "backups.insertOne" in registro
    assert '"metrics":5' in registro.replace(" ", "")


def test_falha_no_dump_registra_failed_sem_vazar_senha(ambiente):
    r = _roda(BACKUP, ambiente, DUMP_FALHA="1")

    assert r.returncode != 0
    assert not [n for n in _arquivos(ambiente) if n.endswith(".archive.gz") or n.endswith(".tmp")]
    registro = ambiente["log"].read_text()
    assert "status: 'failed'" in registro and "server selection timeout" in registro
    # A URI (com senha) é argumento de conexão do mongosh; o que não pode é ir
    # para o erro gravado em `backups` nem para a saída do script
    erro_gravado = registro.split("error: '", 1)[1]
    assert SENHA not in erro_gravado and "***@" in erro_gravado
    assert SENHA not in r.stdout + r.stderr


def test_upload_que_falha_marca_backup_como_failed(ambiente):
    r = _roda(BACKUP, ambiente, BACKUP_UPLOAD_CMD="false {}")
    assert r.returncode != 0
    assert "status: 'failed'" in ambiente["log"].read_text()


def test_upload_recebe_o_caminho_do_arquivo(ambiente, tmp_path):
    marca = tmp_path / "enviado.txt"
    r = _roda(BACKUP, ambiente, BACKUP_UPLOAD_CMD=f"cp {{}} {marca}")
    assert r.returncode == 0, r.stderr
    assert marca.read_text().strip() == "conteudo"


def test_sem_registro_nao_chama_mongosh(ambiente):
    assert _roda(BACKUP, ambiente, BACKUP_RECORD="0").returncode == 0
    assert not ambiente["log"].exists()


def _antigo(amb, dias):
    ts = (dt.datetime.now(dt.UTC) - dt.timedelta(days=dias)).strftime("%Y%m%dT%H%M%SZ")
    for ext in (".archive.gz", ".counts.json"):
        (amb["dir"] / f"trends-{ts}{ext}").write_text("x")
    return ts


def test_retencao_apaga_antigos_mas_preserva_o_minimo(ambiente):
    velhos = [_antigo(ambiente, d) for d in (40, 30, 20)]
    recente = _antigo(ambiente, 1)

    assert _roda(BACKUP, ambiente).returncode == 0

    restantes = {n.split(".")[0].removeprefix("trends-") for n in _arquivos(ambiente)}
    # 3 mais novos ficam: o de agora, o de 1 dia e o de 20 dias (mínimo), apesar dos 14 dias
    assert recente in restantes and velhos[2] in restantes
    assert velhos[0] not in restantes and velhos[1] not in restantes
    assert len(restantes) == 3
    # contagens vão embora junto com o arquivo
    assert not any(velhos[0] in n for n in _arquivos(ambiente))


def test_retencao_nao_mexe_em_arquivo_alheio(ambiente):
    (ambiente["dir"] / "anotacoes.txt").write_text("não é backup")
    _antigo(ambiente, 90)
    _roda(BACKUP, ambiente, BACKUP_KEEP_MIN="1")
    assert "anotacoes.txt" in _arquivos(ambiente)


# --- restore_test.sh ------------------------------------------------------------


def _prepara_restore(amb, contagens_restauradas):
    _roda(BACKUP, amb, BACKUP_RECORD="0")
    (archive,) = (p for p in amb["dir"].iterdir() if p.name.endswith(".archive.gz"))
    _stub(amb["bin"], "mongorestore", 'echo "restaurado" >&2\n')
    _stub(amb["bin"], "mongosh", f"echo '{json.dumps(contagens_restauradas)}'\n")
    return archive


def test_restauracao_confere_contagens(ambiente):
    archive = _prepara_restore(ambiente, {"metrics": 5, "products": 2})
    r = subprocess.run(
        ["bash", str(RESTORE), str(archive)],
        env={**ambiente["env"], "RESTORE_URI": "mongodb://descartavel:27017"},
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "OK" in r.stdout


def test_restauracao_com_divergencia_falha(ambiente):
    archive = _prepara_restore(ambiente, {"metrics": 4, "products": 2})
    r = subprocess.run(
        ["bash", str(RESTORE), str(archive)],
        env={**ambiente["env"], "RESTORE_URI": "mongodb://descartavel:27017"},
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0
    assert "metrics" in r.stdout and "5" in r.stdout and "4" in r.stdout


def test_restauracao_exige_banco_separado(ambiente):
    archive = _prepara_restore(ambiente, {})
    r = subprocess.run(
        ["bash", str(RESTORE), str(archive)],
        env={**ambiente["env"], "RESTORE_URI": ""},
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0 and "RESTORE_URI" in r.stderr
