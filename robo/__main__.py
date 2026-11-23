"""Execução diária do robô:  python -m robo  [--sem-tcm] [--sem-ia] [--sem-alerta]

1. Coleta novidades (Diário Oficial, TCM-BA, emendas federais)
2. Baixa os PDFs e transcreve
3. Resume em linguagem simples e escreve um post (se houver chave da IA)
4. Atualiza site/dados.json
5. Manda alerta no Telegram
"""
import argparse
import json
import sys
import traceback
import unicodedata
from datetime import datetime, timezone

from . import config, notificar, processar
from .fontes import diario, emendas


def _ler(arq, padrao):
    try:
        return json.loads(arq.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return padrao


def _gravar(arq, dados):
    arq.parent.mkdir(parents=True, exist_ok=True)
    arq.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")


def _sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")


def _destaque(item: dict) -> bool:
    texto = _sem_acento(item.get("titulo_original", ""))
    if item["fonte"] == "tcm":
        return "fora do prazo" in texto or "nao entregue" in texto
    if item["fonte"] == "emendas":
        return True
    return any(_sem_acento(p) in texto for p in config.PALAVRAS_ALERTA)


def _publicar_site(todos, erros):
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    site = [{k: v for k, v in i.items() if k != "transcricao"} | {"tem_transcricao": bool(i.get("transcricao"))}
            for i in todos]
    _gravar(config.ARQ_SITE, {"atualizado_em": agora, "erros": erros, "itens": site})
    return agora


def resumir_pendentes(n: int, fonte: str | None) -> int:
    """Gera resumos para itens antigos (sem baixar nada de novo, exceto o PDF do Diário se preciso)."""
    if not config.ANTHROPIC_API_KEY:
        print("Falta ANTHROPIC_API_KEY (no arquivo .env ou nas variáveis de ambiente).")
        return 1
    todos = _ler(config.ARQ_ITENS, [])
    pend = [i for i in todos if not i.get("ia") and (not fonte or i["fonte"] == fonte)]
    pend.sort(key=lambda i: i.get("data", ""), reverse=True)    # mais novos primeiro
    pend.sort(key=lambda i: not i.get("destaque"))              # destaques na frente
    erros, feitos = [], 0
    for item in pend[:n]:
        pdf, temporario = None, False
        if item["fonte"] == "diario":
            pdf = config.PASTA_ARQUIVOS / f"diario_{item['edicao']}_{item['arquivo_id']}.pdf"
            txt = config.PASTA_ARQUIVOS / f"{item['id']}.txt"
            if txt.exists():
                item["transcricao"] = txt.read_text(encoding="utf-8")
            if not pdf.exists():
                temporario = diario.baixar_pdf(item, pdf)
        elif item["fonte"] == "tcm":
            locais = [d["arquivo_local"] for d in item.get("documentos", []) if d.get("arquivo_local")]
            pdf = config.PASTA_ARQUIVOS / locais[0] if locais else None
            if pdf and pdf.exists() and not item.get("transcricao"):
                item["transcricao"], item["paginas"] = processar.extrair_texto(pdf)
        print(f"Resumindo {item['id']}…")
        try:
            item["ia"] = processar.resumir(item, pdf)
            feitos += bool(item["ia"])
        except Exception as e:
            traceback.print_exc()
            erros.append(f"IA {item['id']}: {e}")
        if temporario and not config.GUARDAR_PDF_DIARIO:
            pdf.unlink(missing_ok=True)
    _gravar(config.ARQ_ITENS, todos)
    _publicar_site(todos, erros)
    print(f"Pronto: {feitos} resumo(s), {len(erros)} erro(s), {len(pend) - feitos} ainda sem resumo.")
    return 1 if erros and not feitos else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sem-tcm", action="store_true", help="pula o TCM-BA (precisa de navegador)")
    ap.add_argument("--so-tcm", action="store_true", help="roda só o TCM-BA (para usar num computador no Brasil)")
    ap.add_argument("--sem-ia", action="store_true", help="não gera resumos")
    ap.add_argument("--sem-alerta", action="store_true", help="não manda Telegram")
    ap.add_argument("--resumir-pendentes", type=int, metavar="N", default=0,
                    help="só gera resumos para N itens já coletados que ainda não têm resumo")
    ap.add_argument("--fonte", choices=["diario", "tcm", "emendas"], help="limita --resumir-pendentes a uma fonte")
    args = ap.parse_args()
    if args.sem_ia:
        config.ANTHROPIC_API_KEY = ""
    if args.resumir_pendentes:
        return resumir_pendentes(args.resumir_pendentes, args.fonte)

    estado = _ler(config.ARQ_ESTADO, {"vistos": [], "execucoes": []})
    vistos = set(estado["vistos"])
    todos = _ler(config.ARQ_ITENS, [])
    config.PASTA_ARQUIVOS.mkdir(parents=True, exist_ok=True)
    erros, novos = [], []

    # 1. Coleta -----------------------------------------------------------
    coletores = [] if args.so_tcm else [("Diário Oficial", lambda: diario.listar_edicoes()),
                                        ("Emendas federais", emendas.coletar)]
    if not args.sem_tcm:
        def coletar_tcm():
            from .fontes import tcm  # importa aqui: se o navegador falhar, as outras fontes seguem
            return tcm.coletar(vistos, config.PASTA_ARQUIVOS)
        coletores.append(("TCM-BA", coletar_tcm))
    for nome, coletar in coletores:
        try:
            achados = [i for i in coletar() if i["id"] not in vistos]
            print(f"{nome}: {len(achados)} novidade(s)")
            novos += achados
        except Exception as e:
            traceback.print_exc()
            erros.append(f"{nome}: {e}")

    # 2-3. Baixar, transcrever, resumir ----------------------------------
    resumos = 0
    for item in novos:
        item["destaque"] = _destaque(item)
        item["coletado_em"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        pdf = None
        if item["fonte"] == "diario":
            pdf = config.PASTA_ARQUIVOS / f"diario_{item['edicao']}_{item['arquivo_id']}.pdf"
            try:
                if pdf.exists() or diario.baixar_pdf(item, pdf):
                    item["arquivo_local"] = pdf.name
            except Exception as e:
                erros.append(f"PDF edição {item['edicao']}: {e}")
        elif item["fonte"] == "tcm":
            locais = [d["arquivo_local"] for d in item.get("documentos", []) if d.get("arquivo_local")]
            pdf = config.PASTA_ARQUIVOS / locais[0] if locais else None
        if pdf and pdf.exists():
            try:
                item["transcricao"], item["paginas"] = processar.extrair_texto(pdf)
            except Exception as e:
                erros.append(f"Transcrição {item['id']}: {e}")
        if resumos < config.MAX_RESUMOS_POR_EXECUCAO:
            try:
                item["ia"] = processar.resumir(item, pdf)
                resumos += bool(item["ia"])
            except Exception as e:
                traceback.print_exc()
                erros.append(f"IA {item['id']}: {e}")
        # O PDF do Diário tem link oficial permanente: guardamos só a transcrição.
        if item["fonte"] == "diario" and pdf and pdf.exists() and not config.GUARDAR_PDF_DIARIO:
            pdf.unlink()
            item.pop("arquivo_local", None)

    # 4. Salvar e publicar dados do site --------------------------------
    for item in novos:
        vistos.add(item["id"])
    todos = novos + todos
    todos.sort(key=lambda i: i.get("data", ""), reverse=True)
    _gravar(config.ARQ_ITENS, todos)
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    estado["vistos"] = sorted(vistos)
    estado["execucoes"] = ([{"quando": agora, "novos": len(novos), "erros": erros}]
                           + estado["execucoes"])[:60]
    _gravar(config.ARQ_ESTADO, estado)
    site = [{k: v for k, v in i.items() if k != "transcricao"} | {"tem_transcricao": bool(i.get("transcricao"))}
            for i in todos]
    _gravar(config.ARQ_SITE, {"atualizado_em": agora, "erros": erros, "itens": site})
    # transcrições ficam em arquivos separados, para o site carregar só quando pedir
    for i in novos:
        if i.get("transcricao"):
            (config.PASTA_ARQUIVOS / f"{i['id']}.txt").write_text(i["transcricao"], encoding="utf-8")

    # 5. Alertas ----------------------------------------------------------
    primeira_vez = len(estado["execucoes"]) == 1
    if not args.sem_alerta and not primeira_vez:
        enviados = notificar.enviar([i for i in novos if i["destaque"]] or novos)
        print(f"Alertas enviados: {enviados}")

    print(f"Pronto: {len(novos)} novo(s), {resumos} resumo(s), {len(erros)} erro(s).")
    for e in erros:
        print("  -", e)
    # Uma fonte fora do ar não pode impedir o site de ser atualizado com as outras.
    # Só falha se TODAS as fontes falharem.
    return 1 if len([e for e in erros if ":" in e and e.split(":")[0] in
                     ("Diário Oficial", "Emendas federais", "TCM-BA")]) == len(coletores) else 0


if __name__ == "__main__":
    sys.exit(main())
