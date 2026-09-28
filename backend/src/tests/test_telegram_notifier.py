# src/tests/test_telegram_notifier.py
"""
TelegramNotifier com HTTP mockado (TIE-12).

A API do Telegram exige o token na URL (`/bot<token>/sendMessage`), e o
`HTTPStatusError` do httpx carrega a URL na mensagem — que vai parar no
traceback do worker. O erro precisa sair sem o token (mesma classe do TIE-3).
"""

import json

import httpx
import pytest

from src.infrastructure.telegram import notifier as notifier_mod
from src.infrastructure.telegram.notifier import TelegramError, TelegramNotifier

TOKEN = "123456:segredo-do-bot"


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setattr(notifier_mod.settings, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(notifier_mod.settings, "TELEGRAM_CHAT_ID", "-100200")


def _notifier(handler) -> TelegramNotifier:
    return TelegramNotifier(transport=httpx.MockTransport(handler))


def test_envia_mensagem_para_o_chat_configurado():
    requests: list[httpx.Request] = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    _notifier(handler).send("📈 oi")

    (req,) = requests
    assert req.url.path == f"/bot{TOKEN}/sendMessage"
    assert json.loads(req.content) == {
        "chat_id": "-100200",
        "text": "📈 oi",
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }


@pytest.mark.parametrize("status", [400, 401, 429, 500])
def test_erro_http_nao_vaza_o_token(status):
    with pytest.raises(TelegramError) as exc:
        _notifier(lambda r: httpx.Response(status)).send("x")

    assert str(status) in str(exc.value)
    assert TOKEN not in str(exc.value)
    # Nem pela cadeia de exceções, que o traceback também imprime
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


def test_erro_de_rede_nao_vaza_o_token():
    def handler(request):
        raise httpx.ConnectError(f"falhou {request.url}", request=request)

    with pytest.raises(TelegramError) as exc:
        _notifier(handler).send("x")
    assert TOKEN not in str(exc.value)


def test_sem_config_falha_rapido(monkeypatch):
    monkeypatch.setattr(notifier_mod.settings, "TELEGRAM_CHAT_ID", None)
    with pytest.raises(RuntimeError, match="TELEGRAM_CHAT_ID"):
        TelegramNotifier()
