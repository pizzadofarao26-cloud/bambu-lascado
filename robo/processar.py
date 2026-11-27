"""Transcrição de PDFs e resumo em linguagem simples (Claude)."""
import base64
import json
import re
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
        "chamada": {"type": "string", "description": "Chamada para a lista do site: 1 ou 2 frases, até 160 caracteres, que despertem curiosidade para abrir a matéria sem entregar todos os detalhes. Fiel ao documento: sem exagero, sem sensacionalismo, sem acusação"},
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
    "required": ["titulo", "chamada", "resumo", "pontos", "merece_atencao", "motivo_atencao", "post"],
    "additionalProperties": False,
}


TRACO = r"[-‐-―−]"
CPF_FORMATADO = re.compile(rf"(?<![\d./])\d{{3}}\s?[.,]\s?\d{{3}}\s?[.,]\s?\d{{3}}\s*(?:{TRACO}|[./])\s*\d{{2}}(?![\d/])")
CPF_ROTULO = re.compile(rf"(CPF(?:/MF)?[^\d\n]{{0,25}}\n?\s*)(?:\d(?:[\s.,/]|{TRACO})*){{10}}\d(?!\d)", re.I)
CPF_SOLTO = re.compile(r"(?<![\d./-])\d{11}(?![\d/])")
OCULTO = "[CPF oculto]"


def _cpf_valido(n: str) -> bool:
    if len(set(n)) == 1:
        return False
    for k in (9, 10):
        if sum(int(n[i]) * (k + 1 - i) for i in range(k)) * 10 % 11 % 10 != int(n[k]):
            return False
    return True


def ocultar_cpf(texto: str) -> str:
    """Esconde CPFs (o Diário publica, mas o site não precisa expor ninguém). CNPJ fica."""
    texto = CPF_FORMATADO.sub(OCULTO, texto or "")
    texto = CPF_ROTULO.sub(lambda m: m[1] + OCULTO, texto)
    return CPF_SOLTO.sub(lambda m: OCULTO if _cpf_valido(m[0]) else m[0], texto)


def ocultar_em(obj):
    """Aplica ocultar_cpf em todos os textos, menos endereços e identificadores."""
    if isinstance(obj, str):
        return ocultar_cpf(obj)
    if isinstance(obj, list):
        return [ocultar_em(x) for x in obj]
    if isinstance(obj, dict):
        fixos = ("url", "arquivo", "id", "pdf", "numero", "cnpj", "vistos")
        return {k: v if k.startswith(fixos) else ocultar_em(v) for k, v in obj.items()}
    return obj


def extrair_texto(pdf: Path) -> tuple[str, int]:
    """Retorna (texto, nº de páginas). Texto vazio = PDF escaneado (imagem)."""
    leitor = PdfReader(str(pdf))
    paginas = [(p.extract_text() or "").strip() for p in leitor.pages]
    texto = "\n\n".join(f"[Página {i+1}]\n{t}" for i, t in enumerate(paginas) if t)
    return ocultar_cpf(texto), len(paginas)


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
