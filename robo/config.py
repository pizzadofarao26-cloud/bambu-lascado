"""Configurações do robô Olho de Caranguejo. Tudo que é fixo de Canavieiras fica aqui."""
import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Lê chaves do arquivo .env (só no computador; no GitHub use Secrets). Nunca envie o .env ao GitHub.
_env = RAIZ / ".env"
if _env.exists():
    for _linha in _env.read_text(encoding="utf-8-sig").splitlines():
        if "=" in _linha and not _linha.lstrip().startswith("#"):
            _k, _v = _linha.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
PASTA_SITE = RAIZ / "site"
PASTA_ARQUIVOS = PASTA_SITE / "arquivos"      # PDFs baixados (servidos pelo site)
PASTA_DADOS = RAIZ / "dados"
ARQ_ESTADO = PASTA_DADOS / "estado.json"       # o que o robô já viu
ARQ_ITENS = PASTA_DADOS / "itens.json"         # todos os itens processados
ARQ_SITE = PASTA_SITE / "dados.json"           # o que o site lê

# --- Canavieiras (BA) ---
MUNICIPIO = "Canavieiras"
CODIGO_IBGE = "2906303"
CNPJ_PREFEITURA = "13817804000120"

# Diário Oficial (plataforma DIOF/IMAP)
DIARIO_API = "https://diof.io.org.br/api/"
DIARIO_COD_CLIENTE = 149
DIARIO_SITE = "https://diario.canavieiras.ba.gov.br"

# TCM-BA (e-TCM, consulta pública)
TCM_URL = "https://e.tcm.ba.gov.br/epp/ConsultaPublica/listView.seam"
TCM_MUNICIPIO = "CANAVIEIRAS"
TCM_UNIDADES = ["Prefeitura Municipal de CANAVIEIRAS", "Camara Municipal de CANAVIEIRAS"]

# Emendas federais
TRANSFEREGOV_API = "https://api.transferegov.gestao.gov.br/transferenciasespeciais/"
PORTAL_API = "https://api.portaldatransparencia.gov.br/api-de-dados/"
PORTAL_API_KEY = os.getenv("PORTAL_TRANSPARENCIA_KEY", "")   # opcional, grátis

# Palavras que deixam um ato "em destaque" e disparam alerta
PALAVRAS_ALERTA = [
    "contrato", "aditivo", "licitação", "licitacao", "pregão", "pregao", "dispensa",
    "inexigibilidade", "homologação", "homologacao", "emenda", "convênio", "convenio",
]

# Quanto olhar para trás em cada execução (o estado evita repetir)
DIAS_RETROATIVOS = int(os.getenv("DIAS_RETROATIVOS", "15"))
MAX_DOCS_TCM_POR_EXECUCAO = int(os.getenv("MAX_DOCS_TCM", "10"))
MAX_RESUMOS_POR_EXECUCAO = int(os.getenv("MAX_RESUMOS", "20"))
# PDFs do Diário são grandes (~2 MB cada) e têm link oficial; por padrão não guardamos.
# Na hospedagem paga, com espaço, ligue: GUARDAR_PDF_DIARIO=1
GUARDAR_PDF_DIARIO = os.getenv("GUARDAR_PDF_DIARIO", "0") == "1"

# IA (resumos em linguagem simples). Sem chave, o robô funciona sem resumos.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODELO_IA = os.getenv("MODELO_IA", "claude-opus-5-5")
ESFORCO_IA = os.getenv("ESFORCO_IA", "medium")   # medium: confere somas e escreve com mais cuidado

# Alertas no Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or "@olhodecaranguejo"   # canal público

# Endereço público do site (para links nos alertas)
URL_SITE = os.getenv("URL_SITE") or "https://olhodecaranguejo.com.br"

USER_AGENT = "OlhoDeCaranguejo/1.0 (fiscalizacao cidada de Canavieiras-BA)"
