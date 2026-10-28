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
Analise o nome/link fornecido e a descricao/avaliacoes originais para criar um novo nome comercial exclusivo, otimizado e estruturar todas as informacoes do produto.

Produto Bruto:
- Nome/Link: {nome}
- Descricao Original: {descricao}
- Preco de Custo: R$ {custo}

Tarefas:
1. Crie um NOVO titulo comercial exclusivo, forte e otimizado para SEO (maximo 60 caracteres).
2. Crie uma descricao vendedora completa em HTML estruturado (gancho persuasivo, beneficios detalhados e especificacoes tecnicas).
3. Extraia ou deduza uma lista com pelo menos 1 URL de imagem valida baseada no produto se houver, ou URL generica representativa de alta qualidade.
4. Defina dimensoes fisicas e peso padrao realistas para envio (peso em kg ex: 0.3, altura em cm ex: 5, largura em cm ex: 15, comprimento em cm ex: 15).

Responda EXCLUSIVAMENTE em JSON com as chaves exatas:
{{"eh_tendencia": true, "titulo": "...", "descricao_html": "...", "imagens": ["url_da_imagem_1"], "peso": 0.3, "altura": 5.0, "largura": 15.0, "comprimento": 15.0}}
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
def cadastrar_yampi(alias, user_token, secret_key, analise, custo, venda, sku, registrar):
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

    # 3. Se mesmo assim a marca não existir, pega a primeira marca disponível na loja
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

    # Prepara as imagens no formato aceito pela Yampi
    imagens_formatadas = []
    for url_img in analise.get("imagens", []):
        if url_img:
            imagens_formatadas.append({"url": url_img})

    corpo = {
        "simple": True,
        "brand_id": brand_id,
        "active": PUBLICAR_DIRETO,
        "name": analise["titulo"],
        "description": analise["descricao_html"],
        "images": imagens_formatadas,
        "weight": analise.get("peso", 0.3),
        "height": analise.get("altura", 5.0),
        "width": analise.get("largura", 15.0),
        "length": analise.get("comprimento", 15.0),
        "skus": [{
            "sku": sku,
            "price_cost": round(custo, 2),
            "price_sale": round(venda, 2),
            "price_discount": round(venda * 0.9, 2),
            "blocked_sale": False,
            "stock_manage": True,
            "stock_quantity": 50,  # Estoque inicial preenchido automaticamente
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

            reg(f"DropAI analisando, coletando mídias e estruturando para {plataforma.upper()}...")
            analise = analisar(d["nome"], d["descricao"], d["custo"], reg)
            if analise is None:
                job["status"] = "erro"
                reg("A IA não respondeu. Tente novamente em instantes.")
                return

            job["resultado_ia"] = analise
            reg("Novo Título Criado: " + str(analise.get("titulo")))
            reg(f"Cadastrando na {plataforma.upper()} com imagens, frete e estoque configurados...")

            venda = d["custo"] * 2.0
            ok = False

            if plataforma == "yampi":
                ok = cadastrar_yampi(d["loja_url"], d["api_token"], d["api_secret"], analise, d["custo"], venda, d["sku"], reg)
            elif plataforma == "shopify":
                ok = cadastrar_shopify(d["loja_url"], d["api_token"], analise["titulo"], analise["descricao_html"], d["custo"], venda, d["sku"], reg)
            elif plataforma == "nuvemshop":
                ok = cadastrar_nuvemshop(d["loja_url"], d["api_token"], analise["titulo"], analise["descricao_html"], d["custo"], venda, d["sku"], reg)

            if ok:
                job["status"] = "ok"
                reg(f"Sucesso! Produto completo cadastrado. Preço de Venda (2x): R$ {venda:.2f}")
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
    return Response("DropAI Backend Rodando Perfeitamente", mimetype="text/html")


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
            try:
                conn.close()
            except Exception:
                pass
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
