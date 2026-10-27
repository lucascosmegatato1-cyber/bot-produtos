import os
import json
import time
import uuid
import hmac
import struct
import zlib
import threading
from functools import wraps

import requests
from flask import Flask, request, jsonify, Response

app = Flask(__name__)

CHAVE_GOOGLE = os.environ.get("CHAVE_GOOGLE", "")
YAMPI_ALIAS = os.environ.get("YAMPI_ALIAS", "")
YAMPI_USER_TOKEN = os.environ.get("YAMPI_USER_TOKEN", "")
YAMPI_SECRET_KEY = os.environ.get("YAMPI_SECRET_KEY", "")
SENHA_APP = os.environ.get("SENHA_APP", "")
MARGEM_LUCRO = float(os.environ.get("MARGEM_LUCRO", "2.2"))
NOME_MARCA = os.environ.get("NOME_MARCA", "Titia Flora")
MODELO_PREFERIDO = os.environ.get("MODELO_PREFERIDO", "gemini-3.8-flash")
PUBLICAR_DIRETO = os.environ.get("PUBLICAR_DIRETO", "nao").lower() == "sim"

BASE_GEMINI = "https://generativelanguage.googleapis.com/v1beta"
BASE_YAMPI = "https://api.dooki.com.br/v2/" + YAMPI_ALIAS + "/catalog"
CAB_IA = {"x-goog-api-key": CHAVE_GOOGLE, "Content-Type": "application/json"}
CAB_YAMPI = {
    "User-Token": YAMPI_USER_TOKEN,
    "User-Secret-Key": YAMPI_SECRET_KEY,
    "Content-Type": "application/json",
}
CERCA = chr(96) * 3

JOBS = {}
MODELOS = []


# ---------- seguranca ----------
def senha_ok():
    auth = request.authorization
    if not SENHA_APP or not auth:
        return False
    return hmac.compare_digest((auth.password or "").encode(), SENHA_APP.encode())


def exigir_senha(funcao):
    @wraps(funcao)
    def interno(*args, **kwargs):
        if not senha_ok():
            return Response("Senha necessaria", 401,
                            {"WWW-Authenticate": 'Basic realm="Bot de produtos"'})
        return funcao(*args, **kwargs)
    return interno


# ---------- IA ----------
def motivo(resp):
    try:
        erro = resp.json()["error"]
        for d in erro.get("details", []):
            if "reason" in d:
                return d["reason"]
        return erro.get("message", "erro")[:150]
    except Exception:
        return resp.text[:150]


def listar_modelos():
    global MODELOS
    if MODELOS:
        return MODELOS
    achados = []
    try:
        r = requests.get(BASE_GEMINI + "/models?pageSize=100", headers=CAB_IA, timeout=30)
        if r.status_code == 200:
            for m in r.json().get("models", []):
                nome = m["name"].replace("models/", "")
                ok_metodo = "generateContent" in m.get("supportedGenerationMethods", [])
                lixo = any(x in nome for x in ("image", "tts", "live", "audio", "robotics"))
                if ok_metodo and "flash" in nome and not lixo:
                    achados.append(nome)
    except Exception:
        pass
    lista = [MODELO_PREFERIDO] + [n for n in achados if n != MODELO_PREFERIDO]
    if achados:
        MODELOS = lista[:5]
        return MODELOS
    return lista[:1]


def chamar_ia(corpo, registrar, timeout_val=120):
    for modelo in listar_modelos():
        url = BASE_GEMINI + "/models/" + modelo + ":generateContent"
        for tentativa in (1, 2):
            try:
                r = requests.post(url, headers=CAB_IA, json=corpo, timeout=timeout_val)
            except Exception:
                registrar(modelo + ": falha de conexao")
                r = None
            if r is not None:
                if r.status_code == 200:
                    return r.json()
                registrar(modelo + ": erro " + str(r.status_code) + " (" + motivo(r) + ")")
                if r.status_code in (401, 403):
                    return None
                if r.status_code in (400, 404):
                    break
            if tentativa == 1:
                time.sleep(8)
    return None


def analisar(nome, descricao, custo, registrar):
    prompt = f"""
Voce e um especialista em E-commerce e Copywriting para produtos de sucesso.
Responda sempre em portugues do Brasil, com acentuacao correta.

Produto:
- Nome bruto: {nome}
- Descricao: {descricao}
- Preco de custo: R$ {custo}

Tarefas:
1. Avalie se e comercializavel e possui bom apelo de venda.
2. Crie um titulo comercial altamente atraente e otimizado para SEO (maximo 60 caracteres).
3. Crie uma descricao vendedora em HTML: um paragrafo de gancho persuasivo,
   uma lista de 3 a 5 beneficios claros e especificacoes tecnicas simples.

Responda EXCLUSIVAMENTE em JSON com las chaves:
{{"eh_tendencia": true ou false, "titulo": "...", "descricao_html": "..."}}
"""
    corpo = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    dados = chamar_ia(corpo, registrar)
    if dados is None:
        return None
    try:
        texto = dados["candidates"][0]["content"]["parts"][0]["text"]
        texto = texto.replace(CERCA + "json", "").replace(CERCA, "").strip()
        return json.loads(texto)
    except Exception as erro:
        registrar("Resposta da IA ilegivel: " + str(erro))
        return None


def sugerir_tendencias(termo, registrar, lote_num):
    prompt = f"""
Voce e um curador especialista em e-commerce e produtos de alta demanda.
O usuario digitou o termo: "{termo}".
Gere um lote (lote numero {lote_num}) de exatamente 25 variacoes ou opçoes comerciais altamente precisas e DIFERENTES baseadas neste termo (por exemplo, se o termo for 'sofa', traga 25 novos tipos de sofás que vendem muito online).

Responda EXCLUSIVAMENTE em formato JSON contendo uma lista chamada "produtos" com exatamente 25 itens e os seguintes campos para cada item:
- "nome": Nome comercial forte e completo do produto
- "descricao_resumida": Breve explicacao das caracteristicas e diferenciais do modelo
- "custo_estimado": Um valor numerico realista de preco de custo em Reais (ex: 350.00)
- "motivo_tendencia": Por que este modelo especifico tem alta saida no e-commerce

Exemplo de formato:
{{
  "produtos": [
    {{"nome": "...", "descricao_resumida": "...", "custo_estimado": 150.00, "motivo_tendencia": "..."}}
  ]
}}
"""
    corpo = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    dados = chamar_ia(corpo, registrar, timeout_val=150)
    if dados is None:
        return None
    try:
        texto = dados["candidates"][0]["content"]["parts"][0]["text"]
        texto = texto.replace(CERCA + "json", "").replace(CERCA, "").strip()
        return json.loads(texto)
    except Exception as erro:
        registrar("Erro ao interpretar sugestoes: " + str(erro))
        return None


# ---------- Yampi ----------
def obter_ou_criar_marca(registrar):
    r = requests.get(BASE_YAMPI + "/brands", headers=CAB_YAMPI,
                     params={"name": NOME_MARCA}, timeout=60)
    if r.status_code == 200:
        for m in r.json().get("data", []):
            if m.get("name", "").strip().lower() == NOME_MARCA.lower():
                return m["id"]
    r = requests.post(BASE_YAMPI + "/brands", headers=CAB_YAMPI,
                      json={"name": NOME_MARCA, "active": True, "featured": False},
                      timeout=60)
    if r.status_code in (200, 201):
        return r.json()["data"]["id"]
    registrar("Erro ao criar marca (" + str(r.status_code) + "): " + r.text[:200])
    return None


def cadastrar(titulo, descricao_html, custo, venda, sku, brand_id, registrar):
    corpo = {
        "simple": True,
        "brand_id": brand_id,
        "active": PUBLICAR_DIRETO,
        "name": titulo,
        "description": descricao_html,
        "skus": [{
            "sku": sku,
            "price_cost": round(custo, 2),
            "price_sale": round(venda, 2),
            "price_discount": round(venda * 0.9, 2),
            "blocked_sale": False,
        }],
    }
    r = requests.post(BASE_YAMPI + "/products", headers=CAB_YAMPI, json=corpo, timeout=60)
    if r.status_code in (200, 201):
        return True
    registrar("Erro na Yampi (" + str(r.status_code) + "): " + r.text[:300])
    return False


# ---------- processo em segundo plano ----------
def processar(job_id, d):
    job = JOBS[job_id]

    def reg(msg):
        job["log"].append(msg)

    try:
        faltando = [n for n, v in (("CHAVE_GOOGLE", CHAVE_GOOGLE), ("YAMPI_ALIAS", YAMPI_ALIAS),
                                   ("YAMPI_USER_TOKEN", YAMPI_USER_TOKEN),
                                   ("YAMPI_SECRET_KEY", YAMPI_SECRET_KEY)) if not v]
        if faltando:
            job["status"] = "erro"
            reg("Faltam variaveis de ambiente no Render: " + ", ".join(faltando))
            return

        reg("Analisando com a IA Gemini...")
        analise = analisar(d["nome"], d["descricao"], d["custo"], reg)
        if analise is None:
            job["status"] = "erro"
            reg("A IA nao respondeu. Tente de novo.")
            return

        job["resultado_ia"] = analise

        if not analise.get("eh_tendencia") and not d["forcar"]:
            job["status"] = "ignorado"
            reg("A IA avaliou que o produto NAO e uma tendencia forte.")
            return

        reg("Titulo gerado: " + str(analise.get("titulo")))
        reg("Cadastrando na Yampi...")
        brand_id = obter_ou_criar_marca(reg)
        if brand_id is None:
            job["status"] = "erro"
            return

        venda = d["custo"] * MARGEM_LUCRO
        ok = cadastrar(analise["titulo"], analise["descricao_html"], d["custo"],
                       venda, d["sku"], brand_id, reg)
        if ok:
            job["status"] = "ok"
            estado = "ATIVO" if PUBLICAR_DIRETO else "INATIVO (revise no painel)"
            reg("Sucesso! Cadastrado como " + estado + ". Preco: R$ " + format(venda, ".2f"))
        else:
            job["status"] = "erro"
    except Exception as erro:
        job["status"] = "erro"
        reg("Erro inesperado: " + str(erro))


# ---------- rotas ----------
@app.route("/saude")
def saude():
    return "ok"


@app.route("/")
@exigir_senha
def inicio():
    return Response(PAGINA, mimetype="text/html")


@app.route("/api/produtos", methods=["POST"])
@exigir_senha
def novo():
    d = request.get_json(force=True, silent=True) or {}
    nome = str(d.get("nome", "")).strip()
    descricao = str(d.get("descricao", "")).strip()
    try:
        custo = float(str(d.get("custo", "")).replace(",", "."))
    except ValueError:
        return jsonify(erro="Preco de custo invalido"), 400
    if not nome or custo <= 0:
        return jsonify(erro="Informe o nome e um custo maior que zero"), 400
    sku = str(d.get("sku", "")).strip() or "AUTO-" + str(int(time.time()))
    job_id = uuid.uuid4().hex[:10]
    JOBS[job_id] = {"status": "rodando", "log": []}
    dados = {"nome": nome, "descricao": descricao, "custo": custo,
             "sku": sku, "forcar": bool(d.get("forcar"))}
    threading.Thread(target=processar, args=(job_id, dados), daemon=True).start()
    return jsonify(id=job_id)


@app.route("/api/sugerir", methods=["POST"])
@exigir_senha
def sugerir():
    d = request.get_json(force=True, silent=True) or {}
    nicho = str(d.get("nicho", "")).strip()
    lote = int(d.get("lote", 1))
    if not nicho:
        return jsonify(erro="Informe o termo ou nicho"), 400
    
    logs = []
    def reg(m): logs.append(m)
    
    res = sugerir_tendencias(nicho, reg, lote)
    if not res or "produtos" not in res:
        return jsonify(erro="A IA nao conseguiu gerar sugestoes no momento."), 500
    return jsonify(produtos=res["produtos"])


@app.route("/api/status/<job_id>")
@exigir_senha
def status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify(erro="Tarefa nao encontrada"), 404
    return jsonify(job)


# ---------- PWA ----------
@app.route("/manifest.json")
def manifesto():
    dados = {
        "name": "Catálogo Pro - Bot IA",
        "short_name": "Catálogo Pro",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0f172a",
        "theme_color": "#6366f1",
        "icons": [
            {"src": "/icone-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/icone-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }
    return Response(json.dumps(dados), mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    js = 'self.addEventListener("fetch", function(e) {});'
    return Response(js, mimetype="application/javascript")


ICONES = {}


def gerar_icone(tam):
    if tam in ICONES:
        return ICONES[tam]
    fundo = bytes((99, 102, 241))
    branco = bytes((255, 255, 255))
    a = tam // 4
    b = tam - a
    linha_fundo = b"\x00" + fundo * tam
    linha_meio = b"\x00" + fundo * a + branco * (b - a) + fundo * (tam - b)
    linhas = b"".join(linha_meio if a <= y < b else linha_fundo for y in range(tam))

    def bloco(tipo, dados):
        corpo = tipo + dados
        return struct.pack(">I", len(dados)) + corpo + struct.pack(">I", zlib.crc32(corpo) & 0xFFFFFFFF)

    cabecalho = struct.pack(">IIBBBBB", tam, tam, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n" + bloco(b"IHDR", cabecalho)
            + bloco(b"IDAT", zlib.compress(linhas)) + bloco(b"IEND", b""))
    ICONES[tam] = png
    return png


@app.route("/icone-<int:tam>.png")
def icone(tam):
    if tam not in (192, 512):
        return Response("nao encontrado", 404)
    return Response(gerar_icone(tam), mimetype="image/png")


PAGINA = r"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
  <title>Catálogo Pro - IA & Yampi</title>
  <link rel="manifest" href="/manifest.json">
  <meta name="theme-color" content="#6366f1">
  <link rel="apple-touch-icon" href="/icone-192.png">
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #0f172a;
      --card-bg: #1e293b;
      --input-bg: #0f172a;
      --border: #334155;
      --primary: #6366f1;
      --primary-hover: #4f46e5;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --accent: #10b981;
      --danger: #ef4444;
      --warning: #f59e0b;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Plus Jakarta Sans', sans-serif; }

    body {
      background-color: var(--bg);
      color: var(--text);
      display: flex;
      justify-content: center;
      min-height: 100vh;
      padding: 16px;
    }

    .app-container {
      width: 100%;
      max-width: 540px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }

    .header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 8px 4px;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .brand-icon {
      width: 40px;
      height: 40px;
      background: linear-gradient(135deg, #6366f1, #a855f7);
      border-radius: 12px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 800;
      font-size: 1.2rem;
      box-shadow: 0 4px 12px rgba(99, 102, 241, 0.3);
    }

    .brand-title h1 { font-size: 1.15rem; font-weight: 700; }
    .brand-title p { font-size: 0.75rem; color: var(--text-muted); }

    .badge-yampi {
      background: rgba(16, 185, 129, 0.1);
      border: 1px solid rgba(16, 185, 129, 0.3);
      color: var(--accent);
      padding: 4px 10px;
      border-radius: 20px;
      font-size: 0.75rem;
      font-weight: 600;
    }

    .tabs {
      display: flex;
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 4px;
      gap: 4px;
    }

    .tab-btn {
      flex: 1;
      background: transparent;
      border: none;
      color: var(--text-muted);
      padding: 10px;
      font-size: 0.85rem;
      font-weight: 700;
      border-radius: 10px;
      cursor: pointer;
      transition: all 0.2s;
    }

    .tab-btn.active {
      background: var(--primary);
      color: white;
      box-shadow: 0 2px 8px rgba(99, 102, 241, 0.4);
    }

    .tab-content { display: none; flex-direction: column; gap: 16px; }
    .tab-content.active { display: flex; }

    .card {
      background-color: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 20px;
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3);
    }

    .form-group { margin-bottom: 16px; }

    label {
      display: block;
      font-size: 0.85rem;
      font-weight: 600;
      color: var(--text-muted);
      margin-bottom: 6px;
    }

    input, textarea {
      width: 100%;
      background-color: var(--input-bg);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 12px 14px;
      border-radius: 12px;
      font-size: 0.95rem;
      transition: all 0.2s ease;
    }

    input:focus, textarea:focus {
      outline: none;
      border-color: var(--primary);
      box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.2);
    }

    textarea { min-height: 80px; resize: vertical; }

    .grid-2 {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }

    .calc-box {
      background: rgba(15, 23, 42, 0.6);
      border: 1px dashed var(--border);
      border-radius: 12px;
      padding: 10px 14px;
      display: flex;
      flex-direction: column;
      justify-content: center;
    }

    .calc-box span { font-size: 0.75rem; color: var(--text-muted); }
    .calc-box strong { font-size: 1.1rem; color: var(--accent); }

    .checkbox-container {
      display: flex;
      align-items: center;
      gap: 10px;
      cursor: pointer;
      user-select: none;
      margin-top: 4px;
    }

    .checkbox-container input { width: auto; display: none; }

    .custom-checkbox {
      width: 20px;
      height: 20px;
      border: 2px solid var(--border);
      border-radius: 6px;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all 0.2s;
      background: var(--input-bg);
    }

    .checkbox-container input:checked + .custom-checkbox {
      background: var(--primary);
      border-color: var(--primary);
    }

    .custom-checkbox::after {
      content: '✓';
      color: white;
      font-size: 12px;
      font-weight: bold;
      display: none;
    }

    .checkbox-container input:checked + .custom-checkbox::after { display: block; }

    .btn-submit {
      width: 100%;
      background: linear-gradient(135deg, var(--primary), var(--primary-hover));
      color: white;
      border: none;
      border-radius: 12px;
      padding: 14px;
      font-size: 1rem;
      font-weight: 700;
      cursor: pointer;
      box-shadow: 0 4px 14px rgba(99, 102, 241, 0.35);
      transition: transform 0.1s, opacity 0.2s;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
    }

    .btn-submit:active { transform: scale(0.98); }
    .btn-submit:disabled { opacity: 0.5; cursor: not-allowed; }

    .spinner {
      width: 18px;
      height: 18px;
      border: 2px solid rgba(255,255,255,0.3);
      border-radius: 50%;
      border-top-color: white;
      animation: spin 0.8s linear infinite;
      display: none;
    }

    @keyframes spin { to { transform: rotate(360deg); } }

    .status-card {
      display: none;
      border-left: 4px solid var(--primary);
    }

    .status-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 10px;
    }

    .status-title { font-weight: 700; font-size: 0.95rem; }

    .logs-box {
      background: #090d16;
      border-radius: 10px;
      padding: 12px;
      font-family: monospace;
      font-size: 0.8rem;
      color: #34d399;
      max-height: 150px;
      overflow-y: auto;
      white-space: pre-wrap;
      border: 1px solid rgba(255,255,255,0.05);
    }

    .preview-box {
      margin-top: 12px;
      background: rgba(255,255,255,0.03);
      border-radius: 10px;
      padding: 12px;
      border: 1px solid var(--border);
    }

    .preview-title { font-size: 0.85rem; font-weight: 700; color: var(--accent); margin-bottom: 6px; }
    .preview-content { font-size: 0.8rem; color: var(--text-muted); line-height: 1.4; }

    .sugestao-card {
      background: rgba(255,255,255,0.02);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 16px;
      margin-bottom: 12px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .sugestao-titulo { font-size: 0.95rem; font-weight: 700; color: #fff; }
    .sugestao-desc { font-size: 0.8rem; color: var(--text-muted); }
    .sugestao-footer { display: flex; justify-content: space-between; align-items: center; margin-top: 4px; }
    .sugestao-preco { font-size: 0.9rem; font-weight: 700; color: var(--accent); }
    .btn-cadastrar-sugestao {
      background: var(--accent);
      color: #064e3b;
      border: none;
      padding: 8px 14px;
      border-radius: 8px;
      font-weight: 700;
      font-size: 0.8rem;
      cursor: pointer;
    }
    .btn-cadastrar-sugestao:active { transform: scale(0.95); }
    
    .btn-carregar-mais {
      width: 100%;
      background: transparent;
      border: 2px dashed var(--primary);
      color: var(--text);
      padding: 12px;
      border-radius: 12px;
      font-weight: 700;
      cursor: pointer;
      margin-top: 8px;
      display: none;
    }
  </style>
</head>
<body>

  <div class="app-container">
    <div class="header">
      <div class="brand">
        <div class="brand-icon">⚡</div>
        <div class="brand-title">
          <h1>Catálogo Pro</h1>
          <p>Automação Yampi + Gemini IA</p>
        </div>
      </div>
      <div class="badge-yampi">● Conectado</div>
    </div>

    <div class="tabs">
      <button class="tab-btn active" onclick="mudarAba('manual', this)">Manual</button>
      <button class="tab-btn" onclick="mudarAba('ia', this)">✨ Prateleira IA (Até 100+)</button>
    </div>

    <!-- ABA MANUAL -->
    <div id="aba-manual" class="tab-content active">
      <div class="card">
        <form id="bot-form" onsubmit="return false;">
          <div class="form-group">
            <label for="nome">Nome do Produto</label>
            <input type="text" id="nome" placeholder="Ex: Sofá Retrátil e Reclinável 3 Lugares" required>
          </div>

          <div class="form-group">
            <label for="descricao">Descrição Curta (Fornecedor / AliExpress)</label>
            <textarea id="descricao" placeholder="Copie aqui os detalhes brutos do produto..."></textarea>
          </div>

          <div class="grid-2 form-group">
            <div>
              <label for="custo">Preço de Custo (R$)</label>
              <input type="text" id="custo" inputmode="decimal" placeholder="450,00" required oninput="calcularVenda()">
            </div>
            <div class="calc-box">
              <span>Preço de Venda (2.2x)</span>
              <strong id="preco-venda-preview">R$ 0,00</strong>
            </div>
          </div>

          <div class="form-group">
            <label for="sku">SKU (Opcional)</label>
            <input type="text" id="sku" placeholder="Deixe vazio para gerar automático">
          </div>

          <div class="form-group">
            <label class="checkbox-container">
              <input type="checkbox" id="forcar">
              <div class="custom-checkbox"></div>
              <span style="font-size: 0.85rem; color: var(--text-muted);">Forçar cadastro mesmo se a IA reprovar</span>
            </label>
          </div>

          <button type="button" id="enviar" class="btn-submit">
            <div class="spinner" id="spinner"></div>
            <span id="btn-text">Analisar & Cadastrar na Yampi</span>
          </button>
        </form>
      </div>

      <div class="card status-card" id="status-card">
        <div class="status-header">
          <div class="status-title" id="status-title">Processando...</div>
          <div id="status-badge" style="font-size: 0.8rem; font-weight: 700;"></div>
        </div>
        <div class="logs-box" id="log-box">Iniciando análise com inteligência artificial...</div>

        <div class="preview-box" id="preview-box" style="display: none;">
          <div class="preview-title" id="preview-titulo"></div>
          <div class="preview-content" id="preview-html"></div>
        </div>
      </div>
    </div>

    <!-- ABA SUGESTÕES IA -->
    <div id="aba-ia" class="tab-content">
      <div class="card">
        <div class="form-group">
          <label for="nicho">Comando / Termo de Pesquisa</label>
          <input type="text" id="nicho" placeholder="Ex: sofá, panela elétrica, suporte de celular...">
        </div>
        <button type="button" id="btn-sugerir" class="btn-submit" onclick="iniciarBusca()">
          <div class="spinner" id="spinner-sugerir"></div>
          <span id="btn-sugerir-text">🔍 Gerar Lote de 25 Opções</span>
        </button>
      </div>

      <div id="lista-sugestoes"></div>
      <button type="button" id="btn-carregar-mais" class="btn-carregar-mais" onclick="carregarMais()">➕ Carregar Mais 25 Opções</button>
    </div>

  </div>

  <script>
    const MARGEM = 2.2;
    const $ = id => document.getElementById(id);
    let loteAtual = 1;
    let termoAtual = "";

    function mudarAba(aba, el) {
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      $("aba-" + aba).classList.add('active');
      el.classList.add('active');
    }

    function calcularVenda() {
      const valorStr = $("custo").value.replace(",", ".");
      const custo = parseFloat(valorStr);
      if (!isNaN(custo) && custo > 0) {
        const venda = custo * MARGEM;
        $("preco-venda-preview").textContent = "R$ " + venda.toFixed(2).replace(".", ",");
      } else {
        $("preco-venda-preview").textContent = "R$ 0,00";
      }
    }

    function iniciarBusca() {
      termoAtual = $("nicho").value.trim();
      if (!termoAtual) {
        alert("Digite um termo para pesquisar.");
        return;
      }
      loteAtual = 1;
      $("lista-sugestoes").innerHTML = "";
      executarBusca(false);
    }

    function carregarMais() {
      loteAtual++;
      executarBusca(true);
    }

    async function executarBusca(ehMais) {
      $("btn-sugerir").disabled = true;
      $("btn-carregar-mais").style.display = "none";
      $("spinner-sugerir").style.display = "block";
      $("btn-sugerir-text").textContent = `Buscando lote ${loteAtual} (25 opções)...`;

      try {
        const r = await fetch("/api/sugerir", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ nicho: termoAtual, lote: loteAtual })
        });
        const d = await r.json();
        $("btn-sugerir").disabled = false;
        $("spinner-sugerir").style.display = "none";
        $("btn-sugerir-text").textContent = "🔍 Gerar Lote de 25 Opções";
        $("btn-carregar-mais").style.display = "block";

        if (!r.ok) {
          alert(d.erro || "Erro ao buscar sugestões.");
          return;
        }

        let html = $("lista-sugestoes").innerHTML;
        (d.produtos || []).forEach((p) => {
          html += `
            <div class="sugestao-card">
              <div class="sugestao-titulo">${p.nome}</div>
              <div class="sugestao-desc">${p.descricao_resumida}</div>
              <div style="font-size:0.75rem; color:#f59e0b;">💡 Por que vende: ${p.motivo_tendencia}</div>
              <div class="sugestao-footer">
                <div class="sugestao-preco">Custo Est: R$ ${Number(p.custo_estimado).toFixed(2).replace('.', ',')}</div>
                <button class="btn-cadastrar-sugestao" onclick="preencherEEnviar('${p.nome.replace(/'/g, "\\'")}', '${p.descricao_resumida.replace(/'/g, "\\'")}', ${p.custo_estimado})">🚀 Cadastrar</button>
              </div>
            </div>
          `;
        });
        $("lista-sugestoes").innerHTML = html;
      } catch (e) {
        $("btn-sugerir").disabled = false;
        $("spinner-sugerir").style.display = "none";
        $("btn-sugerir-text").textContent = "🔍 Gerar Lote de 25 Opções";
        $("btn-carregar-mais").style.display = "block";
        alert("Erro de conexão ao buscar sugestões.");
      }
    }

    function preencherEEnviar(nome, desc, custo) {
      $("nome").value = nome;
      $("descricao").value = desc;
      $("custo").value = custo.toFixed(2).replace('.', ',');
      calcularVenda();
      mudarAba('manual', document.querySelectorAll('.tab-btn')[0]);
      $("enviar").click();
    }

    $("enviar").onclick = async () => {
      const nome = $("nome").value.trim();
      const custo = $("custo").value.trim();

      if (!nome || !custo) {
        alert("Por favor, preencha o nome do produto e o preço de custo.");
        return;
      }

      const corpo = {
        nome: nome,
        descricao: $("descricao").value.trim(),
        custo: custo,
        sku: $("sku").value.trim(),
        forcar: $("forcar").checked
      };

      $("enviar").disabled = true;
      $("spinner").style.display = "block";
      $("btn-text").textContent = "Processando com IA...";

      $("status-card").style.display = "block";
      $("status-title").textContent = "Analisando produto...";
      $("status-badge").textContent = "⏳ RODANDO";
      $("status-badge").style.color = "var(--warning)";
      $("log-box").textContent = "Conectando ao servidor e enviando dados para o Gemini...";
      $("preview-box").style.display = "none";

      try {
        const r = await fetch("/api/produtos", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(corpo)
        });
        const d = await r.json();

        if (!r.ok) {
          $("status-title").textContent = "Erro na solicitação";
          $("status-badge").textContent = "❌ ERRO";
          $("status-badge").style.color = "var(--danger)";
          $("log-box").textContent = d.erro || "Falha ao enviar os dados.";
          resetBotao();
          return;
        }

        acompanhar(d.id);
      } catch (e) {
        $("status-title").textContent = "Erro de conexão";
        $("status-badge").textContent = "❌ ERRO";
        $("status-badge").style.color = "var(--danger)";
        $("log-box").textContent = "Não foi possível se comunicar com o servidor.";
        resetBotao();
      }
    };

    async function acompanhar(id) {
      try {
        const r = await fetch("/api/status/" + id);
        const d = await r.json();

        if (!r.ok) {
          $("status-title").textContent = "Erro no servidor";
          $("log-box").textContent = d.erro || "Erro ao consultar status.";
          resetBotao();
          return;
        }

        $("log-box").textContent = (d.log || []).join("\n");
        $("log-box").scrollTop = $("log-box").scrollHeight;

        if (d.resultado_ia) {
          $("preview-box").style.display = "block";
          $("preview-titulo").textContent = "✨ Título Criado: " + (d.resultado_ia.titulo || "");
          $("preview-html").innerHTML = d.resultado_ia.descricao_html || "";
        }

        if (d.status === "rodando") {
          setTimeout(() => acompanhar(id), 2000);
        } else {
          if (d.status === "ok") {
            $("status-title").textContent = "Produto Cadastrado com Sucesso!";
            $("status-badge").textContent = "✅ SUCESSO";
            $("status-badge").style.color = "var(--accent)";
          } else if (d.status === "ignorado") {
            $("status-title").textContent = "Produto Reprovado pela IA";
            $("status-badge").textContent = "⚠️ REPROVADO";
            $("status-badge").style.color = "var(--warning)";
          } else {
            $("status-title").textContent = "Falha no Cadastro";
            $("status-badge").textContent = "❌ ERRO";
            $("status-badge").style.color = "var(--danger)";
          }
          resetBotao();
        }
      } catch (e) {
        setTimeout(() => acompanhar(id), 3000);
      }
    }

    function resetBotao() {
      $("enviar").disabled = false;
      $("spinner").style.display = "none";
      $("btn-text").textContent = "Analisar & Cadastrar na Yampi";
    }

    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js");
    }
  </script>
</body>
</html>"""
