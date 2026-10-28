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

# Chave centralizada da IA (Fica no Render, o cliente nao mexe nisso)
CHAVE_GOOGLE = os.environ.get("CHAVE_GOOGLE", "")
SENHA_ADMIN = os.environ.get("SENHA_ADMIN", "admin123")
NOME_MARCA = os.environ.get("NOME_MARCA", "DropAI Premium")
MODELO_PREFERIDO = os.environ.get("MODELO_PREFERIDO", "gemini-3.8-flash")
PUBLICAR_DIRETO = os.environ.get("PUBLICAR_DIRETO", "nao").lower() == "sim"

CERCA = chr(96) * 3
JOBS = {}
MODELOS = []


# ---------- Base de Dados SQLite (Utilizadores & Lojas) ----------
def init_db():
    conn = sqlite3.connect("dropai.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            senha TEXT NOT NULL,
            yampi_alias TEXT,
            yampi_token TEXT,
            yampi_secret TEXT
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
                    registrar(f"⏳ DropAI Cooldown: Limite de cota atingido (Erro 429). Aguardando 10s...")
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
Voce e o DropAI, uma IA especialista em E-commerce e Copywriting de alta conversao. 
Analise o nome/link fornecido e a descricao/avaliacoes originais para criar um novo nome comercial exclusivo e altamente chamativo.

Produto Bruto:
