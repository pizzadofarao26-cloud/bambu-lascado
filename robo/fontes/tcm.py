"""Coletor do e-TCM (TCM-BA), consulta pública de prestações de contas.

O e-TCM é um sistema JSF (PrimeFaces/RichFaces) sem API: precisa de um navegador
de verdade. Usamos Playwright (Chromium sem tela).

Para cada unidade de Canavieiras (Prefeitura e Câmara) e cada ano, lista as
prestações de contas (competência, tipo, data de envio, status: "Entregue no
prazo" / "Entregue fora do prazo"...) e, para as novas, baixa os documentos
publicados (PDFs).
"""
import re
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from .. import config

F = "consultaPublicaTabPanel:consultaPublicaPCSearchForm:"

JS_SELECIONAR = """
async ([campo, texto]) => {
  const f = 'consultaPublicaTabPanel:consultaPublicaPCSearchForm:';
  const s = document.getElementById(f + campo + '_input');
  const o = [...s.options].find(o => o.text.trim() === texto);
  if (!o) return false;
  const w = Object.values(PrimeFaces.widgets).find(w => w.id === f + campo);
  w.selectValue(o.value);
  return true;
}
"""

JS_LINHAS = """
() => [...document.querySelectorAll('tr[id^="consultaPublicaTabPanel:consultaPublicaDataTable:"]')]
  .map((tr, i) => {
    const c = [...tr.querySelectorAll('td')].map(td => {
      const d = td.querySelectorAll('div'); return (d.length ? d[d.length-1] : td).innerText.trim();
    });
    return {indice: i, celulas: c};
  })
"""

JS_DOCUMENTOS = """
() => [...document.querySelectorAll('tr[data-ri]')]
  .filter(tr => tr.querySelector('a[id$=":downloadDocBinario"]'))
  .map(tr => {
    const c = [...tr.querySelectorAll('td')].map(td => td.innerText.trim());
    const a = tr.querySelector('a[id$=":downloadDocBinario"]');
    return {classificacao: c[1] || '', nome: c[2] || '', usuario: c[3] || '', data: c[4] || '',
            link_id: a.id};
  })
"""


def _esperar(page, ms=2500):
    page.wait_for_timeout(ms)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PWTimeout:
        pass


def _pesquisar(page, unidade: str, ano: int):
    page.goto(config.TCM_URL, wait_until="networkidle")
    page.wait_for_function("() => window.PrimeFaces && Object.keys(PrimeFaces.widgets).length > 3")
    # A ordem importa: município recarrega a lista de unidades.
    if not page.evaluate(JS_SELECIONAR, ["municipio", config.TCM_MUNICIPIO]):
        raise RuntimeError("Município não encontrado no e-TCM")
    _esperar(page)
    if not page.evaluate(JS_SELECIONAR, ["unidadeJurisdicionada", unidade]):
        raise RuntimeError(f"Unidade não encontrada no e-TCM: {unidade}")
    _esperar(page)
    page.evaluate(JS_SELECIONAR, ["competenciaPCAno", str(ano)])
    _esperar(page, 1500)
    page.click(f'[id="{F}searchButton"]')
    _esperar(page, 4000)


def _chave(unidade, celulas) -> str:
    base = "-".join(re.sub(r"\W+", "", x.lower()) for x in [unidade] + celulas[1:5])
    return "tcm-" + base


def _baixar_documentos(page, prefixo: str, destino: Path, limite: int) -> list[dict]:
    docs = page.evaluate(JS_DOCUMENTOS)
    salvos = []
    for d in docs[:limite]:
        nome_seguro = re.sub(r"[^\w.\-]+", "_", d["nome"])[:80] or "documento.pdf"
        arq = destino / f"{prefixo}_{nome_seguro}"
        ok = False
        try:
            with page.expect_download(timeout=45000) as dl:
                page.click(f'[id="{d["link_id"]}"]')
            dl.value.save_as(arq)
            ok = True
        except PWTimeout:
            # Alguns documentos abrem numa nova aba em vez de baixar.
            try:
                with page.context.expect_page(timeout=15000) as nova:
                    page.click(f'[id="{d["link_id"]}"]')
                p2 = nova.value
                p2.wait_for_load_state()
                r = page.context.request.get(p2.url)
                if r.ok and r.body().startswith(b"%PDF"):
                    arq.write_bytes(r.body())
                    ok = True
                p2.close()
            except PWTimeout:
                pass
        d = {k: v for k, v in d.items() if k != "link_id"}
        d["arquivo_local"] = arq.name if ok else None
        salvos.append(d)
    return salvos


def coletar(ja_vistos: set[str], destino: Path) -> list[dict]:
    """Retorna as prestações de contas novas (não vistas), com documentos baixados."""
    destino.mkdir(parents=True, exist_ok=True)
    anos = [date.today().year, date.today().year - 1]
    novos, docs_restantes = [], config.MAX_DOCS_TCM_POR_EXECUCAO
    with sync_playwright() as pw:
        nav = pw.chromium.launch(headless=True)
        ctx = nav.new_context(accept_downloads=True, user_agent=config.USER_AGENT, locale="pt-BR")
        page = ctx.new_page()
        for unidade in config.TCM_UNIDADES:
            for ano in anos:
                _pesquisar(page, unidade, ano)
                linhas = page.evaluate(JS_LINHAS)
                for ln in linhas:
                    c = ln["celulas"]  # [vazio, competência, tipo, unidade, data envio, status]
                    if len(c) < 6:
                        continue
                    chave = _chave(unidade, c)
                    if chave in ja_vistos:
                        continue
                    item = {
                        "id": chave,
                        "fonte": "tcm",
                        "competencia": c[1], "tipo": c[2], "unidade": c[3],
                        "data_envio": c[4], "status": c[5],
                        "data": "-".join(reversed(c[4].split("/"))) if "/" in c[4] else str(date.today()),
                        "titulo_original": f"Prestação de contas {c[2]} {c[1]} - {c[3]}: {c[5]} (enviada em {c[4]})",
                        "url_pagina": config.TCM_URL,
                        "documentos": [],
                    }
                    if docs_restantes > 0:
                        try:
                            _pesquisar(page, unidade, ano)
                            page.locator('a[id$=":selecionarPrestacao"]').nth(ln["indice"]).click()
                            _esperar(page, 4000)
                            item["documentos"] = _baixar_documentos(page, chave, destino, docs_restantes)
                            docs_restantes -= sum(1 for d in item["documentos"] if d["arquivo_local"])
                        except Exception as e:  # o TCM às vezes falha; seguimos sem os PDFs
                            item["erro_documentos"] = str(e)[:200]
                    novos.append(item)
        nav.close()
    return novos
