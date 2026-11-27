"""Lê os "Extratos de Contrato" publicados no Diário Oficial e monta a lista de contratos.

Cada extrato traz número, objeto, empresa, valor e prazo. Juntamos o contrato com os
termos aditivos dele: o prazo que vale é o do ato mais recente. O que não está escrito
no extrato fica vazio (nada é adivinhado). CPFs nunca são guardados.

    python -m robo.contratos --dias 700     # monta o histórico (baixa PDFs antigos)
"""
import argparse
import calendar
import json
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

from . import config, processar
from .fontes import diario

ARQ_ATOS = config.PASTA_DADOS / "atos_contratos.json"   # um registro por extrato publicado
ARQ_SITE = config.PASTA_SITE / "contratos.json"         # contratos já agrupados, lido pelo site

MESES = {m: i + 1 for i, m in enumerate(
    "janeiro fevereiro março abril maio junho julho agosto setembro outubro novembro dezembro".split())}
MESES["marco"] = 3
DATA = r"(\d{1,2})\s*[°º]?\s*(?:/|\.)\s*(\d{1,2})\s*(?:/|\.)\s*(\d{4})|(\d{1,2})\s*[°º]?\s*(?:\([^)]*\)\s*)?(?:dias\s+)?(?:do\s+mês\s+)?d[eo]\s+([a-zç]+)\s+(?:de\s+)?(\d{4})"
INICIO = re.compile(r"EXTRATO\s+D[EO]S?\s+(?:[\wº°ªÚú.]+\s+){0,6}?CONTRATO|EXTRATO\s+D[EO]\s+(?:\d+\s*[º°]?\s*)?TERMO\s+ADITIVO")
ASSINATURA = re.compile(r"Canavieiras\s*(?:[,/\-–(]\s*(?:Bahia|BA)\)?)?\s*[,.\-–]?\s*(?:em\s+)?(" + DATA + ")", re.I)


def _datas(texto):
    out = []
    for m in re.finditer(DATA, texto, re.I):
        d, mes, a = (m[1], m[2], m[3]) if m[1] else (m[4], MESES.get(m[5].lower()), m[6])
        try:
            d, mes, a = int(d), int(mes), int(a)
        except (TypeError, ValueError):
            continue
        if 1 <= d <= 31 and 1 <= mes <= 12 and 2000 <= a <= 2060:
            d = min(d, calendar.monthrange(a, mes)[1])      # o Diário às vezes escreve "31/04"
            out.append(f"{a}-{mes:02d}-{d:02d}")
    return out


def _sem_cpf(t):
    t = t.replace("[CPF oculto]", "")
    t = re.sub(r"\d{3}\.\d{3}\.\d{3}\s*[-‐–]\s*\d{2}", "", t)
    t = re.sub(r"(?<!\d)\d{11}(?!\d)", "", t)          # MEI costuma ter o CPF no nome
    return re.sub(r"\s+", " ", t).strip(" .,;:-–[(")


def _limpar_paginas(t):
    """Tira cabeçalhos repetidos de página, que cortam os extratos no meio.
    Devolve o texto limpo e onde começa cada página nele: [(posição, nº da página)]."""
    partes = re.split(r"\[Página (\d+)\]\n", t)
    limpo, inicios = partes[0], [(0, 1)]
    for n, pag in zip(partes[1::2], partes[2::2]):
        pag = re.sub(r"^.*?Esta edição encontra-se no site oficial deste ente\.", " ", pag, count=1, flags=re.S)
        pag = re.sub(r"Pra?ç[a.]* da Bandeira.{0,90}?\d{4}-\d{4}", " ", pag, flags=re.S)
        limpo += " "
        inicios.append((len(limpo), int(n)))
        limpo += pag
    return limpo, inicios


def _pagina(inicios, pos):
    return next(n for p, n in reversed(inicios) if p <= pos)


def _valor(t):
    m = re.search(r"VALOR(?:\s+(?:GLOBAL|TOTAL|ESTIMADO|DO|DA|DE|CONTRATO|CONTRATADO|ADITIVO|MENSAL|ANUAL))*[^R]{0,50}R\$\s*(\d{1,3}(?:\.\d{3})*,\d{2})(?![\d,.]\d)", t, re.I)
    if not m or re.search("MENSAL", m[0], re.I):
        return None
    return float(m[1].replace(".", "").replace(",", "."))


def _campo(t, rotulo, fim):
    m = re.search(rf"\b(?:{rotulo})\s*:?\s*(.+?)(?=\s*(?:{fim}))", t, re.I | re.S)
    return re.sub(r"\s+", " ", m[1]).strip(" .,;:-–") if m else None


CNPJ = r"(\d{2}\.?\d{3}\.?\d{3}\s*/\s*\d{4}\s*[-‐–]\s*\d{2})"
FIM_EMP = r"(?=\s*[,.;(]?\s*(?:PESSOA JUR|INSCRIT|CNPJ|CPF|COM SEDE|SEDIAD|SITUAD|ENDERE|RESID|RESIED|NESTE ATO|REPRESENTAD|VALOR|VIG[ÊE]N|PRAZO|RG\b|OBJETO|DORAVANTE))"


def _empresa(plano):
    """Empresa e CNPJ, só se estiverem claramente no campo certo do extrato."""
    m = (re.search(r"PARTES\s*:\s*(?:O\s+)?(?:PREFEITURA|MUNIC[IÍ]PIO).{0,60}?CANAVIEIRAS\s*[-–,/]?\s*(?:BAHIA|BA\b)?\s*(?:,?\s*E\s+)?(?:A\s+)?(?:EMPRESA\s*:?\s+)?(.{3,110}?)" + FIM_EMP, plano, re.I)
         or re.search(r"(?:CONTRATAD[AO]|CREDENCIAD[AO])\s*:\s*(?:A\s+)?(?:EMPRESA\s*:?\s+)?(?:CONTRATAD[AO]\s*:\s*)?(.{3,110}?)" + FIM_EMP, plano, re.I))
    if not m or re.search(r"PREFEITURA|CONTRATANTE|OBJETO|MUNIC[IÍ]PIO DE", m[1], re.I):
        return None, None
    emp = _sem_cpf(m[1])
    c = re.search(CNPJ, plano[m.end():m.end() + 130])
    cnpj = None
    if c:
        n = re.sub(r"\D", "", c[1])
        if len(n) == 14 and n[:8] != config.CNPJ_PREFEITURA[:8]:
            cnpj = f"{n[:2]}.{n[2:5]}.{n[5:8]}/{n[8:12]}-{n[12:]}"
    return (emp if len(emp) > 2 else None), cnpj


def ler_extratos(texto: str, item: dict) -> list[dict]:
    t, paginas = _limpar_paginas(texto)
    ini = [m.start() for m in INICIO.finditer(t)]
    atos = []
    for n, a in enumerate(ini):
        bloco = t[a:min(a + 3500, ini[n + 1] if n + 1 < len(ini) else len(t))]
        bloco = re.split(r"TERMO DE (?:HOMOLOGA|RATIFICA|AUTORIZA)|HOMOLOGO E RATIFICO|AVISO DE|PUBLICAÇÃO DO EXTRATO", bloco)[0]
        # extratos sem data no final terminam no nome do prefeito
        pref = re.search(r"PAULO\s+C[EÉ][ZS]AR\s+RAMOS\s+CARVALHO\s*[–-]\s*PREFEITO", bloco)
        if pref:
            bloco = bloco[:pref.end()]
        ass = ASSINATURA.search(bloco, 60)
        # corta depois da assinatura: o que vem depois já é outro ato
        if ass and ass.start() > 250:
            bloco = bloco[:ass.end()]
        plano = re.sub(r"\s+", " ", bloco)
        if re.search(r"ONDE SE LÊ|retifica[çr]", plano, re.I):
            continue                                        # errata: exige leitura humana
        num = re.search(r"(?:CONTRATO|CONTRATO ADMINISTRATIVO)\s*(?:N[º°.ºoe]*\s*)?:?\s*(\d{1,4})\s*(/?)\s*(20\d{2})\b", plano, re.I)
        if not num:
            continue
        numero = num[1]
        if not num[2] and len(numero) == 4 and numero.endswith("1"):
            numero = numero[:-1]                            # a barra virou "1" na leitura do escaneado
        aditivo = re.search(r"(\d+)\s*[º°oª]?\s*(?:TERMO\s+)?(?:DE\s+)?(?:TERMO\s+)?ADITIVO", plano[:260], re.I)
        eh_aditivo = bool(aditivo) or "ADITIVO" in plano[:260].upper()
        fim_campo = r"CONTRATAD[AO]\s*:|CONTRATANTE\s*:|VALOR|VIG[ÊE]NCIA|PRAZO|AMPARO|FUNDAMENTO|DOTAÇÃO|PROCESSO\s+ADM|$"
        objeto = _campo(plano, r"OBJETO", fim_campo)
        emp, cnpj = _empresa(plano)
        # fim da vigência: última data do trecho de prazo, antes da assinatura
        corpo = bloco[:ass.start()] if ass else bloco
        corpo = re.sub(r"\s+", " ", corpo)
        fim_vig = None
        m = re.search(r"(?:VIG[ÊE]NCIA|PRAZO)(?! DE ENTREGA)[^:]{0,60}?(?::|SERÁ|DE\s)(.{0,330})", corpo[100:], re.I)
        if m:
            trecho = re.split(r"FUNDAMENTO|AMPARO|DOTAÇÃO|VALOR", m[0], flags=re.I)[0]
            ate = re.search(r"(?:até|\ba\b|término|findando|encerra\w*)(.{0,80})", trecho, re.I)
            d = _datas(trecho)
            if d and (ate or len(d) > 1):
                fim_vig = max(d)
        data_ass = (_datas(ass[1]) or [None])[0] if ass else None
        if data_ass and data_ass > item["data"]:
            data_ass = None                                 # data impossível (erro de digitação)
        if fim_vig and fim_vig < (data_ass or "2000"):
            fim_vig = None
        atos.append({
            "numero": f"{int(numero):03d}/{num[3]}",
            "tipo": "aditivo" if eh_aditivo else "contrato",
            "ordem": int(aditivo[1]) if aditivo else None,
            "objeto": _sem_cpf(objeto)[:500] if objeto else None,
            "empresa": emp, "cnpj": cnpj,
            "valor": _valor(corpo), "fim": fim_vig, "assinatura": data_ass,
            "modalidade": next((nome for pad, nome in (
                (r"DISPENSA", "Dispensa de licitação"), (r"INEXIGIBILIDADE", "Inexigibilidade"),
                (r"PREG[ÃA]O", "Pregão"), (r"CONCORR[ÊE]NCIA", "Concorrência"),
                (r"CREDENCIAMENTO|CHAMADA P", "Credenciamento"), (r"TOMADA DE PRE", "Tomada de preços"),
                (r"ADES[ÃA]O", "Adesão a ata")) if re.search(pad, plano, re.I)), None),
            "publicado": item["data"], "edicao": item["edicao"],
            "pdf": item["url_pdf_origem"], "pagina": _pagina(paginas, a), "item": item["id"],
        })
    return atos


def _nome(s):
    s = unicodedata.normalize("NFD", (s or "").upper())
    return re.sub(r"[^A-Z0-9]|(?:LTDA|EIRELI|ME|EPP)", "", s)


def _mesma(grupo, a):
    raizes = {x["cnpj"][:10] for x in grupo if x["cnpj"]}
    nomes = {_nome(x["empresa"]) for x in grupo if x["empresa"]}
    return (bool(a["cnpj"]) and a["cnpj"][:10] in raizes) or _nome(a["empresa"]) in nomes         or not (a["empresa"] or a["cnpj"])


def agrupar(atos: list[dict]) -> list[dict]:
    grupos = {}
    for a in sorted(atos, key=lambda a: (a["assinatura"] or a["publicado"], a["publicado"])):
        raiz = (a["cnpj"] or "")[:10]
        # numeração se repete entre órgãos: só junta se for a mesma empresa
        chave = next((k for k in grupos if k[0] == a["numero"] and _mesma(grupos[k], a)), None)
        if chave is None:
            chave = (a["numero"], raiz if raiz or not any(k[0] == a["numero"] for k in grupos) else _nome(a["empresa"]))
            grupos[chave] = []
        # o mesmo extrato pode ser republicado
        if not any(x["tipo"] == a["tipo"] and x["ordem"] == a["ordem"] and x["fim"] == a["fim"]
                   and x["valor"] == a["valor"] and x["tipo"] == "aditivo" for x in grupos[chave]):
            grupos[chave].append(a)
    out = []
    for (numero, raiz), lista in grupos.items():
        orig = next((a for a in lista if a["tipo"] == "contrato"), None)

        def ult(campo):
            return next((a[campo] for a in reversed(lista) if a[campo]), None)
        # prazo: o ato mais recente que informa prazo
        out.append({
            "id": f"contrato-{numero.replace('/', '-')}" + (f"-{raiz.replace('.', '')}" if raiz else ""),
            "numero": numero, "empresa": (orig or {}).get("empresa") or ult("empresa"),
            "cnpj": ult("cnpj"), "objeto": (orig or {}).get("objeto") or ult("objeto"),
            "modalidade": (orig or {}).get("modalidade") or ult("modalidade"),
            "valor": (orig or {}).get("valor") or ult("valor"),
            "valor_de": "contrato" if (orig or {}).get("valor") else ("aditivo" if ult("valor") else None),
            "fim": lista[-1]["fim"] if lista[-1]["tipo"] == "aditivo" else ult("fim"), "assinatura": (orig or {}).get("assinatura"),
            "ultimo_ato": max(a["publicado"] for a in lista),
            "atos": [{k: a[k] for k in ("tipo", "ordem", "valor", "fim", "assinatura", "publicado", "edicao", "pdf", "pagina", "item")}
                     for a in lista],
        })
    out.sort(key=lambda c: (c["ultimo_ato"], c["numero"]), reverse=True)
    return out


def _ler(arq, padrao):
    try:
        return json.loads(arq.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return padrao


def atualizar(itens: list[dict], textos: dict[str, str]) -> int:
    """Relê os extratos das edições em `textos` (id -> transcrição) e regrava a lista."""
    base = _ler(ARQ_ATOS, {"lidos": [], "atos": []})
    atos = [a for a in base["atos"] if a["item"] not in textos]
    for i in itens:
        if i["id"] in textos:
            atos += ler_extratos(textos[i["id"]], i)
    base["lidos"] = sorted(set(base["lidos"]) | set(textos))
    base["atos"] = atos
    contratos = agrupar(atos)
    ARQ_ATOS.write_text(json.dumps(base, ensure_ascii=False, indent=1), encoding="utf-8")
    ARQ_SITE.write_text(json.dumps({"desde": min((a["publicado"] for a in atos), default=None),
                                    "contratos": contratos}, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(contratos)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=0, help="quantos dias para trás baixar do Diário")
    ap.add_argument("--cache", default="", help="pasta para guardar transcrições antigas (reuso em testes)")
    args = ap.parse_args()
    itens = [i for i in _ler(config.ARQ_ITENS, []) if i["fonte"] == "diario"]
    textos = {}
    for i in itens:
        f = config.PASTA_ARQUIVOS / f"{i['id']}.txt"
        if f.exists():
            textos[i["id"]] = f.read_text(encoding="utf-8")
    falhas = 0
    if args.dias:
        cache = Path(args.cache or tempfile.mkdtemp())
        cache.mkdir(parents=True, exist_ok=True)
        for i in diario.listar_edicoes(dias=args.dias):
            if i["id"] in textos or not re.search(r"contrat|aditiv|extrato|dispensa|inexig", i["titulo_original"], re.I):
                continue
            itens.append(i)
            f = cache / f"{i['id']}.txt"
            if not f.exists():
                pdf = cache / "tmp.pdf"
                try:
                    if not diario.baixar_pdf(i, pdf):
                        raise RuntimeError("não é PDF")
                    f.write_text(processar.extrair_texto(pdf)[0], encoding="utf-8")
                except Exception as e:
                    falhas += 1
                    print("falhou", i["data"], i["edicao"], str(e)[:100])
                    continue
                print(i["data"], i["edicao"], flush=True)
            textos[i["id"]] = f.read_text(encoding="utf-8")
    print(f"{atualizar(itens, textos)} contrato(s), {falhas} edição(ões) com falha")


if __name__ == "__main__":
    sys.exit(main())
