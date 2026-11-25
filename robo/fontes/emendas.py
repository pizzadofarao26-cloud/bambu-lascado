"""Emendas e repasses federais para Canavieiras.

1. Transferegov - "Emendas Pix" (transferências especiais): API aberta, sem chave.
2. Portal da Transparência - convênios do município (precisa de chave grátis,
   variável PORTAL_TRANSPARENCIA_KEY). Se não houver chave, é pulado.
"""
from datetime import date

import requests

from .. import config


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def emendas_pix() -> list[dict]:
    r = requests.get(
        config.TRANSFEREGOV_API + "plano_acao_especial",
        params={"cnpj_beneficiario_plano_acao": f"eq.{config.CNPJ_PREFEITURA}",
                "order": "ano_emenda_parlamentar_plano_acao.desc"},
        headers={"User-Agent": config.USER_AGENT}, timeout=60)
    r.raise_for_status()
    itens = []
    for e in r.json():
        valor = _num(e.get("valor_custeio_plano_acao")) + _num(e.get("valor_investimento_plano_acao"))
        itens.append({
            "id": f"emenda-pix-{e['id_plano_acao']}",
            "fonte": "emendas",
            "data": f"{e.get('ano_emenda_parlamentar_plano_acao') or e.get('ano_plano_acao')}-01-01",
            "ano": e.get("ano_emenda_parlamentar_plano_acao"),
            "parlamentar": e.get("nome_parlamentar_emenda_plano_acao"),
            "numero_emenda": e.get("numero_emenda_parlamentar_plano_acao"),
            "valor": valor,
            "situacao": e.get("situacao_plano_acao"),
            "areas": e.get("codigo_descricao_areas_politicas_publicas_plano_acao"),
            "motivo_impedimento": e.get("motivo_impedimento_plano_acao"),
            "titulo_original": (
                f"Emenda Pix {e.get('numero_emenda_parlamentar_plano_acao')} de "
                f"{e.get('nome_parlamentar_emenda_plano_acao')}: R$ {valor:,.2f} "
                f"({e.get('situacao_plano_acao')})"),
            "url_pagina": "https://especiais.transferegov.sistema.gov.br/transferencia-especial/plano-acao/detalhe/"
                          f"{e['id_plano_acao']}/dados-basicos",
        })
    return itens


def convenios_portal() -> list[dict]:
    if not config.PORTAL_API_KEY:
        return []
    hoje = date.today()
    itens, pagina = [], 1
    while pagina <= 20:
        r = requests.get(
            config.PORTAL_API + "convenios",
            params={"codigoIBGE": config.CODIGO_IBGE,
                    "dataInicial": f"01/01/{hoje.year - 1}",
                    "dataFinal": hoje.strftime("%d/%m/%Y"),
                    "pagina": pagina},
            headers={"chave-api-dados": config.PORTAL_API_KEY, "User-Agent": config.USER_AGENT},
            timeout=60)
        r.raise_for_status()
        lote = r.json()
        if not lote:
            break
        for c in lote:
            objeto = (c.get("dimConvenio") or {}).get("objeto") or ""
            numero = (c.get("dimConvenio") or {}).get("numero") or c.get("id")
            itens.append({
                "id": f"convenio-{c.get('id')}",
                "fonte": "emendas",
                "data": (c.get("dataPublicacao") or c.get("dataInicioVigencia") or "")[:10],
                "valor": _num(c.get("valor")),
                "valor_liberado": _num(c.get("valorLiberado")),
                "situacao": c.get("situacao"),
                "orgao": ((c.get("orgao") or {}).get("nome")),
                "titulo_original": f"Convênio {numero}: {objeto}".strip(),
                "url_pagina": f"https://portaldatransparencia.gov.br/convenios/{c.get('id')}",
            })
        pagina += 1
    return itens


def coletar() -> list[dict]:
    return emendas_pix() + convenios_portal()
