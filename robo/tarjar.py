"""Tarja de CPF em PDFs do TCM antes de publicar no site.

Para cada PDF baixado do e-TCM:
- procura CPFs no texto de cada página (mesmas regras de processar.ocultar_cpf);
- cobre cada um com uma tarja preta de verdade (redação: o texto some do arquivo);
- confere de novo: se ainda houver CPF, ou se o PDF for escaneado (sem texto
  para conferir), o PDF NÃO é publicado e fica só o texto com os CPFs ocultos.

Uso manual:  python -m robo.tarjar arquivo.pdf [...]
"""
import re
import sys
from pathlib import Path

from . import processar

MIN_TEXTO_POR_PAGINA = 150   # abaixo disso a página é imagem (escaneada)


def _cpfs_na_pagina(texto: str) -> set[str]:
    """Trechos exatos do texto que são CPF (para procurar e tarjar)."""
    achados = set(m[0] for m in processar.CPF_FORMATADO.finditer(texto))
    for m in processar.CPF_ROTULO.finditer(texto):
        achados.add(m[0][len(m[1]):].strip())
    for m in processar.CPF_SOLTO.finditer(texto):
        if processar._cpf_valido(m[0]):
            achados.add(m[0])
    return {a for a in achados if a}


def _variantes(trecho: str) -> list[str]:
    """O texto extraído pode juntar/separar caracteres de jeito diferente do desenho."""
    v = {trecho, re.sub(r"\s+", "", trecho), re.sub(r"\s+", " ", trecho)}
    return [x for x in v if x]


def tarjar(origem: Path, destino: Path) -> dict:
    """Gera `destino` com os CPFs tarjados. Retorna {'publicar': bool, 'cpfs': n, 'motivo': str, 'texto': str}."""
    import pymupdf
    doc = pymupdf.open(origem)
    total, escaneadas = 0, 0
    for pag in doc:
        texto = pag.get_text()
        if len(texto.strip()) < MIN_TEXTO_POR_PAGINA and pag.get_images():
            escaneadas += 1
        for trecho in _cpfs_na_pagina(texto):
            for v in _variantes(trecho):
                areas = pag.search_for(v)
                for r in areas:
                    pag.add_redact_annot(r, fill=(0, 0, 0))
                if areas:
                    total += 1
                    break
        # reforço para escaneados: a leitura do texto pode errar o número (0→O, 5→s);
        # então também tarjamos as palavras com algarismos logo depois da palavra "CPF"
        palavras = pag.get_text("words")   # (x0, y0, x1, y1, palavra, bloco, linha, n)
        for i, w in enumerate(palavras):
            if not re.search(r"C\.?P\.?F", w[4], re.I):
                continue
            for w2 in palavras[i + 1:i + 6]:
                if re.search(r"\d", w2[4]):
                    pag.add_redact_annot(pymupdf.Rect(w2[:4]), fill=(0, 0, 0))
                    total += 1
                elif not re.fullmatch(r"[\W_nº°o]*|n|no|sob|o|MF|/MF|:", w2[4], re.I):
                    break
        # PIXELS: apaga também os pontos da imagem por baixo (documentos escaneados com texto por cima)
        tinha_tarja = bool(list(pag.annots(types=[pymupdf.PDF_ANNOT_REDACT])))
        pag.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS)
        if tinha_tarja:
            # a imagem tarjada volta sem compressão (5x maior): regrava em JPEG, como o original
            for im in pag.get_images(full=True):
                x, larg, alt = im[0], im[2], im[3]
                if larg * alt < 300 * 300 or "DCT" in doc.xref_get_key(x, "Filter")[1]:
                    continue
                pix = pymupdf.Pixmap(doc, x)
                if pix.alpha:
                    pix = pymupdf.Pixmap(pix, 0)
                if pix.n > 3:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                pag.replace_image(x, stream=pix.tobytes("jpeg", jpg_quality=75))
    doc.save(destino, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True)
    doc.close()

    # conferência final no arquivo já salvo
    conf = pymupdf.open(destino)
    texto_final = "\n\n".join(f"[Página {i + 1}]\n{p.get_text()}" for i, p in enumerate(conf))
    conf.close()
    sobrou = processar.ocultar_cpf(texto_final).count(processar.OCULTO)
    if escaneadas:
        motivo = f"{escaneadas} página(s) escaneada(s): não dá para conferir CPF"
    elif sobrou:
        motivo = f"{sobrou} CPF(s) não puderam ser tarjados"
    else:
        motivo = ""
    return {"publicar": not motivo, "cpfs": total, "motivo": motivo,
            "texto": processar.ocultar_cpf(texto_final)}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for a in sys.argv[1:]:
        p = Path(a)
        r = tarjar(p, p.with_name(p.stem + "_tarjado.pdf"))
        print(p.name, "| tarjados:", r["cpfs"], "| publicar:", r["publicar"], r["motivo"])
