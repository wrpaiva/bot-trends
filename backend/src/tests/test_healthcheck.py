# src/tests/test_healthcheck.py
"""
Healthcheck do container da API (TIE-38): liveness falhando N vezes seguidas
encerra o processo (o restart policy sobe de novo); readiness falhando só
marca unhealthy — reiniciar a API não conserta o Mongo fora.
"""

import signal

import pytest

from apps.healthcheck import main as hc

LIVE, READY = "http://x/health", "http://x/health/ready"


def _proc(base, processos):
    """Monta um /proc falso em `base`: {pid: (ppid, starttime)}."""
    for pid, (ppid, inicio) in processos.items():
        d = base / str(pid)
        d.mkdir(parents=True)
        # comm com espaço e parêntese: o parse tem que usar o ÚLTIMO ")"
        campos = ["S", str(ppid)] + ["0"] * 17 + [str(inicio)]
        (d / "stat").write_text(f"{pid} (uvicorn (x) y) " + " ".join(campos) + "\n")
    return base


class _Sinais:
    def __init__(self):
        self.enviados = []

    def __call__(self, pid, sig):
        self.enviados.append((pid, sig))


def _rodar(tmp_path, respostas, *, proc, kill=None, max_falhas=3):
    return hc.check(
        live_url=LIVE,
        ready_url=READY,
        state=tmp_path / "estado.json",
        max_falhas=max_falhas,
        get=lambda url, *a: respostas[url],
        proc=proc,
        kill=kill or _Sinais(),
        sleep=lambda s: None,
    )


@pytest.fixture
def proc(tmp_path):
    # tini (PID 1) → uvicorn (PID 7)
    return _proc(tmp_path / "proc", {1: (0, 100), 7: (1, 250)})


def test_vivo_e_pronto_e_saudavel(tmp_path, proc):
    assert _rodar(tmp_path, {LIVE: 200, READY: 200}, proc=proc) == 0


def test_pronto_falhando_so_marca_unhealthy_sem_matar(tmp_path, proc):
    sinais = _Sinais()
    for _ in range(5):
        assert _rodar(tmp_path, {LIVE: 200, READY: 503}, proc=proc, kill=sinais) == 1
    assert sinais.enviados == []


def test_liveness_falhando_abaixo_do_limite_nao_mata(tmp_path, proc):
    sinais = _Sinais()
    for _ in range(2):
        assert _rodar(tmp_path, {LIVE: None, READY: None}, proc=proc, kill=sinais) == 1
    assert sinais.enviados == []


def test_liveness_falhando_n_vezes_encerra_o_processo_da_api(tmp_path, proc):
    sinais = _Sinais()
    for _ in range(3):
        _rodar(tmp_path, {LIVE: None, READY: None}, proc=proc, kill=sinais)
    # TERM primeiro (desligamento limpo), KILL depois (loop travado ignora TERM)
    assert sinais.enviados == [(7, signal.SIGTERM), (7, signal.SIGKILL)]


def test_sucesso_zera_a_contagem(tmp_path, proc):
    sinais = _Sinais()
    for live in (None, None, 200, None, None):
        _rodar(tmp_path, {LIVE: live, READY: 200}, proc=proc, kill=sinais)
    assert sinais.enviados == []


def test_contagem_de_outro_processo_nao_vale(tmp_path, proc):
    # Depois do restart o PID/início mudam: a contagem antiga não pode matar
    # a API nova no meio da subida
    sinais = _Sinais()
    for _ in range(2):
        _rodar(tmp_path, {LIVE: None, READY: None}, proc=proc, kill=sinais)
    novo = _proc(tmp_path / "proc2", {1: (0, 100), 7: (1, 999)})
    _rodar(tmp_path, {LIVE: None, READY: None}, proc=novo, kill=sinais)
    assert sinais.enviados == []


def test_sem_init_nao_tenta_matar_o_pid_1(tmp_path):
    # Sem `init: true` a API é o PID 1, que ignora SIGKILL de dentro do
    # container: não há o que fazer daqui, só avisar
    proc = _proc(tmp_path / "proc", {1: (0, 100)})
    sinais = _Sinais()
    for _ in range(3):
        assert _rodar(tmp_path, {LIVE: None, READY: None}, proc=proc, kill=sinais) == 1
    assert sinais.enviados == []


def test_processo_que_ja_morreu_no_term_nao_quebra(tmp_path, proc):
    def kill(pid, sig):
        if sig == signal.SIGKILL:
            raise ProcessLookupError

    for _ in range(3):
        assert _rodar(tmp_path, {LIVE: None, READY: None}, proc=proc, kill=kill) == 1


def test_estado_corrompido_conta_do_zero(tmp_path, proc):
    (tmp_path / "estado.json").write_text("{lixo")
    assert _rodar(tmp_path, {LIVE: 200, READY: 200}, proc=proc) == 0


def test_get_devolve_status_ou_none(monkeypatch):
    import urllib.error

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(hc.urllib.request, "urlopen", lambda url, timeout: _Resp())
    assert hc.http_status("http://x") == 200

    def erro_http(url, timeout):
        raise urllib.error.HTTPError(url, 503, "x", None, None)

    monkeypatch.setattr(hc.urllib.request, "urlopen", erro_http)
    assert hc.http_status("http://x") == 503

    def sem_conexao(url, timeout):
        raise urllib.error.URLError("recusada")

    monkeypatch.setattr(hc.urllib.request, "urlopen", sem_conexao)
    assert hc.http_status("http://x") is None


def test_main_passa_os_argumentos_para_o_check(monkeypatch, tmp_path):
    recebido = {}
    monkeypatch.setattr(hc, "check", lambda **kw: recebido.update(kw) or 0)
    monkeypatch.setattr(
        "sys.argv", ["hc", "--max-falhas", "5", "--state", str(tmp_path / "e.json")]
    )
    assert hc.main() == 0
    assert recebido["max_falhas"] == 5
    assert recebido["live_url"].endswith("/health")
    assert recebido["ready_url"].endswith("/health/ready")


def test_processo_que_some_durante_a_leitura_e_ignorado(tmp_path):
    proc = _proc(tmp_path / "proc", {1: (0, 100), 7: (1, 250)})
    (proc / "8").mkdir()  # sem stat: sumiu entre o listdir e a leitura
    assert hc.processo_da_api(proc) == (7, "250")
