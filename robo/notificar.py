"""Alertas no celular via Telegram (grátis).

Como configurar (5 minutos):
1. No Telegram, fale com @BotFather, envie /newbot e copie o token -> TELEGRAM_BOT_TOKEN.
2. Mande qualquer mensagem para o seu bot novo.
3. Abra https://api.telegram.org/bot<TOKEN>/getUpdates e copie "chat":{"id": ...} -> TELEGRAM_CHAT_ID.
   (Para um canal público, adicione o bot como admin e use @nome_do_canal.)
"""
import html

import requests

from . import config

NOME_FONTE = {"diario": "Diário Oficial", "tcm": "TCM-BA", "emendas": "Emendas federais"}


def _texto(item: dict) -> str:
    ia = item.get("ia") or {}
    titulo = ia.get("titulo") or item.get("titulo_original", "").split("\n")[0][:120]
    resumo = ia.get("resumo") or item.get("titulo_original", "")[:400]
    destaque = "🚨 " if (ia.get("merece_atencao") or item.get("destaque")) else ""
    linhas = [f"{destaque}<b>{html.escape(NOME_FONTE.get(item['fonte'], item['fonte']))}</b>"
              + (f" · Ed. {item['edicao']}" if item.get("edicao") else ""),
              f"<b>{html.escape(titulo)}</b>", "", html.escape(resumo)]
    if ia.get("motivo_atencao"):
        linhas += ["", "⚠️ " + html.escape(ia["motivo_atencao"])]
    linhas += ["", f'<a href="{config.URL_SITE}/#{item["id"]}">Ler no Olho de Caranguejo</a>']
    return "\n".join(linhas)


def enviar(itens: list[dict]) -> int:
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID) or not itens:
        return 0
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    enviados = 0
    for item in itens[:15]:  # evita enxurrada na primeira execução
        r = requests.post(url, json={"chat_id": config.TELEGRAM_CHAT_ID, "text": _texto(item),
                                     "parse_mode": "HTML", "disable_web_page_preview": True},
                          timeout=30)
        enviados += r.ok
        if not r.ok:
            print("  ! Telegram:", r.status_code, r.text[:200])
    if len(itens) > 15:
        requests.post(url, json={"chat_id": config.TELEGRAM_CHAT_ID,
                                 "text": f"…e mais {len(itens) - 15} novidades no site: {config.URL_SITE}"},
                      timeout=30)
    return enviados


def teste() -> int:
    """python -m robo.notificar  ->  manda uma mensagem de teste para o canal."""
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        print("Faltam TELEGRAM_BOT_TOKEN e/ou TELEGRAM_CHAT_ID.")
        return 1
    base = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/"
    eu = requests.get(base + "getMe", timeout=30).json()
    print("Bot:", "@" + eu.get("result", {}).get("username", "?") if eu.get("ok") else eu.get("description"))
    r = requests.post(base + "sendMessage", json={
        "chat_id": config.TELEGRAM_CHAT_ID, "parse_mode": "HTML", "disable_web_page_preview": False,
        "text": "🦀 <b>Olho de Caranguejo</b> está de olho!\n\nA partir de agora, este canal avisa quando sair "
                "contrato, aditivo, licitação ou outra novidade importante no dinheiro público de Canavieiras.\n\n"
                f'<a href="{config.URL_SITE}">{config.URL_SITE.removeprefix("https://")}</a>'}, timeout=30)
    print("Mensagem:", "enviada" if r.ok else f"falhou ({r.status_code}): {r.json().get('description')}")
    return 0 if r.ok else 1


if __name__ == "__main__":
    raise SystemExit(teste())
