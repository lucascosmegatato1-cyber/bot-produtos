import os
import json
import time
import uuid
import sqlite3
import threading

import requests
from flask import Flask, request, jsonify, Response, send_file

# Importa o cliente do Turso se estiver disponível
try:
    import libsql_client
except ImportError:
    libsql_client = None

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "dropai-secret-key-super-segura-2026")

# ---------- CONTAS VIP COM PRIORIDADE MÁXIMA ----------
EMAILS_VIP = [
    "lojashopee995@gmail.com",  # Sua conta principal (Prioridade 1)
    "email_do_seu_pai@gmail.com",      # Pai
    "email_do_seu_irmao@gmail.com"   # Irmão
]

# Chave centralizada da IA no Render
CHAVE_GOOGLE = os.environ.get("CHAVE_GOOGLE", "")
NOME_MARCA = os.environ.get("NOME_MARCA", "DropAI Premium")
MODELO_PREFERIDO = os.environ.get("MODELO_PREFERIDO", "gemini-3.8-flash")
PUBLICAR_DIRETO = os.environ.get("PUBLICAR_DIRETO", "nao").lower() == "sim"

TURSO_URL = os.environ.get("TURSO_DATABASE_URL", "")
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "")

CERCA = chr(96) * 3
JOBS = {}
MODELOS = []

LOCK_PROCESSAMENTO = threading.Lock()


# ---------- Base de Dados (Turso / SQLite) ----------
def db_conectar():
    if TURSO_URL and TURSO_TOKEN and libsql_client:
        return libsql_client.create_client_sync(url=TURSO_URL, auth_token=TURSO_TOKEN)
    else:
        return sqlite3.connect("dropai.db")

def init_db():
    if TURSO_URL and TURSO_TOKEN and libsql_client:
        conn = db_conectar()
        conn.execute("""
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
    else:
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
    
    # 1. Tenta listar as marcas existentes para encontrar a marca padrão
    r = requests.get(base_yampi + "/brands", headers=headers, params={"name": NOME_MARCA}, timeout=60)
    brand_id = None
    if r.status_code == 200:
        data_json = r.json()
        marcas = data_json.get("data", []) if isinstance(data_json, dict) else data_json
        for m in marcas:
            if isinstance(m, dict) and m.get("name", "").strip().lower() == NOME_MARCA.lower():
                brand_id = m["id"]
                break

    # 2. Se não encontrar, tenta criar a marca obrigatoriamente
    if not brand_id:
        r_brand = requests.post(base_yampi + "/brands", headers=headers, json={"name": NOME_MARCA, "active": True}, timeout=60)
        if r_brand.status_code in (200, 201):
            res_b = r_brand.json()
            brand_id = res_b.get("data", {}).get("id") or res_b.get("id")

    # 3. Se mesmo assim a marca não existir, pega a primeira marca disponível na loja para evitar o erro 422
    if not brand_id:
        r_all = requests.get(base_yampi + "/brands", headers=headers, timeout=60)
        if r_all.status_code == 200:
            all_data = r_all.json()
            lista_marcas = all_data.get("data", []) if isinstance(all_data, dict) else all_data
            if lista_marcas and isinstance(lista_marcas[0], dict):
                brand_id = lista_marcas[0].get("id")

    if not brand_id:
        registrar("Erro na Yampi: Nenhuma marca encontrada na loja. Crie uma marca manualmente na Yampi.")
        return False

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


# ---------- Rotas API & Ícones Estáticos ----------
@app.route("/saude")
def saude():
    return "ok"


@app.route("/")
def inicio():
    return Response(PAGINA, mimetype="text/html")


@app.route("/icone.png")
def icone_arquivo():
    if os.path.exists("icone.png"):
        return send_file("icone.png", mimetype="image/png")
    return "Ícone não encontrado", 404


@app.route("/favicon.ico")
def favicon():
    if os.path.exists("icone.png"):
        return send_file("icone.png", mimetype="image/png")
    return "", 204


@app.route("/api/auth/registrar", methods=["POST"])
def registrar_usuario():
    d = request.get_json(force=True, silent=True) or {}
    email = str(d.get("email", "")).strip().lower()
    senha = str(d.get("senha", "")).strip()
    if not email or not senha:
        return jsonify(erro="Informe e-mail e senha"), 400

    eh_vip = 1 if email in [e.lower() for e in EMAILS_VIP] else 0

    conn = db_conectar()
    try:
        if TURSO_URL and TURSO_TOKEN and libsql_client:
            conn.execute("INSERT INTO usuarios (email, senha, eh_vip) VALUES (?, ?, ?)", [email, senha, eh_vip])
        else:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO usuarios (email, senha, eh_vip) VALUES (?, ?, ?)", (email, senha, eh_vip))
            conn.commit()
            conn.close()
        return jsonify(sucesso=True, eh_vip=bool(eh_vip))
    except Exception:
        if not (TURSO_URL and TURSO_TOKEN and libsql_client):
            conn.close()
        return jsonify(erro="Este e-mail já está cadastrado. Clique em Entrar."), 400


@app.route("/api/auth/login", methods=["POST"])
def login_usuario():
    d = request.get_json(force=True, silent=True) or {}
    email = str(d.get("email", "")).strip().lower()
    senha = str(d.get("senha", "")).strip()

    conn = db_conectar()
    if TURSO_URL and TURSO_TOKEN and libsql_client:
        res = conn.execute("SELECT id, plataforma, loja_url, api_token, api_secret, eh_vip FROM usuarios WHERE email = ? AND senha = ?", [email, senha])
        rows = res.rows
        user = rows[0] if rows else None
    else:
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
    if TURSO_URL and TURSO_TOKEN and libsql_client:
        conn.execute("UPDATE usuarios SET plataforma = ?, loja_url = ?, api_token = ?, api_secret = ? WHERE email = ?", [plataforma, loja_url, api_token, api_secret, email])
    else:
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
            {"src": "/icone.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/icone.png", "sizes": "512x512", "type": "image/png"},
        ],
    }
    return Response(json.dumps(dados), mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    js = 'self.addEventListener("fetch", function(e) {});'
    return Response(js, mimetype="application/javascript")


PAGINA = r"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
  <title>DropAI - Multiplataforma Inteligente</title>
  <link rel="manifest" href="/manifest.json">
  <meta name="theme-color" content="#00A3FF">
  <link rel="apple-touch-icon" href="/icone.png">
  <link rel="icon" href="/icone.png" type="image/png">
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
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 10px;
    }
    
    .app-logo-img {
      width: 75px;
      height: 75px;
      border-radius: 18px;
      object-fit: cover;
      border: 2px solid var(--primary);
      box-shadow: 0 0 20px rgba(0, 163, 255, 0.5);
    }

    .auth-logo h1 { 
      font-size: 2.2rem; 
      font-weight: 800; 
      background: linear-gradient(135deg, #00A3FF, #FFFFFF); 
      -webkit-background-clip: text; 
      -webkit-text-fill-color: transparent; 
      letter-spacing: -1px;
    }
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

    .brand-icon-img {
      width: 42px;
      height: 42px;
      border-radius: 10px;
      border: 1px solid var(--primary);
      object-fit: cover;
      box-shadow: 0 0 10px rgba(0, 163, 255, 0.4);
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

    <!-- TELA DE LOGIN -->
    <div id="auth-screen">
      <div class="auth-logo">
        <img src="/icone.png" alt="DropAI Logo" class="app-logo-img">
        <h1>DropAI</h1>
        <p>Automação Multiplataforma Inteligente</p>
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
          <div class="brand-icon">
            <img src="/icone.png" alt="DropAI" class="brand-icon-img">
          </div>
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

          <div class="tutorial-box">
            <div class="tutorial-title">📖 Como encontrar suas credenciais:</div>
            <div class="tutorial-steps" id="tutorial-texto"></div>
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
                <input type="text" id="custo" placeholder="0.00" required oninput="calcularVenda()">
              </div>
              <div class="calc-box">
                <span>Preço de Venda (2x)</span>
                <strong id="calc-venda">R$ 0,00</strong>
              </div>
            </div>

            <div class="form-group">
              <label for="sku">SKU Personalizado (Opcional)</label>
              <input type="text" id="sku" placeholder="Deixe em branco para auto gerar...">
            </div>

            <button type="button" class="btn-submit" id="btn-enviar" onclick="enviarProduto()">
              <div class="spinner" id="spinner-enviar"></div>
              <span>🚀 Importar e Cadastrar Produto</span>
            </button>
          </form>
        </div>

        <div class="card status-card" id="card-status">
          <div class="status-header">
            <span class="status-title" id="status-titulo">Processando...</span>
            <span id="status-badge" style="font-size: 0.75rem; font-weight: 750;">Aguardando</span>
          </div>
          <div class="logs-box" id="logs-box">Iniciando tarefas...</div>
          <div class="preview-box" id="preview-box" style="display:none;">
            <div class="preview-title" id="prev-nome-titulo">Título Gerado</div>
            <div class="preview-content" id="prev-desc-html"></div>
          </div>
        </div>
      </div>

      <!-- ABA PRATELEIRA / IA TENDÊNCIAS -->
      <div id="aba-ia" class="tab-content">
        <div class="card">
          <h3 style="font-size: 1rem; margin-bottom: 12px; color: var(--primary);">✨ Prateleira Inteligente DropAI</h3>
          <p style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 16px;">Descubra produtos em alta num nicho e importe-os instantaneamente.</p>
          
          <div class="form-group">
            <label for="ia-termo">Termo / Nicho do Produto</label>
            <input type="text" id="ia-termo" placeholder="ex: gadgets para pets, utilidades cozinha...">
          </div>

          <button type="button" class="btn-submit" onclick="carregarTendencias(1)">
            🔍 Gerar Lote de Produtos (20 itens)
          </button>
        </div>

        <div id="lista-sugestoes-container"></div>
        <button type="button" id="btn-mais-lotes" class="btn-carregar-mais" style="display:none;" onclick="carregarMaisLotes()">Carregar Mais 20 Opções</button>
      </div>

    </div>

  </div>

  <script>
    let usuarioAtual = null;
    let loteAtualNum = 1;
    let termoAtualBusca = "";

    window.addEventListener('DOMContentLoaded', () => {
      const salvo = localStorage.getItem('dropai_user');
      if (salvo) {
        try {
          usuarioAtual = JSON.parse(salvo);
          entrarNoPainel();
        } catch(e) {
          localStorage.removeItem('dropai_user');
        }
      }
      atualizarLabelsConfig();
    });

    function mudarAba(nome, btn) {
      document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
      document.getElementById('aba-' + nome).classList.add('active');
      if(btn) btn.classList.add('active');
    }

    function atualizarLabelsConfig() {
      const plat = document.getElementById('cfg-plataforma').value;
      const lblUrl = document.getElementById('lbl-url');
      const lblToken = document.getElementById('lbl-token');
      const lblSecret = document.getElementById('lbl-secret');
      const groupSecret = document.getElementById('group-secret');
      const tutorial = document.getElementById('tutorial-texto');

      if (plat === 'yampi') {
        lblUrl.innerText = "Yampi Alias (Nome da Loja)";
        document.getElementById('cfg-url').placeholder = "ex: minhaloja";
        lblToken.innerText = "Yampi User-Token";
        document.getElementById('cfg-token').placeholder = "Cole seu User-Token...";
        lblSecret.innerText = "Yampi User-Secret-Key";
        document.getElementById('cfg-secret').placeholder = "Cole sua User-Secret-Key...";
        groupSecret.style.display = "block";
        tutorial.innerHTML = `
          <div class="step-item"><span class="step-badge">1</span><span>Acesse o painel da sua Yampi em Configurações > API.</span></div>
          <div class="step-item"><span class="step-badge">2</span><span>Copie o Alias da sua loja, o User-Token e a User-Secret-Key e cole acima.</span></div>
        `;
      } else if (plat === 'shopify') {
        lblUrl.innerText = "Domínio da Loja Shopify";
        document.getElementById('cfg-url').placeholder = "minhaloja.myshopify.com";
        lblToken.innerText = "Shopify Admin API Access Token";
        document.getElementById('cfg-token').placeholder = "shpat_xxxxxxxx...";
        groupSecret.style.display = "none";
        tutorial.innerHTML = `
          <div class="step-item"><span class="step-badge">1</span><span>Na Shopify, vá em Apps > Desenvolver apps > Criar um app customizado.</span></div>
          <div class="step-item"><span class="step-badge">2</span><span>Conceda permissões de escrita em produtos e copie o Access Token (shpat_...).</span></div>
        `;
      } else if (plat === 'nuvemshop') {
        lblUrl.innerText = "ID da Loja Nuvemshop";
        document.getElementById('cfg-url').placeholder = "ex: 1234567";
        lblToken.innerText = "Nuvemshop Access Token";
        document.getElementById('cfg-token').placeholder = "Cole seu token de acesso...";
        groupSecret.style.display = "none";
        tutorial.innerHTML = `
          <div class="step-item"><span class="step-badge">1</span><span>Acesse parceiros nuvemshop ou crie um app na sua conta para obter o Token e Store ID.</span></div>
        `;
      }
    }

    function fazerLogin() {
      const email = document.getElementById('auth-email').value.trim();
      const senha = document.getElementById('auth-senha').value.trim();
      if(!email || !senha) { alert('Preencha e-mail e senha.'); return; }

      fetch('/api/auth/login', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({email, senha})
      })
      .then(r => r.json().then(data => ({status: r.status, body: data})))
      .then(res => {
        if(res.status === 200 && res.body.sucesso) {
          usuarioAtual = res.body.user;
          localStorage.setItem('dropai_user', JSON.stringify(usuarioAtual));
          entrarNoPainel();
        } else {
          alert(res.body.erro || 'Erro ao fazer login');
        }
      });
    }

    function fazerRegisto() {
      const email = document.getElementById('auth-email').value.trim();
      const senha = document.getElementById('auth-senha').value.trim();
      if(!email || !senha) { alert('Preencha e-mail e senha para criar conta.'); return; }

      fetch('/api/auth/registrar', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({email, senha})
      })
      .then(r => r.json().then(data => ({status: r.status, body: data})))
      .then(res => {
        if(res.status === 200 && res.body.sucesso) {
          alert('Conta criada com sucesso! Faça login.');
        } else {
          alert(res.body.erro || 'Erro ao registrar');
        }
      });
    }

    function entrarNoPainel() {
      document.getElementById('auth-screen').style.display = 'none';
      document.getElementById('app-screen').style.display = 'flex';
      document.getElementById('user-email-display').innerText = usuarioAtual.email;

      if(usuarioAtual.eh_vip) {
        document.getElementById('badge-status-vip').style.display = 'block';
      }

      document.getElementById('cfg-plataforma').value = usuarioAtual.plataforma || 'yampi';
      document.getElementById('cfg-url').value = usuarioAtual.loja_url || '';
      document.getElementById('cfg-token').value = usuarioAtual.api_token || '';
      document.getElementById('cfg-secret').value = usuarioAtual.api_secret || '';
      atualizarLabelsConfig();
    }

    function fazerLogout() {
      localStorage.removeItem('dropai_user');
      usuarioAtual = null;
      document.getElementById('app-screen').style.display = 'none';
      document.getElementById('auth-screen').style.display = 'flex';
    }

    function salvarConfigLoja() {
      if(!usuarioAtual) return;
      const plataforma = document.getElementById('cfg-plataforma').value;
      const loja_url = document.getElementById('cfg-url').value.trim();
      const api_token = document.getElementById('cfg-token').value.trim();
      const api_secret = document.getElementById('cfg-secret').value.trim();

      fetch('/api/auth/salvar-config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({email: usuarioAtual.email, plataforma, loja_url, api_token, api_secret})
      })
      .then(r => r.json())
      .then(data => {
        if(data.sucesso) {
          usuarioAtual.plataforma = plataforma;
          usuarioAtual.loja_url = loja_url;
          usuarioAtual.api_token = api_token;
          usuarioAtual.api_secret = api_secret;
          localStorage.setItem('dropai_user', JSON.stringify(usuarioAtual));
          alert('Configurações salvas e associadas à sua conta com sucesso!');
        } else {
          alert(data.erro || 'Erro ao salvar');
        }
      });
    }

    function calcularVenda() {
      const custoStr = document.getElementById('custo').value.replace(',', '.');
      const custo = parseFloat(custoStr) || 0;
      const venda = custo * 2.0;
      document.getElementById('calc-venda').innerText = 'R$ ' + venda.toFixed(2).replace('.', ',');
    }

    function enviarProduto() {
      if(!usuarioAtual.loja_url || !usuarioAtual.api_token) {
        alert('Configure sua loja na aba ⚙️ Loja antes de importar!');
        mudarAba('config', document.querySelectorAll('.tab-btn')[2]);
        return;
      }

      const nome = document.getElementById('nome').value.trim();
      const descricao = document.getElementById('descricao').value.trim();
      const custo = document.getElementById('custo').value.trim();
      const sku = document.getElementById('sku').value.trim();

      if(!nome || !custo) {
        alert('Preencha o nome e o preço de custo.');
        return;
      }

      const btn = document.getElementById('btn-enviar');
      const spinner = document.getElementById('spinner-enviar');
      btn.disabled = true;
      spinner.style.display = 'block';

      const cardStatus = document.getElementById('card-status');
      const logsBox = document.getElementById('logs-box');
      cardStatus.style.display = 'block';
      logsBox.innerText = 'Enviando produto para o motor DropAI...';
      document.getElementById('preview-box').style.display = 'none';

      fetch('/api/produtos', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          nome, descricao, custo, sku,
          plataforma: usuarioAtual.plataforma,
          loja_url: usuarioAtual.loja_url,
          api_token: usuarioAtual.api_token,
          api_secret: usuarioAtual.api_secret,
          eh_vip: usuarioAtual.eh_vip
        })
      })
      .then(r => r.json())
      .then(data => {
        if(data.erro) {
          alert(data.erro);
          btn.disabled = false;
          spinner.style.display = 'none';
          return;
        }
        monitorarJob(data.id);
      })
      .catch(err => {
        alert('Erro de conexão');
        btn.disabled = false;
        spinner.style.display = 'none';
      });
    }

    function monitorarJob(jobId) {
      const interval = setInterval(() => {
        fetch('/api/status/' + jobId)
        .then(r => r.json())
        .then(job => {
          const logsBox = document.getElementById('logs-box');
          logsBox.innerText = job.log.join('\n');
          logsBox.scrollTop = logsBox.scrollHeight;

          if(job.status === 'ok' || job.status === 'erro') {
            clearInterval(interval);
            document.getElementById('btn-enviar').disabled = false;
            document.getElementById('spinner-enviar').style.display = 'none';
            document.getElementById('status-badge').innerText = job.status === 'ok' ? 'Concluído ✅' : 'Erro ❌';
            
            if(job.resultado_ia) {
              document.getElementById('prev-nome-titulo').innerText = job.resultado_ia.titulo;
              document.getElementById('prev-desc-html').innerHTML = job.resultado_ia.descricao_html;
              document.getElementById('preview-box').style.display = 'block';
            }
          }
        });
      }, 2000);
    }

    function carregarTendencias(lote) {
      const termo = document.getElementById('ia-termo').value.trim();
      if(!termo) { alert('Digite um termo ou nicho.'); return; }
      termoAtualBusca = termo;
      loteAtualNum = lote;

      const container = document.getElementById('lista-sugestoes-container');
      container.innerHTML = '<p style="text-align:center; color:var(--text-muted); padding:20px;">A IA está curando as melhores tendências...</p>';
      document.getElementById('btn-mais-lotes').style.display = 'none';

      fetch('/api/sugerir', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({nicho: termo, lote: lote})
      })
      .then(r => r.json())
      .then(data => {
        if(data.erro) {
          container.innerHTML = `<p style="text-align:center; color:var(--danger);">${data.erro}</p>`;
          return;
        }
        renderizarProdutosPrateleira(data.produtos);
        document.getElementById('btn-mais-lotes').style.display = 'block';
      });
    }

    function carregarMaisLotes() {
      loteAtualNum++;
      carregarTendencias(loteAtualNum);
    }

    function renderizarProdutosPrateleira(produtos) {
      const container = document.getElementById('lista-sugestoes-container');
      container.innerHTML = '';
      produtos.forEach(p => {
        const card = document.createElement('div');
        card.className = 'sugestao-card';
        card.innerHTML = `
          <div class="sugestao-titulo">${p.nome}</div>
          <div class="sugestao-desc">${p.descricao_resumida}</div>
          <div style="font-size:0.75rem; color:var(--warning);">💡 ${p.motivo_tendencia}</div>
          <div class="sugestao-footer">
            <span class="sugestao-preco">Custo Est.: R$ ${p.custo_estimado}</span>
            <button class="btn-cadastrar-sugestao" onclick="importarSugestao('${p.nome.replace(/'/g, "")}', '${p.descricao_resumida.replace(/'/g, "")}', ${p.custo_estimado})">Importar Produto</button>
          </div>
        `;
        container.appendChild(card);
      });
    }

    function importarSugestao(nome, desc, custo) {
      mudarAba('manual', document.querySelectorAll('.tab-btn')[0]);
      document.getElementById('nome').value = nome;
      document.getElementById('descricao').value = desc;
      document.getElementById('custo').value = custo;
      calcularVenda();
      window.scrollTo({top: 0, behavior: 'smooth'});
    }
  </script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
