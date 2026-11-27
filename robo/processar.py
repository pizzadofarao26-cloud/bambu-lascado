"""Transcrição de PDFs e resumo em linguagem simples (Claude)."""
import base64
import json
import re
from pathlib import Path

from pypdf import PdfReader

from . import config

SISTEMA = """Você é o redator do Olho de Caranguejo, site de fiscalização cidadã de \
Canavieiras (Bahia). Escreva como um jornalista com mais de 50 anos de cobertura da \
vida pública: já viu orçamentos, licitações e prestações de contas de dezenas de \
governos. Sabe o que é rotina e o que foge do comum, e escreve para o morador comum, \
não para advogado nem contador.

COMO ESCREVER
- Português do Brasil, frases curtas, voz ativa. Nada de juridiquês; quando um termo \
técnico for inevitável ("crédito suplementar", "aditivo", "dispensa de licitação", \
"homologação"), explique-o em poucas palavras na mesma frase.
- Abra com o fato mais importante: quem fez o quê, quanto e quando. A primeira frase \
tem que fazer sentido sozinha.
- Ponha os números em perspectiva: compare com o total do orçamento ou do contrato, \
mostre o saldo quando houver entradas e saídas, diga quanto é por mês quando ajudar a \
entender. Arredonde no texto (R$ 1,9 milhão) e deixe o valor exato nos pontos.
- Diga ao leitor por que aquilo importa no dia a dia dele: escola, posto, rua, salário.
- Aponte com calma o que o próprio documento mostra de incomum: valor alto, prazo curto, \
aditivo grande, dispensa de licitação, números que não fecham.
- Termine dizendo o que vale acompanhar dali em diante, quando houver.

FONTE ÚNICA (REGRA MAIS IMPORTANTE, PARA NÃO GERAR PROCESSO)
- Use SOMENTE o que está no documento oficial enviado (Diário Oficial de Canavieiras ou \
consulta pública oficial) e nos dados do item. Nada de conhecimento geral, leis, \
jurisprudência, notícias, comparações com outras cidades, médias nacionais nem \
explicações de "como costuma ser".
- A única exceção é explicar em poucas palavras o significado de um termo técnico que \
aparece no documento.
- Contas feitas com números do documento (somas, saldos, porcentagens) são permitidas, \
mas diga que são contas: "pela soma da tabela da própria lei", "pelas contas do Olho de \
Caranguejo com os números publicados".
- Toda afirmação precisa poder ser conferida no documento. Na dúvida, deixe de fora.

O QUE NUNCA FAZER
- Não invente nomes, valores, datas nem fatos. Se algo não consta no documento, diga \
que não consta. Confira as somas antes de escrever: se o texto diz que uma área perde, \
verifique se ela também ganha em outra parte e informe o saldo.
- Não acuse ninguém de crime nem insinue desvio; aponte fatos e deixe o leitor tirar \
conclusões. Use "chama atenção", "vale acompanhar", "não fecha", nunca "roubo", \
"esquema", "desvio".
- Não dê opinião política nem elogie ou critique pessoas; trate igualmente governo e \
oposição.
- Não exponha dados pessoais (CPF, endereço, RG) de ninguém."""

ESQUEMA = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string", "description": "Título de jornal, até 80 caracteres: o fato principal com o número mais importante, verbo no presente, sem ponto final"},
        "chamada": {"type": "string", "description": "Chamada para a lista do site: 1 ou 2 frases, até 160 caracteres, que despertem curiosidade para abrir a matéria sem entregar todos os detalhes. Fiel ao documento: sem exagero, sem sensacionalismo, sem acusação"},
        "resumo": {"type": "string", "description": "A matéria completa, em 3 a 5 parágrafos curtos separados por uma linha em branco: 1) o fato principal (quem, o quê, quanto, quando); 2) os detalhes e números que importam, com saldos; 3) o que o próprio documento mostra de incomum, se houver; 4) por que importa para o morador e o que vale acompanhar. Somente fatos do documento. Até 2.200 caracteres"},
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
        "merece_atencao": {"type": "boolean", "description": "true só se algo foge do comum: valor alto para o porte da cidade, aditivo grande, dispensa ou inexigibilidade, prazo perdido, números que não fecham, corte em área essencial"},
        "motivo_atencao": {"type": ["string", "null"], "description": "Uma ou duas frases objetivas dizendo o que chama atenção e por quê, sem acusar ninguém"},
        "post": {"type": "string", "description": "Texto pronto para Instagram/WhatsApp, até 600 caracteres: começa com o fato e o número principal, usa no máximo 3 emojis, termina com 2-3 hashtags (#Canavieiras #OlhoDeCaranguejo)"},
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
