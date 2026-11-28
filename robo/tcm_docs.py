"""Documentos do TCM-BA no site, com CPFs tarjados.

    python -m robo.tcm_docs [--limite 50]

1. Tarja os PDFs do TCM que já estavam no site e ainda não passaram pela tarja.
2. Baixa, tarja e publica os documentos das prestações de contas que ainda não têm.
Precisa rodar num computador no Brasil (o TCM bloqueia acessos de fora).
"""
import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from . import config
from .__main__ import _gravar, _ler, _publicar_site


def tarjar_existentes(itens: list[dict]) -> int:
    from .fontes.tcm import publicar_documento
    n = 0
    for i in itens:
        for d in i.get("documentos") or []:
            arq = d.get("arquivo_local")
            if not arq or "cpfs_tarjados" in d:
                continue
            atual = config.PASTA_ARQUIVOS / arq
            if not atual.exists():
                continue
            bruto = Path(tempfile.mkdtemp(prefix="tcm_")) / arq
            shutil.move(atual, bruto)
            d |= publicar_documento(bruto, config.PASTA_ARQUIVOS, arq)
            bruto.unlink(missing_ok=True)
            n += 1
            print(f"  tarjado: {arq[-60:]} ({d.get('cpfs_tarjados', 0)} CPF) "
                  f"{'publicado' if d['arquivo_local'] else 'só texto: ' + d.get('nao_publicado', '')}", flush=True)
    return n


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=50, help="quantas prestações baixar nesta rodada")
    args = ap.parse_args()
    itens = _ler(config.ARQ_ITENS, [])
    print(f"Já publicados e tarjados agora: {tarjar_existentes(itens)}")
    _gravar(config.ARQ_ITENS, itens)
    from .fontes import tcm
    try:
        feitos = tcm.documentos_pendentes(itens, config.PASTA_ARQUIVOS, args.limite)
    finally:
        _gravar(config.ARQ_ITENS, itens)              # salva o que deu, mesmo se o TCM cair no meio
        erros = _ler(config.ARQ_SITE, {}).get("erros", [])
        _publicar_site(itens, erros)
    print(f"Prestações com documentos baixados nesta rodada: {feitos}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
