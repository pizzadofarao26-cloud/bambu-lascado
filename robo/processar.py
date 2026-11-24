"""Transcrição de PDFs e resumo em linguagem simples (Claude)."""
import base64
import json
from pathlib import Path

from pypdf import PdfReader

from . import config

SISTEMA = """Você escreve para o site Olho de Caranguejo, que explica para moradores de \
Canavieiras (Bahia) o que a Prefeitura, a Câmara e o governo federal fazem com o \
dinheiro público. Escreva em português do Brasil, frases curtas, sem juridiquês. \
Quem lê não conhece termos como "termo aditivo" ou "homologação": explique numa \
frase quando aparecerem. Seja fiel ao documento: não invente nomes, valores, datas \
ou acusações. Se um valor ou nome não estiver no texto, diga que não consta. Não \
dê opinião política; aponte fatos que merecem atenção (valores altos, aditivos, \
prazos perdidos, dispensa de licitação)."""

ESQUEMA = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string", "description": "Título simples, até 90 caracteres"},
        "resumo": {"type": "string", "description": "2 a 4 frases explicando o que aconteceu"},
        "pontos": {
            "type": "array",
            "description": "Cada ato relevante: empresa/pessoa, valor e objeto, quando constarem",
            "items": {
                "type": "object",
                "properties": {
                    "tipo": {"type": "string", "enum": ["contrato", "aditivo", "licitacao", "dispensa",
                                                         "pessoal", "lei_decreto", "contas", "emenda", "outro"]},
                    "descricao": {"type": "string"},
                    "valor_reais": {"type": ["number", "null"]},
                    "empresa_ou_pessoa": {"type": ["string", "null"]},
                },
                "required": ["tipo", "descricao", "valor_reais", "empresa_ou_pessoa"],
                "additionalProperties": False,
            },
        },
        "merece_atencao": {"type": "boolean"},
        "motivo_atencao": {"type": ["string", "null"]},
        "post": {"type": "string", "description": "Texto pronto para Instagram/WhatsApp, até 600 caracteres, com 2-3 hashtags"},
    },
    "required": ["titulo", "resumo", "pontos", "merece_atencao", "motivo_atencao", "post"],
    "additionalProperties": False,
}


def extrair_texto(pdf: Path) -> tuple[str, int]:
    """Retorna (texto, nº de páginas). Texto vazio = PDF escaneado (imagem)."""
    leitor = PdfReader(str(pdf))
    paginas = [(p.extract_text() or "").strip() for p in leitor.pages]
    return "\n\n".join(f"[Página {i+1}]\n{t}" for i, t in enumerate(paginas) if t), len(paginas)


def _parece_escaneado(texto: str, n_paginas: int) -> bool:
    return n_paginas > 0 and len(texto) / n_paginas < 150


def resumir(item: dict, pdf: Path | None) -> dict | None:
    """Gera resumo, pontos e post. Sem chave da API, retorna None."""
    if not config.ANTHROPIC_API_KEY:
        return None
    import anthropic  # importa só quando necessário

    cliente = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    contexto = {k: v for k, v in item.items()
                if k not in ("transcricao", "ia", "documentos") and not k.startswith("url")}
    conteudo = []
    instrucao = ("Dados do item coletado (JSON):\n" + json.dumps(contexto, ensure_ascii=False) +
                 "\n\nExplique este item para os moradores de Canavieiras.")

    if pdf and pdf.exists():
        texto = item.get("transcricao") or ""
        if texto and not _parece_escaneado(texto, item.get("paginas", 1)):
            conteudo.append({"type": "text", "text": "Texto do documento oficial:\n\n" + texto})
        elif pdf.stat().st_size < 30 * 1024 * 1024:
            # PDF escaneado: o Claude lê a imagem das páginas e também transcreve.
            conteudo.append({"type": "document", "source": {
                "type": "base64", "media_type": "application/pdf",
                "data": base64.standard_b64encode(pdf.read_bytes()).decode()}})
            instrucao += " O documento está escaneado; leia as páginas com atenção."
    conteudo.append({"type": "text", "text": instrucao})

    resposta = cliente.beta.messages.create(
        model=config.MODELO_IA,
        max_tokens=8000,
        system=SISTEMA,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": config.ESFORCO_IA,
                       "format": {"type": "json_schema", "schema": ESQUEMA}},
        messages=[{"role": "user", "content": conteudo}],
    )
    if resposta.stop_reason in ("refusal", "max_tokens"):
        print(f"  ! IA não concluiu ({resposta.stop_reason}) para {item['id']}")
        return None
    texto = next((b.text for b in resposta.content if b.type == "text"), "")
    return json.loads(texto)
