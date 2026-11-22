"""Coletor do Diário Oficial de Canavieiras.

O site diario.canavieiras.ba.gov.br é um app que busca os dados na API pública
https://diof.io.org.br/api/ (cod_cliente 149). Usamos a mesma API.
"""
import html
import re
from datetime import date, timedelta

import requests

from .. import config

CABECALHOS = {
    "User-Agent": config.USER_AGENT,
    "Content-Type": "application/json",
    "Origin": config.DIARIO_SITE,
    "Referer": config.DIARIO_SITE + "/",
}


def _limpar(texto_html: str) -> str:
    t = re.sub(r"<br\s*/?>|</div>|</p>", "\n", texto_html or "", flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t).replace("\xa0", " ")
    return "\n".join(l.strip() for l in t.splitlines() if l.strip())


def listar_edicoes(dias: int = config.DIAS_RETROATIVOS, busca: str | None = None) -> list[dict]:
    """Edições publicadas nos últimos `dias` (todas, ou só as que casam com `busca`)."""
    fim = date.today()
    ini = fim - timedelta(days=dias)
    itens, pagina = [], 1
    while True:
        corpo = {
            "cod_cliente": config.DIARIO_COD_CLIENTE,
            "dat_envio_ini": ini.isoformat(),
            "dat_envio_fim": fim.isoformat(),
            "edicao": None,
            "des_observacao": busca,
            "page": pagina,
            "itemsPerPage": 50,
        }
        r = requests.post(config.DIARIO_API + "diario-oficial/edicoes-anteriores-group",
                          json=corpo, headers=CABECALHOS, timeout=60)
        r.raise_for_status()
        dados = r.json()
        for grupo in dados.get("data", []):
            for el in grupo.get("elements", []):
                arquivo = el.get("des_arquivoa4")
                itens.append({
                    "id": f"diario-{el['cod_documento']}-{arquivo}",
                    "fonte": "diario",
                    "data": (el.get("dat_envio") or grupo["key"])[:10],
                    "edicao": str(el["cod_documento"]),
                    "titulo_original": _limpar(el.get("des_observacao", "")),
                    "url_pdf_origem": f"{config.DIARIO_API}diario-oficial/download/{arquivo}",
                    "url_pagina": f"{config.DIARIO_SITE}/edicoes-anteriores",
                    "arquivo_id": arquivo,
                })
        if pagina >= int(dados.get("pages") or 1):
            break
        pagina += 1
    return itens


def baixar_pdf(item: dict, destino) -> bool:
    r = requests.get(item["url_pdf_origem"], headers=CABECALHOS, timeout=180)
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        return False
    destino.write_bytes(r.content)
    return True
