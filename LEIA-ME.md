# Olho de Caranguejo

Site de fiscalização cidadã de Canavieiras (BA). Um robô visita as fontes oficiais
todo dia às 6h, baixa os documentos, transcreve, resume em linguagem simples,
atualiza o site e manda alerta no Telegram.

## O que o robô acompanha

| Fonte | Como lê | O que guarda |
|---|---|---|
| Diário Oficial de Canavieiras | API pública `diof.io.org.br` (cliente 149) | Todas as edições, transcrição do PDF, link para o PDF oficial |
| TCM-BA (e-TCM) | Navegador automático (Playwright) | Prestações de contas da Prefeitura e da Câmara, status (no prazo / fora do prazo) e PDFs enviados |
| Emendas Pix (Transferegov) | API aberta, filtrada pelo CNPJ 13.817.804/0001-20 | Emendas, parlamentar autor, valor, situação |
| Portal da Transparência (opcional) | API com chave grátis | Convênios federais do município (IBGE 2906303) |

## Estrutura

```
robo/            o robô (python -m robo)
  fontes/        um coletor por fonte
  processar.py   transcrição do PDF + resumo com IA (Claude)
  notificar.py   alertas no Telegram
site/            o site (HTML estático) + dados.json + arquivos/
dados/           memória do robô (o que já viu)
.github/workflows/robo.yml   agenda diária no GitHub Actions
```

## Colocar no ar de graça (GitHub)

1. Crie uma conta em github.com e um repositório novo (pode ser público).
2. Envie esta pasta para o repositório.
3. Em **Settings > Pages**, em "Source", escolha **GitHub Actions**.
4. Em **Settings > Secrets and variables > Actions**, cadastre:
   - `ANTHROPIC_API_KEY`: chave da API do Claude (console.anthropic.com), para os resumos. Sem ela o site funciona, só que sem resumos.
   - `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`: para os alertas (veja abaixo).
   - `PORTAL_TRANSPARENCIA_KEY` (opcional): portaldatransparencia.gov.br/api-de-dados/cadastrar-email
   - (Opcional) Em **Variables**, `URL_SITE` só se o endereço do site mudar; o padrão é `https://olhodecaranguejo.com.br`.
5. Na aba **Actions**, abra "Robô diário" e clique **Run workflow** para a primeira execução.

O site fica em `https://SEU-USUARIO.github.io/NOME-DO-REPO/`. Quando comprar o domínio,
em **Settings > Pages > Custom domain** coloque `olhodecaranguejo.com.br` e crie no registro do
domínio os apontamentos DNS que o GitHub mostrar. Não precisa de hospedagem paga.

## Alertas no Telegram

1. No Telegram, fale com **@BotFather**, envie `/newbot` e copie o token.
2. Crie um canal (ex.: "Olho de Caranguejo"), adicione o bot como administrador.
3. `TELEGRAM_CHAT_ID` = `@nome_do_canal`. Quem entrar no canal recebe os avisos no celular.

A primeira execução não manda alertas (para não disparar dezenas de mensagens antigas).

## Rodar no computador

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium
.venv\Scripts\python -m robo            # tudo
.venv\Scripts\python -m robo --sem-tcm  # mais rápido, sem o TCM
```

Ajustes por variável de ambiente: `DIAS_RETROATIVOS` (padrão 15), `MAX_RESUMOS` (20),
`MAX_DOCS_TCM` (10), `GUARDAR_PDF_DIARIO=1` (guarda os PDFs do Diário, ~2 MB cada),
`MODELO_IA`, `ESFORCO_IA`.

## Custos

- GitHub Actions + Pages: grátis para repositório público.
- Resumos com IA: pagos por uso na API da Anthropic. São ~1 a 3 edições por dia útil;
  `MAX_RESUMOS` limita quantos resumos por dia. Para gastar menos, use
  `MODELO_IA=claude-haiku-5-5`.
- Domínio .com ou .com.br: anual, no registrador.

## Cuidados

- Os resumos são feitos por IA e podem errar. O site sempre mostra o texto original e o
  link do documento oficial; confira antes de publicar um post.
- Se o Diário, o TCM ou o Transferegov mudarem o site, o coletor daquela fonte pode
  parar. As outras continuam, e o erro aparece no log do GitHub Actions e em `dados/estado.json`.
