import os
import json
import time
import uuid
import hmac
import sqlite3
import struct
import zlib
import threading
from functools import wraps

import requests
from flask import Flask, request, jsonify, Response, session

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "dropai-secret-key-super-segura-2026")

# ---------- CONTAS VIP COM PRIORIDADE MÁXIMA ----------
EMAILS_VIP = [
    "lojashopee995@gmail.com",  # Sua conta principal (Prioridade 1)
    "email_do_seu_pai@gmail.com",     # Pai
    "email_do_seu_irmao@gmail.com"   # Irmão
]

# Chave centralizada da IA no Render
CHAVE_GOOGLE = os.environ.get("CHAVE_GOOGLE", "")
NOME_MARCA = os.environ.get("NOME_MARCA", "DropAI Premium")
MODELO_PREFERIDO = os.environ.get("MODELO_PREFERIDO", "gemini-3.8-flash")
PUBLICAR_DIRETO = os.environ.get("PUBLICAR_DIRETO", "nao").lower() == "sim"

CERCA = chr(96) * 3
JOBS = {}
MODELOS = []

LOCK_PROCESSAMENTO = threading.Lock()


# ---------- Base de Dados SQLite ----------
def init_db():
    conn = sqlite3.connect("dropai.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            senha TEXT NOT NULL,
            eh_vip INTEGER DEFAULT 0,
            plataforma TEXT DEFAULT 'yampi',
            loja_url TEXT,
            api_token TEXT,
            api_secret TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()


def db_conectar():
    return sqlite3.connect("dropai.db")


# ---------- IA Centralizada ----------
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
    headers = {"x-goog-api-key": CHAVE_GOOGLE, "Content-Type": "application/json"}
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models?pageSize=100", headers=headers, timeout=30)
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
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
        headers = {"x-goog-api-key": CHAVE_GOOGLE, "Content-Type": "application/json"}
        for tentativa in (1, 2):
            try:
                r = requests.post(url, headers=headers, json=corpo, timeout=timeout_val)
            except Exception:
                registrar(modelo + ": falha de conexao")
                r = None
            if r is not None:
                if r.status_code == 200:
                    return r.json()
                
                err_msg = motivo(r)
                if r.status_code == 429:
                    registrar(f"⏳ DropAI Cooldown: Limite de cota atingido. Aguardando 10s...")
                    time.sleep(10)
                else:
                    registrar(modelo + ": erro " + str(r.status_code) + " (" + err_msg + ")")

                if r.status_code in (401, 403):
                    return None
                if r.status_code in (400, 404):
                    break
            if tentativa == 1:
                time.sleep(5)
    return None


def analisar(nome, descricao, custo, registrar):
    prompt = f"""
Voce e o DropAI, uma IA especialista em E-commerce e Copywriting. 
Analise o nome/link fornecido e a descricao/avaliacoes originais para criar um novo nome comercial exclusivo e altamente chamativo.

Produto Bruto:
- Nome/Link: {nome}
- Descricao Original: {descricao}
- Preco de Custo: R$ {custo}

Tarefas:
1. Crie um NOVO titulo comercial exclusivo, forte e otimizado para SEO (maximo 60 caracteres).
2. Crie uma descricao vendedora completa em HTML estruturado (gancho persuasivo, beneficios detalhados e especificacoes tecnicas).

Responda EXCLUSIVAMENTE em JSON com as chaves exatas:
{{"eh_tendencia": true, "titulo": "...", "descricao_html": "..."}}
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
Voce e o DropAI, curador especialista em e-commerce. O usuario pediu o termo: "{termo}".
Gere o lote {lote_num} de exatamente 20 opçoes comerciais detalhadas baseadas neste termo.

Responda EXCLUSIVAMENTE em formato JSON contendo uma lista chamada "produtos" com 20 itens contendo:
- "nome": Nome comercial forte e unico
- "descricao_resumida": Descricao rica detalhando diferenciais
- "custo_estimado": Preco de custo realista em Reais (ex: 50.00)
- "motivo_tendencia": Por que vende bem
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


# ---------- Integrações Multiplataforma ----------

def cadastrar_yampi(alias, user_token, secret_key, titulo, descricao_html, custo, venda, sku, registrar):
    base_yampi = f"https://api.dooki.com.br/v2/{alias}/catalog"
    headers = {"User-Token": user_token, "User-Secret-Key": secret_key, "Content-Type": "application/json"}
    
    r = requests.get(base_yampi + "/brands", headers=headers, params={"name": NOME_MARCA}, timeout=60)
    brand_id = None
    if r.status_code == 200:
        for m in r.json().get("data", []):
            if m.get("name", "").strip().lower() == NOME_MARCA.lower():
                brand_id = m["id"]
    if not brand_id:
        r_brand = requests.post(base_yampi + "/brands", headers=headers, json={"name": NOME_MARCA, "active": True}, timeout=60)
        if r_brand.status_code in (200, 201):
            brand_id = r_brand.json()["data"]["id"]

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
    r = requests.post(base_yampi + "/products", headers=headers, json=corpo, timeout=60)
    if r.status_code in (200, 201):
        return True
    registrar("Erro na Yampi (" + str(r.status_code) + "): " + r.text[:300])
    return False


def cadastrar_shopify(loja_url, access_token, titulo, descricao_html, custo, venda, sku, registrar):
    loja_url = loja_url.replace("https://", "").replace("http://", "").strip("/")
    url = f"https://{loja_url}/admin/api/2024-01/products.json"
    headers = {"X-Shopify-Access-Token": access_token, "Content-Type": "application/json"}
    
    corpo = {
        "product": {
            "title": titulo,
            "body_html": descricao_html,
            "vendor": NOME_MARCA,
            "status": "active" if PUBLICAR_DIRETO else "draft",
            "variants": [{
                "sku": sku,
                "price": f"{venda:.2f}",
                "compare_at_price": f"{(venda * 1.2):.2f}",
                "cost_price": f"{custo:.2f}"
            }]
        }
    }
    r = requests.post(url, headers=headers, json=corpo, timeout=60)
    if r.status_code in (200, 201):
        return True
    registrar("Erro na Shopify (" + str(r.status_code) + "): " + r.text[:300])
    return False


def cadastrar_nuvemshop(store_id, access_token, titulo, descricao_html, custo, venda, sku, registrar):
    url = f"https://api.nuvemshop.com.br/v1/{store_id}/products"
    headers = {
        "Authentication": f"bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": "DropAI (suporte@dropai.com)"
    }
    
    corpo = {
        "name": {"pt": titulo},
        "description": {"pt": descricao_html},
        "published": PUBLICAR_DIRETO,
        "variants": [{
            "sku": sku,
            "price": f"{venda:.2f}",
            "cost": f"{custo:.2f}"
        }]
    }
    r = requests.post(url, headers=headers, json=corpo, timeout=60)
    if r.status_code in (200, 201):
        return True
    registrar("Erro na Nuvemshop (" + str(r.status_code) + "): " + r.text[:300])
    return False


# ---------- Processo Background ----------
def processar(job_id, d):
    job = JOBS[job_id]

    def reg(msg):
        job["log"].append(msg)

    if not d.get("eh_vip"):
        reg("Fila DropAI: Aguardando slot do servidor...")
        time.sleep(2)

    with LOCK_PROCESSAMENTO:
        try:
            if not CHAVE_GOOGLE:
                job["status"] = "erro"
                reg("Erro crítico: Chave da IA centralizada não configurada no Render.")
                return

            plataforma = d.get("plataforma", "yampi")
            if not d.get("loja_url") or not d.get("api_token"):
                job["status"] = "erro"
                reg("Erro: Credenciais da loja não configuradas. Vá em Configurações.")
                return

            if d.get("eh_vip"):
                reg("👑 Processamento Prioritário VIP Ativado!")

            reg(f"DropAI analisando e sintetizando nome para {plataforma.upper()}...")
            analise = analisar(d["nome"], d["descricao"], d["custo"], reg)
            if analise is None:
                job["status"] = "erro"
                reg("A IA não respondeu. Tente novamente em instantes.")
                return

            job["resultado_ia"] = analise
            reg("Novo Título Criado: " + str(analise.get("titulo")))
            reg(f"Cadastrando na {plataforma.upper()} com multiplicador 2x...")

            venda = d["custo"] * 2.0
            ok = False

            if plataforma == "yampi":
                ok = cadastrar_yampi(d["loja_url"], d["api_token"], d["api_secret"], analise["titulo"], analise["descricao_html"], d["custo"], venda, d["sku"], reg)
            elif plataforma == "shopify":
                ok = cadastrar_shopify(d["loja_url"], d["api_token"], analise["titulo"], analise["descricao_html"], d["custo"], venda, d["sku"], reg)
            elif plataforma == "nuvemshop":
                ok = cadastrar_nuvemshop(d["loja_url"], d["api_token"], analise["titulo"], analise["descricao_html"], d["custo"], venda, d["sku"], reg)

            if ok:
                job["status"] = "ok"
                reg(f"Sucesso! Produto cadastrado. Preço de Venda (2x): R$ {venda:.2f}")
            else:
                job["status"] = "erro"
        except Exception as erro:
            job["status"] = "erro"
            reg("Erro inesperado: " + str(erro))


# ---------- Rotas API ----------
@app.route("/saude")
def saude():
    return "ok"


@app.route("/")
def inicio():
    return Response(PAGINA, mimetype="text/html")


@app.route("/api/auth/registrar", methods=["POST"])
def registrar_usuario():
    d = request.get_json(force=True, silent=True) or {}
    email = str(d.get("email", "")).strip().lower()
    senha = str(d.get("senha", "")).strip()
    if not email or not senha:
        return jsonify(erro="Informe e-mail e senha"), 400

    eh_vip = 1 if email in [e.lower() for e in EMAILS_VIP] else 0

    conn = db_conectar()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO usuarios (email, senha, eh_vip) VALUES (?, ?, ?)", (email, senha, eh_vip))
        conn.commit()
        return jsonify(sucesso=True, eh_vip=bool(eh_vip))
    except sqlite3.IntegrityError:
        return jsonify(erro="Este e-mail já está cadastrado. Clique em Entrar."), 400
    finally:
        conn.close()


@app.route("/api/auth/login", methods=["POST"])
def login_usuario():
    d = request.get_json(force=True, silent=True) or {}
    email = str(d.get("email", "")).strip().lower()
    senha = str(d.get("senha", "")).strip()

    conn = db_conectar()
    cursor = conn.cursor()
    cursor.execute("SELECT id, plataforma, loja_url, api_token, api_secret, eh_vip FROM usuarios WHERE email = ? AND senha = ?", (email, senha))
    user = cursor.fetchone()
    conn.close()

    if not user:
        return jsonify(erro="E-mail ou senha incorretos"), 401

    is_vip = 1 if email in [e.lower() for e in EMAILS_VIP] else (user[5] or 0)

    return jsonify(sucesso=True, user={
        "id": user[0], 
        "email": email, 
        "plataforma": user[1] or "yampi",
        "loja_url": user[2] or "", 
        "api_token": user[3] or "", 
        "api_secret": user[4] or "", 
        "eh_vip": bool(is_vip)
    })


@app.route("/api/auth/salvar-config", methods=["POST"])
def salvar_config():
    d = request.get_json(force=True, silent=True) or {}
    email = str(d.get("email", "")).strip().lower()
    plataforma = str(d.get("plataforma", "yampi")).strip()
    loja_url = str(d.get("loja_url", "")).strip()
    api_token = str(d.get("api_token", "")).strip()
    api_secret = str(d.get("api_secret", "")).strip()

    if not email:
        return jsonify(erro="Utilizador não identificado"), 400

    conn = db_conectar()
    cursor = conn.cursor()
    cursor.execute("UPDATE usuarios SET plataforma = ?, loja_url = ?, api_token = ?, api_secret = ? WHERE email = ?", 
                   (plataforma, loja_url, api_token, api_secret, email))
    conn.commit()
    conn.close()
    return jsonify(sucesso=True)


@app.route("/api/produtos", methods=["POST"])
def novo():
    d = request.get_json(force=True, silent=True) or {}
    nome = str(d.get("nome", "")).strip()
    descricao = str(d.get("descricao", "")).strip()
    try:
        custo = float(str(d.get("custo", "")).replace(",", "."))
    except ValueError:
        return jsonify(erro="Preço de custo inválido"), 400
    if not nome or custo <= 0:
        return jsonify(erro="Informe o nome e um custo maior que zero"), 400
    
    sufixo_sku = str(int(time.time() * 1000))[-6:]
    sku_informado = str(d.get("sku", "")).strip()
    sku = f"{sku_informado}-{sufixo_sku}" if sku_informado else f"AUTO-{sufixo_sku}"

    job_id = uuid.uuid4().hex[:10]
    JOBS[job_id] = {"status": "rodando", "log": []}
    
    dados = {
        "nome": nome,
        "descricao": descricao,
        "custo": custo,
        "sku": sku,
        "plataforma": str(d.get("plataforma", "yampi")).strip(),
        "loja_url": str(d.get("loja_url", "")).strip(),
        "api_token": str(d.get("api_token", "")).strip(),
        "api_secret": str(d.get("api_secret", "")).strip(),
        "eh_vip": bool(d.get("eh_vip"))
    }
    
    threading.Thread(target=processar, args=(job_id, dados), daemon=True).start()
    return jsonify(id=job_id)


@app.route("/api/sugerir", methods=["POST"])
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
        return jsonify(erro="DropAI em cooldown. Aguarde 1 minuto."), 500
    return jsonify(produtos=res["produtos"])


@app.route("/api/status/<job_id>")
def status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify(erro="Tarefa não encontrada"), 404
    return jsonify(job)


# ---------- PWA ----------
@app.route("/manifest.json")
def manifesto():
    dados = {
        "name": "DropAI - Automação Multiplataforma",
        "short_name": "DropAI",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#000000",
        "theme_color": "#00A3FF",
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
    fundo = bytes((0, 0, 0))
    ciano = bytes((0, 163, 255))
    a = tam // 4
    b = tam - a
    linha_fundo = b"\x00" + fundo * tam
    linha_meio = b"\x00" + fundo * a + ciano * (b - a) + fundo * (tam - b)
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
        return Response("não encontrado", 404)
    return Response(gerar_icone(tam), mimetype="image/png")


PAGINA = r"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
  <title>DropAI - Multiplataforma Inteligente</title>
  <link rel="manifest" href="/manifest.json">
  <meta name="theme-color" content="#00A3FF">
  <link rel="apple-touch-icon" href="/icone-192.png">
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #000000;
      --card-bg: #111111;
      --input-bg: #030303;
      --border: #4F4F4F;
      --primary: #00A3FF;
      --primary-hover: #0081CC;
      --text: #F9FAFB;
      --text-muted: #9CA3AF;
      --accent: #00E676;
      --danger: #EF4444;
      --warning: #F59E0B;
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

    #auth-screen {
      display: flex;
      flex-direction: column;
      justify-content: center;
      min-height: 90vh;
      gap: 20px;
    }

    .auth-logo {
      text-align: center;
      margin-bottom: 10px;
    }
    .auth-logo h1 { font-size: 2.2rem; font-weight: 800; background: linear-gradient(135deg, #00A3FF, #A78BFA); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    .auth-logo p { font-size: 0.85rem; color: var(--text-muted); }

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
      border: 2px solid var(--primary);
      border-radius: 12px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--primary);
      font-weight: 800;
      font-size: 1.2rem;
      box-shadow: 0 0 15px rgba(0, 163, 255, 0.4);
      position: relative;
    }

    .brand-icon::after {
      content: '♚';
      position: absolute;
      top: -8px;
      font-size: 0.9rem;
      text-shadow: 0 0 5px rgba(0, 163, 255, 1);
    }

    .brand-title h1 { font-size: 1.15rem; font-weight: 700; color: var(--text); }
    .brand-title p { font-size: 0.75rem; color: var(--text-muted); }

    .badge-vip {
      background: rgba(245, 158, 11, 0.15);
      border: 1px solid rgba(245, 158, 11, 0.4);
      color: var(--warning);
      padding: 4px 10px;
      border-radius: 20px;
      font-size: 0.75rem;
      font-weight: 700;
    }

    .badge-yampi {
      background: rgba(0, 230, 118, 0.1);
      border: 1px solid rgba(0, 230, 118, 0.3);
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
      font-size: 0.8rem;
      font-weight: 700;
      border-radius: 10px;
      cursor: pointer;
      transition: all 0.2s;
    }

    .tab-btn.active {
      background: var(--primary);
      color: #000;
      box-shadow: 0 0 10px rgba(0, 163, 255, 0.3);
    }

    .tab-content { display: none; flex-direction: column; gap: 16px; }
    .tab-content.active { display: flex; }

    .card {
      background-color: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 20px;
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.7);
    }

    .form-group { margin-bottom: 16px; }

    label {
      display: block;
      font-size: 0.85rem;
      font-weight: 600;
      color: var(--text-muted);
      margin-bottom: 6px;
    }

    input, select, textarea {
      width: 100%;
      background-color: var(--input-bg);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 12px 14px;
      border-radius: 12px;
      font-size: 0.95rem;
      transition: all 0.2s ease;
    }

    input:focus, select:focus, textarea:focus {
      outline: none;
      border-color: var(--primary);
      box-shadow: 0 0 10px rgba(0, 163, 255, 0.2);
    }

    textarea { min-height: 80px; resize: vertical; }

    .grid-2 {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }

    .calc-box {
      background: rgba(3, 3, 3, 0.9);
      border: 1px dashed var(--border);
      border-radius: 12px;
      padding: 10px 14px;
      display: flex;
      flex-direction: column;
      justify-content: center;
    }

    .calc-box span { font-size: 0.75rem; color: var(--text-muted); }
    .calc-box strong { font-size: 1.1rem; color: var(--accent); }

    .btn-submit {
      width: 100%;
      background: linear-gradient(135deg, var(--primary), var(--primary-hover));
      color: #000;
      border: none;
      border-radius: 12px;
      padding: 14px;
      font-size: 1rem;
      font-weight: 700;
      cursor: pointer;
      box-shadow: 0 4px 14px rgba(0, 163, 255, 0.3);
      transition: transform 0.1s, opacity 0.2s;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
    }

    .btn-submit:active { transform: scale(0.98); }
    .btn-submit:disabled { opacity: 0.5; cursor: not-allowed; }

    /* ESTILO DO GUIA PASSO A PASSO VISUAL */
    .tutorial-box {
      margin-top: 16px;
      background: rgba(0, 163, 255, 0.03);
      border: 1px solid rgba(0, 163, 255, 0.2);
      border-radius: 14px;
      padding: 16px;
    }
    .tutorial-title {
      font-size: 0.9rem;
      font-weight: 700;
      color: var(--primary);
      margin-bottom: 10px;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .tutorial-steps {
      display: flex;
      flex-direction: column;
      gap: 10px;
      font-size: 0.8rem;
      color: var(--text-muted);
      line-height: 1.4;
    }
    .step-item {
      display: flex;
      align-items: flex-start;
      gap: 8px;
    }
    .step-badge {
      background: var(--primary);
      color: #000;
      font-weight: 800;
      font-size: 0.7rem;
      padding: 2px 6px;
      border-radius: 6px;
      flex-shrink: 0;
    }

    .spinner {
      width: 18px;
      height: 18px;
      border: 2px solid rgba(0,0,0,0.3);
      border-radius: 50%;
      border-top-color: #000;
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
      background: #030303;
      border-radius: 10px;
      padding: 12px;
      font-family: monospace;
      font-size: 0.8rem;
      color: #34d399;
      max-height: 150px;
      overflow-y: auto;
      white-space: pre-wrap;
      border: 1px solid rgba(255,255,255,0.03);
    }

    .preview-box {
      margin-top: 12px;
      background: rgba(255,255,255,0.01);
      border-radius: 10px;
      padding: 12px;
      border: 1px solid var(--border);
    }

    .preview-title { font-size: 0.85rem; font-weight: 700; color: var(--primary); margin-bottom: 6px; }
    .preview-content { font-size: 0.8rem; color: var(--text-muted); line-height: 1.4; }

    .sugestao-card {
      background: rgba(255,255,255,0.01);
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
    .sugestao-preco { font-size: 0.9rem; font-weight: 700; color: var(--primary); }
    .btn-cadastrar-sugestao {
      background: var(--accent);
      color: #000;
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
      border: 2px dashed var(--border);
      color: var(--text);
      padding: 12px;
      border-radius: 12px;
      font-weight: 700;
      cursor: pointer;
      margin-top: 8px;
    }
  </style>
</head>
<body>

  <div class="app-container">

    <!-- TELA DE LOGIN / CADASTRO -->
    <div id="auth-screen">
      <div class="auth-logo">
        <h1>♚ DropAI</h1>
        <p>Automação Multiplataforma</p>
      </div>
      <div class="card">
        <div class="form-group">
          <label for="auth-email">E-mail de Acesso</label>
          <input type="email" id="auth-email" placeholder="seu@email.com">
        </div>
        <div class="form-group">
          <label for="auth-senha">Senha</label>
          <input type="password" id="auth-senha" placeholder="Sua senha...">
        </div>
        <div style="display: flex; gap: 10px;">
          <button type="button" class="btn-submit" onclick="fazerLogin()">Entrar</button>
          <button type="button" class="btn-submit" style="background: transparent; border: 1px solid var(--border); color: var(--text);" onclick="fazerRegisto()">Criar Conta</button>
        </div>
      </div>
    </div>

    <!-- PAINEL PRINCIPAL -->
    <div id="app-screen" style="display: none; flex-direction: column; gap: 16px;">
      <div class="header">
        <div class="brand">
          <div class="brand-icon">⚡</div>
          <div class="brand-title">
            <h1>DropAI</h1>
            <p id="user-email-display">Painel do Lojista</p>
          </div>
        </div>
        <div style="display: flex; align-items: center; gap: 8px;">
          <div class="badge-vip" id="badge-status-vip" style="display:none;">👑 VIP</div>
          <div class="badge-yampi" id="status-conexao">● Conectado</div>
          <button onclick="fazerLogout()" style="background: transparent; border: none; color: var(--danger); font-size: 0.75rem; font-weight: 700; cursor: pointer;">Sair</button>
        </div>
      </div>

      <!-- ABAS -->
      <div class="tabs">
        <button class="tab-btn active" onclick="mudarAba('manual', this)">Importar</button>
        <button class="tab-btn" onclick="mudarAba('ia', this)">✨ Prateleira</button>
        <button class="tab-btn" onclick="mudarAba('config', this)">⚙️ Loja</button>
      </div>

      <!-- ABA CONFIGURAÇÕES DA LOJA / PLATAFORMA -->
      <div id="aba-config" class="tab-content">
        <div class="card">
          <h3 style="font-size: 1rem; margin-bottom: 12px; color: var(--primary);">⚙️ Configuração da Plataforma</h3>
          <p style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 16px;">Selecione onde a sua loja opera e insira as credenciais de acesso.</p>
          
          <div class="form-group">
            <label for="cfg-plataforma">Plataforma de E-commerce</label>
            <select id="cfg-plataforma" onchange="atualizarLabelsConfig()">
              <option value="yampi">Yampi</option>
              <option value="shopify">Shopify</option>
              <option value="nuvemshop">Nuvemshop</option>
            </select>
          </div>

          <div class="form-group">
            <label id="lbl-url" for="cfg-url">Yampi Alias (Nome da Loja)</label>
            <input type="text" id="cfg-url" placeholder="ex: minhaloja">
          </div>
          
          <div class="form-group">
            <label id="lbl-token" for="cfg-token">Yampi User-Token</label>
            <input type="password" id="cfg-token" placeholder="Cole seu token...">
          </div>

          <div class="form-group" id="group-secret">
            <label id="lbl-secret" for="cfg-secret">Yampi User-Secret-Key</label>
            <input type="password" id="cfg-secret" placeholder="Cole sua secret key...">
          </div>

          <button type="button" class="btn-submit" onclick="salvarConfigLoja()">💾 Salvar Configurações</button>

          <!-- GUIA PASSO A PASSO DINÂMICO LOGO ABAIXO DO BOTÃO -->
          <div class="tutorial-box">
            <div class="tutorial-title">📖 Como encontrar suas credenciais:</div>
            <div class="tutorial-steps" id="tutorial-texto">
              <!-- Preenchido via JavaScript dinamicamente -->
            </div>
          </div>

        </div>
      </div>

      <!-- ABA IMPORTAR / MANUAL -->
      <div id="aba-manual" class="tab-content active">
        <div class="card">
          <form id="bot-form" onsubmit="return false;">
            <div class="form-group">
              <label for="nome">Link ou Nome do Produto Original</label>
              <input type="text" id="nome" placeholder="Cole o link ou nome do produto..." required>
            </div>

            <div class="form-group">
              <label for="descricao">Detalhes / Avaliações Originais</label>
              <textarea id="descricao" placeholder="Detalhes para o DropAI mesclar e gerar nome exclusivo..."></textarea>
            </div>

            <div class="grid-2 form-group">
              <div>
                <label for="custo">Preço de Custo (R$)</label>
                <input type="text" id="custo" inputmode="decimal" placeholder="50,00" required oninput="calcularVenda()">
              </div>
              <div class="calc-box">
                <span>Venda (2x Exato)</span>
                <strong id="preco-venda-preview">R$ 0,00</strong>
              </div>
            </div>

            <div class="form-group">
              <label for="sku">SKU Base (Opcional)</label>
              <input type="text" id="sku" placeholder="Ex: PROD-01">
            </div>

            <button type="button" id="enviar" class="btn-submit">
              <div class="spinner" id="spinner"></div>
              <span id="btn-text">Processar & Cadastrar na Loja (2x)</span>
            </button>
          </form>
        </div>

        <div class="card status-card" id="status-card">
          <div class="status-header">
            <div class="status-title" id="status-title">Processando...</div>
            <div id="status-badge" style="font-size: 0.8rem; font-weight: 700;"></div>
          </div>
          <div class="logs-box" id="log-box">Analisando dados do produto original...</div>

          <div class="preview-box" id="preview-box" style="display: none;">
            <div class="preview-title" id="preview-titulo"></div>
            <div class="preview-content" id="preview-html"></div>
          </div>
        </div>
      </div>

      <!-- ABA PRATELEIRA IA -->
      <div id="aba-ia" class="tab-content">
        <div class="card">
          <div class="form-group">
            <label for="nicho">Comando / Termo de Pesquisa</label>
            <input type="text" id="nicho" placeholder="Ex: sofá, utilidades cozinha, gadgets...">
          </div>
          <button type="button" id="btn-sugerir" class="btn-submit" onclick="iniciarBusca()">
            <div class="spinner" id="spinner-sugerir"></div>
            <span id="btn-sugerir-text">🔍 Gerar Lote de 20 Opções</span>
          </button>
        </div>

        <div id="lista-sugestoes"></div>
        <button type="button" id="btn-carregar-mais" class="btn-carregar-mais" onclick="carregarMais()" style="display:none;">➕ Carregar Mais 20 Opções</button>
      </div>

    </div>

  </div>

  <script>
    const MARGEM = 2.0;
    const $ = id => document.getElementById(id);
    let loteAtual = 1;
    let termoAtual = "";

    window.onload = () => {
      const savedUser = localStorage.getItem("dropai_user");
      if (savedUser) {
        const user = JSON.parse(savedUser);
        entrarNoSistema(user);
      }
      atualizarLabelsConfig();
    };

    function atualizarLabelsConfig() {
      const plat = $("cfg-plataforma").value;
      const tut = $("tutorial-texto");

      if (plat === "yampi") {
        $("lbl-url").textContent = "Yampi Alias (Nome da Loja)";
        $("cfg-url").placeholder = "ex: minhaloja";
        $("lbl-token").textContent = "Yampi User-Token";
        $("cfg-token").placeholder = "Cole seu User-Token...";
        $("group-secret").style.display = "block";
        $("lbl-secret").textContent = "Yampi User-Secret-Key";
        $("cfg-secret").placeholder = "Cole sua User-Secret-Key...";

        tut.innerHTML = `
          <div class="step-item"><span class="step-badge">1</span><span>Acesse o painel da sua Yampi e vá em <strong>Configurações > API</strong>.</span></div>
          <div class="step-item"><span class="step-badge">2</span><span>Copie o <strong>Alias</strong> (o nome que fica na URL da sua loja).</span></div>
          <div class="step-item"><span class="step-badge">3</span><span>Gere ou copie o seu <strong>User-Token</strong> e a <strong>User-Secret-Key</strong> e cole acima.</span></div>
        `;
      } else if (plat === "shopify") {
        $("lbl-url").textContent = "URL da Loja Shopify";
        $("cfg-url").placeholder = "ex: minhaloja.myshopify.com";
        $("lbl-token").textContent = "Admin API Access Token";
        $("cfg-token").placeholder = "shpat_xxxxxxxx...";
        $("group-secret").style.display = "none";

        tut.innerHTML = `
          <div class="step-item"><span class="step-badge">1</span><span>Na Shopify, vá em <strong>Apps > Desenvolver aplicativos</strong>.</span></div>
          <div class="step-item"><span class="step-badge">2</span><span>Crie um app e libere permissão de escrita em <strong>Produtos</strong> na API Admin.</span></div>
          <div class="step-item"><span class="step-badge">3</span><span>Instale o app e copie o <strong>Token de Acesso da API Admin</strong> (começa com shpat_).</span></div>
        `;
      } else if (plat === "nuvemshop") {
        $("lbl-url").textContent = "ID da Loja Nuvemshop";
        $("cfg-url").placeholder = "ex: 1234567";
        $("lbl-token").textContent = "Access Token (Bearer)";
        $("cfg-token").placeholder = "Cole o token da Nuvemshop...";
        $("group-secret").style.display = "none";

        tut.innerHTML = `
          <div class="step-item"><span class="step-badge">1</span><span>Acesse o painel de parceiros ou desenvolvedores da Nuvemshop.</span></div>
          <div class="step-item"><span class="step-badge">2</span><span>Crie um aplicativo privado para obter o seu <strong>Store ID</strong> (ID da loja).</span></div>
          <div class="step-item"><span class="step-badge">3</span><span>Copie o <strong>Access Token</strong> gerado e cole nos campos acima.</span></div>
        `;
      }
    }

    async function fazerLogin() {
      const email = $("auth-email").value.trim();
      const senha = $("auth-senha").value.trim();
      if (!email || !senha) { alert("Preencha e-mail e senha."); return; }

      try {
        const r = await fetch("/api/auth/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, senha })
        });
        const d = await r.json();
        if (!r.ok) { alert(d.erro || "Erro ao entrar."); return; }
        
        localStorage.setItem("dropai_user", JSON.stringify(d.user));
        entrarNoSistema(d.user);
      } catch (e) {
        alert("Erro de conexão.");
      }
    }

    async function fazerRegisto() {
      const email = $("auth-email").value.trim();
      const senha = $("auth-senha").value.trim();
      if (!email || !senha) { alert("Preencha e-mail e senha."); return; }

      try {
        const r = await fetch("/api/auth/registrar", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, senha })
        });
        const d = await r.json();
        if (!r.ok) { alert(d.erro || "Erro ao criar conta."); return; }
        alert("Conta criada com sucesso! Clique em Entrar.");
      } catch (e) {
        alert("Erro de conexão.");
      }
    }

    function entrarNoSistema(user) {
      $("auth-screen").style.display = "none";
      $("app-screen").style.display = "flex";
      $("user-email-display").textContent = user.email;
      $("cfg-plataforma").value = user.plataforma || "yampi";
      $("cfg-url").value = user.loja_url || "";
      $("cfg-token").value = user.api_token || "";
      $("cfg-secret").value = user.api_secret || "";
      atualizarLabelsConfig();
      if (user.eh_vip) {
        $("badge-status-vip").style.display = "inline-block";
      }
      if (user.loja_url) { $("status-conexao").textContent = "● " + user.plataforma.toUpperCase(); }
    }

    function fazerLogout() {
      localStorage.removeItem("dropai_user");
      $("app-screen").style.display = "none";
      $("auth-screen").style.display = "flex";
    }

    async function salvarConfigLoja() {
      const user = JSON.parse(localStorage.getItem("dropai_user") || "{}");
      user.plataforma = $("cfg-plataforma").value;
      user.loja_url = $("cfg-url").value.trim();
      user.api_token = $("cfg-token").value.trim();
      user.api_secret = $("cfg-secret").value.trim();

      try {
        const r = await fetch("/api/auth/salvar-config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: user.email, plataforma: user.plataforma, loja_url: user.loja_url, api_token: user.api_token, api_secret: user.api_secret })
        });
        const d = await r.json();
        if (!r.ok) { alert(d.erro || "Erro ao salvar."); return; }

        localStorage.setItem("dropai_user", JSON.stringify(user));
        $("status-conexao").textContent = "● " + user.plataforma.toUpperCase();
        alert("Configurações da loja salvas com sucesso!");
        mudarAba('manual', document.querySelectorAll('.tab-btn')[0]);
      } catch (e) {
        alert("Erro ao comunicar com o servidor.");
      }
    }

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
      executarBusca();
    }

    function carregarMais() {
      loteAtual++;
      executarBusca();
    }

    async function executarBusca() {
      $("btn-sugerir").disabled = true;
      $("btn-carregar-mais").style.display = "none";
      $("spinner-sugerir").style.display = "block";
      $("btn-sugerir-text").textContent = `DropAI buscando lote ${loteAtual} (20 opções)...`;

      try {
        const r = await fetch("/api/sugerir", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ nicho: termoAtual, lote: loteAtual })
        });
        const d = await r.json();
        $("btn-sugerir").disabled = false;
        $("spinner-sugerir").style.display = "none";
        $("btn-sugerir-text").textContent = "🔍 Gerar Lote de 20 Opções";
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
              <div style="font-size:0.75rem; color:#f59e0b;">💡 Destaque: ${p.motivo_tendencia}</div>
              <div class="sugestao-footer">
                <div class="sugestao-preco">Custo: R$ ${Number(p.custo_estimado).toFixed(2).replace('.', ',')} | Venda (2x): R$ ${(p.custo_estimado * 2).toFixed(2).replace('.', ',')}</div>
                <button class="btn-cadastrar-sugestao" onclick="preencherEEnviar('${p.nome.replace(/'/g, "\\'")}', '${p.descricao_resumida.replace(/'/g, "\\'")}', ${p.custo_estimado})">🚀 Cadastrar</button>
              </div>
            </div>
          `;
        });
        $("lista-sugestoes").innerHTML = html;
      } catch (e) {
        $("btn-sugerir").disabled = false;
        $("spinner-sugerir").style.display = "none";
        $("btn-sugerir-text").textContent = "🔍 Gerar Lote de 20 Opções";
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
      const user = JSON.parse(localStorage.getItem("dropai_user") || "{}");

      if (!nome || !custo) {
        alert("Por favor, preencha o nome/link do produto e o preço de custo.");
        return;
      }
      if (!user.loja_url || !user.api_token) {
        alert("Configure os seus dados de acesso na aba ⚙️ Loja antes de cadastrar.");
        mudarAba('config', document.querySelectorAll('.tab-btn')[2]);
        return;
      }

      const corpo = {
        nome: nome,
        descricao: $("descricao").value.trim(),
        custo: custo,
        sku: $("sku").value.trim(),
        plataforma: user.plataforma || "yampi",
        loja_url: user.loja_url,
        api_token: user.api_token,
        api_secret: user.api_secret,
        eh_vip: user.eh_vip
      };

      $("enviar").disabled = true;
      $("spinner").style.display = "block";
      $("btn-text").textContent = "DropAI processando...";

      $("status-card").style.display = "block";
      $("status-title").textContent = "Sintetizando e enviando para a loja...";
      $("status-badge").textContent = "⏳ RODANDO";
      $("status-badge").style.color = "var(--warning)";
      $("log-box").textContent = "DropAI gerando título exclusivo e aplicando multiplicador 2x...";
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
      $("btn-text").textContent = "Processar & Cadastrar na Loja (2x)";
    }

    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js");
    }
  </script>
</body>
</html>"""
