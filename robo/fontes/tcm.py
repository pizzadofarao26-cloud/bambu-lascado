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


def _abrir(page, tentativas: int = 3):
    """O e-TCM às vezes demora a responder (principalmente fora do Brasil): tenta de novo."""
    for n in range(1, tentativas + 1):
        try:
            page.goto(config.TCM_URL, wait_until="domcontentloaded", timeout=90000)
            return
        except PWTimeout:
            if n == tentativas:
                raise
            print(f"  TCM-BA demorou a responder, tentando de novo ({n}/{tentativas})…")
            page.wait_for_timeout(10000 * n)


def _pesquisar(page, unidade: str, ano: int):
    _abrir(page)
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


def publicar_documento(bruto: Path, destino: Path, nome: str) -> dict:
    """Tarja os CPFs e publica: PDF tarjado (se der para conferir) + texto com CPFs ocultos."""
    from .. import tarjar
    info = {"arquivo_local": None, "texto_local": None}
    try:
        if not bruto.read_bytes()[:5].startswith(b"%PDF"):
            info["nao_publicado"] = "arquivo não é PDF"
            return info
        pdf = destino / nome
        r = tarjar.tarjar(bruto, pdf)
        info["cpfs_tarjados"] = r["cpfs"]
        if r["publicar"]:
            info["arquivo_local"] = pdf.name
        else:
            pdf.unlink(missing_ok=True)
            info["nao_publicado"] = r["motivo"]
        if len(r["texto"]) > 200:
            txt = destino / (Path(nome).stem + ".txt")
            txt.write_text(r["texto"], encoding="utf-8")
            info["texto_local"] = txt.name
    except Exception as e:
        info["nao_publicado"] = f"erro ao processar: {str(e)[:120]}"
    return info


JS_PROXIMA_PAGINA = """
() => {
  const a = document.querySelector('a[id$=":downloadDocBinario"]');
  const nx = a && a.closest('.ui-datatable').querySelector('.ui-paginator-next');
  if (!nx || nx.classList.contains('ui-state-disabled')) return false;
  nx.click(); return true;
}
"""


def _interessa(d) -> bool:
    c = d["classificacao"]
    return bool(re.search(config.TCM_DOCS_PUBLICOS, c, re.I)) and not re.search(config.TCM_DOCS_PRIVADOS, c, re.I)


def _baixar_um(page, d: dict, arq: Path) -> bool:
    try:
        with page.expect_download(timeout=45000) as dl:
            page.click(f'[id="{d["link_id"]}"]')
        dl.value.save_as(arq)
        return True
    except PWTimeout:
        # Alguns documentos abrem numa nova aba em vez de baixar.
        try:
            with page.context.expect_page(timeout=15000) as nova:
                page.click(f'[id="{d["link_id"]}"]')
            p2 = nova.value
            p2.wait_for_load_state()
            r = page.context.request.get(p2.url)
            ok = r.ok and r.body().startswith(b"%PDF")
            if ok:
                arq.write_bytes(r.body())
            p2.close()
            return ok
        except PWTimeout:
            return False


def _baixar_documentos(page, prefixo: str, destino: Path, limite: int) -> list[dict]:
    """Percorre todas as páginas da lista de documentos (vem de 10 em 10; uma prestação
    mensal tem centenas). Baixa só os tipos de interesse público (contratos etc.), com
    CPFs tarjados; os demais (folha, pagamentos, extratos) entram só na lista."""
    import tempfile
    temp = Path(tempfile.mkdtemp(prefix="tcm_"))
    salvos, vistos, baixados = [], set(), 0
    for _ in range(300):
        for d in page.evaluate(JS_DOCUMENTOS):
            chave = (d["classificacao"], d["nome"])
            if chave in vistos:
                continue
            vistos.add(chave)
            info = {k: v for k, v in d.items() if k != "link_id"}
            if not _interessa(d):
                continue          # folha, pagamentos, extratos: só contamos (ficam no site do TCM)
            if baixados >= limite:
                salvos.append(info | {"arquivo_local": None, "texto_local": None, "nao_publicado": "limite desta rodada"})
                continue
            nome_seguro = re.sub(r"[^\w.\-]+", "_", d["nome"])[:80] or "documento.pdf"
            if not nome_seguro.lower().endswith(".pdf"):
                nome_seguro += ".pdf"
            arq = temp / f"{prefixo}_{nome_seguro}"
            if _baixar_um(page, d, arq):
                info |= publicar_documento(arq, destino, arq.name)   # nunca publica o arquivo bruto
                arq.unlink(missing_ok=True)
                baixados += 1
            else:
                info |= {"arquivo_local": None, "texto_local": None, "nao_publicado": "o TCM não entregou o arquivo"}
            salvos.append(info)
        if not page.evaluate(JS_PROXIMA_PAGINA):
            break
        _esperar(page, 1500)
    return salvos, len(vistos)


def documentos_pendentes(itens: list[dict], destino: Path, limite: int) -> int:
    """Baixa (e tarja) os documentos de prestações já conhecidas que ainda não têm documentos."""
    pend = {i["id"]: i for i in itens if i["fonte"] == "tcm" and not i.get("documentos")
            and int(i["competencia"][-4:]) >= config.TCM_DOCS_DESDE_ANO}
    if not pend:
        return 0
    feitos = 0
    with sync_playwright() as pw:
        nav = pw.chromium.launch(headless=True)
        ctx = nav.new_context(accept_downloads=True, user_agent=config.USER_AGENT, locale="pt-BR")
        page = ctx.new_page()
        page.set_default_timeout(60000)
        for unidade in config.TCM_UNIDADES:
            for ano in sorted({int(i["competencia"][-4:]) for i in pend.values()}, reverse=True):
                if feitos >= limite:
                    break
                _pesquisar(page, unidade, ano)
                for ln in page.evaluate(JS_LINHAS):
                    c = ln["celulas"]
                    if len(c) < 6 or _chave(unidade, c) not in pend or feitos >= limite:
                        continue
                    item = pend[_chave(unidade, c)]
                    try:
                        _pesquisar(page, unidade, ano)
                        page.locator('a[id$=":selecionarPrestacao"]').nth(ln["indice"]).click()
                        _esperar(page, 4000)
                        item["documentos"], item["documentos_total"] = _baixar_documentos(page, item["id"], destino, 60)
                        item.pop("erro_documentos", None)
                        feitos += 1
                        print(f"  {item['competencia']} {item['tipo']} {item['unidade'][:22]}: "
                              f"{len(item['documentos'])} documento(s)", flush=True)
                    except Exception as e:
                        item["erro_documentos"] = str(e)[:200]
        nav.close()
    return feitos


def coletar(ja_vistos: set[str], destino: Path) -> list[dict]:
    """Retorna as prestações de contas novas (não vistas), com documentos baixados."""
    destino.mkdir(parents=True, exist_ok=True)
    anos = [date.today().year, date.today().year - 1]
    novos, docs_restantes = [], config.MAX_DOCS_TCM_POR_EXECUCAO
    with sync_playwright() as pw:
        nav = pw.chromium.launch(headless=True)
        ctx = nav.new_context(accept_downloads=True, user_agent=config.USER_AGENT, locale="pt-BR")
        page = ctx.new_page()
        page.set_default_timeout(60000)
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
                    if docs_restantes > 0 and int(c[1][-4:]) >= config.TCM_DOCS_DESDE_ANO:
                        try:
                            _pesquisar(page, unidade, ano)
                            page.locator('a[id$=":selecionarPrestacao"]').nth(ln["indice"]).click()
                            _esperar(page, 4000)
                            item["documentos"], item["documentos_total"] = _baixar_documentos(page, chave, destino, docs_restantes)
                            docs_restantes -= sum(1 for d in item["documentos"] if d["arquivo_local"])
                        except Exception as e:  # o TCM às vezes falha; seguimos sem os PDFs
                            item["erro_documentos"] = str(e)[:200]
                    novos.append(item)
        nav.close()
    return novos
