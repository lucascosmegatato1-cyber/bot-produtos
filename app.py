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


def chamar_ia(corpo, registrar):
    for modelo in listar_modelos():
        url = BASE_GEMINI + "/models/" + modelo + ":generateContent"
        for tentativa in (1, 2):
            try:
                r = requests.post(url, headers=CAB_IA, json=corpo, timeout=90)
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
Voce e um especialista em E-commerce e Copywriting para produtos tendencia de utilidades domesticas.
Responda sempre em portugues do Brasil, com acentuacao correta.

Produto:
- Nome bruto: {nome}
- Descricao: {descricao}
- Preco de custo: R$ {custo}

Tarefas:
1. Avalie se e pratico, util e com potencial viral/tendencia.
2. Crie um titulo comercial atraente (maximo 60 caracteres).
3. Crie uma descricao vendedora em HTML: um paragrafo de gancho,
    uma lista de 3 a 5 beneficios e especificacoes tecnicas simples.

Responda EXCLUSIVAMENTE em JSON com as chaves:
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

        reg("Analisando com a IA...")
        analise = analisar(d["nome"], d["descricao"], d["custo"], reg)
        if analise is None:
            job["status"] = "erro"
            reg("A IA nao respondeu. Tente de novo em alguns minutos.")
            return

        if not analise.get("eh_tendencia") and not d["forcar"]:
            job["status"] = "ignorado"
            reg("A IA avaliou que nao e uma tendencia forte.")
            return

        reg("Titulo: " + str(analise.get("titulo")))
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
            estado = "ATIVO" if PUBLICAR_DIRETO else "INATIVO (revise no painel antes de publicar)"
            reg("Cadastrado como " + estado + ". Preco de venda: R$ " + format(venda, ".2f"))
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


@app.route("/api/status/<job_id>")
@exigir_senha
def status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify(erro="Tarefa nao encontrada (o servidor pode ter reiniciado)"), 404
    return jsonify(job)


# ---------- PWA (app instalavel) - rotas sem senha de proposito ----------
@app.route("/manifest.json")
def manifesto():
    dados = {
        "name": "Bot de Produtos",
        "short_name": "Bot Produtos",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#f4f4f6",
        "theme_color": "#5b3df5",
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
    fundo = bytes((91, 61, 245))
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
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bot de Produtos</title>
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#5b3df5">
<link rel="apple-touch-icon" href="/icone-192.png">
<style>
body{font-family:system-ui,sans-serif;margin:0;padding:16px;background:#f4f4f6;color:#222}
.cartao{max-width:520px;margin:0 auto;background:#fff;border-radius:14px;padding:18px;box-shadow:0 2px 8px #0002}
h1{font-size:1.25rem;margin:0 0 12px}
label{display:block;margin:12px 0 4px;font-weight:600;font-size:.9rem}
input,textarea{width:100%;box-sizing:border-box;padding:12px;font-size:1rem;border:1px solid #ccc;border-radius:10px}
textarea{min-height:90px}
button{width:100%;margin-top:16px;padding:14px;font-size:1.05rem;border:0;border-radius:10px;background:#5b3df5;color:#fff;font-weight:600}
button:disabled{opacity:.5}
.linha{display:flex;align-items:center;gap:8px;margin-top:12px}
.linha input{width:auto}
#log{white-space:pre-wrap;background:#111;color:#cfe;padding:12px;border-radius:10px;margin-top:16px;font-size:.85rem;display:none}
#resultado{margin-top:12px;font-weight:700}
</style></head><body><div class="cartao">
<h1>Cadastrar produto na Yampi</h1>
<label>Nome do produto</label><input id="nome">
<label>Descrição</label><textarea id="descricao"></textarea>
<label>Preço de custo (R$)</label><input id="custo" inputmode="decimal" placeholder="12,50">
<label>SKU (opcional)</label><input id="sku" placeholder="deixe vazio para gerar automático">
<div class="linha"><input type="checkbox" id="forcar"><span>Cadastrar mesmo se a IA achar que não é tendência</span></div>
<button id="enviar">Analisar e cadastrar</button>
<div id="resultado"></div><div id="log"></div>
</div>
<script>
const $ = id => document.getElementById(id);
$("enviar").onclick = async () => {
  const corpo = {nome:$("nome").value, descricao:$("descricao").value, custo:$("custo").value,
                 sku:$("sku").value, forcar:$("forcar").checked};
  $("enviar").disabled = true;
  $("resultado").textContent = "Enviando...";
  $("log").style.display = "block";
  $("log").textContent = "";
  try {
    const r = await fetch("/api/produtos", {method:"POST",
      headers:{"Content-Type":"application/json"}, body:JSON.stringify(corpo)});
    const d = await r.json();
    if (!r.ok) { $("resultado").textContent = d.erro || "Erro"; $("enviar").disabled = false; return; }
    acompanhar(d.id);
  } catch (e) {
    $("resultado").textContent = "Falha de conexão";
    $("enviar").disabled = false;
  }
};
async function acompanhar(id) {
  try {
    const r = await fetch("/api/status/" + id);
    const d = await r.json();
    if (!r.ok) { $("resultado").textContent = d.erro || "Erro"; $("enviar").disabled = false; return; }
    $("log").textContent = (d.log || []).join("\n");
    const msgs = {rodando:"Processando...", ok:"✅ Cadastrado!", erro:"❌ Deu erro", ignorado:"⚠️ Ignorado pela IA"};
    $("resultado").textContent = msgs[d.status] || d.status;
    if (d.status === "rodando") setTimeout(() => acompanhar(id), 2500);
    else $("enviar").disabled = false;
  } catch (e) {
    setTimeout(() => acompanhar(id), 4000);
  }
}
if ("serviceWorker" in navigator) { navigator.serviceWorker.register("/sw.js"); }
</script></body></html>"""
