from flask import Flask, request, jsonify, render_template_string, make_response, redirect, abort
from instagrapi import Client
from pymongo import MongoClient
import re
import os
import uuid
import random
import string
import time
import requests
import json
import base64
import io
import qrcode
import hashlib
import threading
from collections import defaultdict
from datetime import datetime, timedelta
from urllib.parse import unquote

app = Flask(__name__)

# ==========================================
# CONFIGURAÇÃO DO MONGODB
# ==========================================
MONGO_URI = "mongodb+srv://seyzalel_db_user:q4dKhbwPQwBcmEFZ@dmtopmonitor.dnbpdnd.mongodb.net/?appName=DMTopMonitor"

try:
    mongo_client = MongoClient(MONGO_URI)
    db = mongo_client["dmtopmonitor"]
    activities_collection = db["user_activities"]
    config_collection = db["session_configs"] # Coleção para gerenciar os tokens configurados antigos
    users_collection = db["users"]            # Coleção para gerenciar planos dos usuários
    settings_collection = db["settings"]      # Coleção para configurações globais (WhatsApp, Pix, etc)
    payments_collection = db["payments"]      # Coleção para gerenciar os pagamentos PIX
    profiles_cache_collection = db["profiles_cache"] # Coleção para salvar banda de proxy
    print("Sistema integrado e conectado ao MongoDB com sucesso.")
except Exception as e:
    print(f"Aviso de sistema: Falha na conexão com o MongoDB. Detalhe: {e}")

# ==========================================
# CACHE EM MEMÓRIA RAM (ECONOMIA MÁXIMA)
# ==========================================
MEMORY_CACHE = {}

# ==========================================
# SISTEMA DE PROTEÇÃO ANTI-BOT E FIREWALL
# ==========================================
IP_HISTORY = defaultdict(list)
RATE_LIMIT_MAX_REQ = 100
RATE_LIMIT_WINDOW = 60
BANNED_IPS = set()
IP_BANS = {}
BAN_TIME = 600
SUSPICIOUS_UA_REGEX = re.compile(
    r'(bot|spider|crawler|scraper|curl|wget|python|urllib|libwww|httpclient|java|php|postman|insomnia|'
    r'headless|phantom|selenium|puppeteer|playwright|cypress|slurp|yahoo|yandex|baidu|teoma|alexa|'
    r'nikto|nmap|sqlmap|mechanize|scrapy|zgrab|nucleus|httpx|masscan)',
    re.IGNORECASE
)

@app.before_request
def bot_firewall():
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if client_ip:
        client_ip = client_ip.split(',')[0].strip()
    else:
        client_ip = 'unknown'
        
    now = time.time()
    
    # Verifica se está banido
    if client_ip in IP_BANS:
        if now < IP_BANS[client_ip]:
            abort(403, description="Acesso negado: IP bloqueado por comportamento suspeito (Anti-Bot Ativado).")
        else:
            del IP_BANS[client_ip]
            
    # Rate Limit
    IP_HISTORY[client_ip] = [timestamp for timestamp in IP_HISTORY[client_ip] if now - timestamp < RATE_LIMIT_WINDOW]
    IP_HISTORY[client_ip].append(now)
    
    if len(IP_HISTORY[client_ip]) > RATE_LIMIT_MAX_REQ:
        IP_BANS[client_ip] = now + BAN_TIME
        abort(429, description="Too Many Requests: Tráfego anômalo detectado. Proteção de proxy ativada.")
        
    # Bloqueia bots conhecidos por User-Agent
    ua = request.headers.get('User-Agent', '')
    if SUSPICIOUS_UA_REGEX.search(ua):
        IP_BANS[client_ip] = now + BAN_TIME
        abort(403, description="Acesso negado: Assinatura de Bot/Scraper detectada.")

# ==========================================
# CONFIGURAÇÃO DA INSTAGRAPI (OTIMIZADA)
# ==========================================
SESSION_ID = "244877207%3AAr21uf6FwpaBd0%3A16%3AAYjowzk6z7VN2wEA5C372L5rHeUQTnpQogFR8rt83g"
PROXY_URL = "http://user-spbdjmclc2-continent-eu:lS8QnaqotlM9i3Y+g3@gate.decodo.com:10001"

insta_client_singleton = None
insta_lock = threading.Lock()

def get_sticky_proxy(sessionid):
    """
    Força a rede proxy a manter o MESMO IP (Sticky IP) baseado na sessão.
    Evita a rotação desnecessária de IPs que faz o Instagram baixar dados de segurança pesados.
    """
    try:
        if "@" in PROXY_URL:
            protocol, rest = PROXY_URL.split('://')
            credentials, address = rest.split('@')
            user, pwd = credentials.split(':', 1)
            
            sid_hash = hashlib.md5(sessionid.encode()).hexdigest()[:8]
            if "-session-" not in user:
                sticky_user = f"{user}-session-{sid_hash}"
                return f"{protocol}://{sticky_user}:{pwd}@{address}"
    except Exception:
        pass
    return PROXY_URL

def get_insta_client():
    """
    Inicia o Instagrapi APENAS quando alguém pesquisa algo (Lazy Load).
    Evita gastar franquia de proxy em reinicializações do servidor.
    """
    global insta_client_singleton
    with insta_lock:
        if insta_client_singleton is None:
            print("Inicializando sessão Instagrapi (Lazy Load)...")
            try:
                cl = Client()
                sticky_proxy = get_sticky_proxy(SESSION_ID)
                cl.set_proxy(sticky_proxy)
                cl.login_by_sessionid(SESSION_ID)
                insta_client_singleton = cl
                print("Instagrapi carregado e proxy fixado com sucesso.")
            except Exception as e:
                print(f"Erro ao inicializar Instagrapi: {e}")
                cl = Client()
                cl.set_proxy(PROXY_URL)
                cl.login_by_sessionid(SESSION_ID)
                insta_client_singleton = cl
        return insta_client_singleton

# ==========================================
# CONFIGURAÇÃO GGPIXAPI
# ==========================================
GGPIX_API_KEY = "gk_64425202f1286434cc2c5f2cab27b6952edc30caa87e3a77"
GGPIX_BASE_URL = "https://ggpixapi.com/api/v1"
GGPIX_HEADERS = {
    "Content-Type": "application/json",
    "X-API-Key": GGPIX_API_KEY
}

def criar_cobranca_pix_ggpix(valor_centavos, descricao, nome_pagador, cpf_pagador):
    url = f"{GGPIX_BASE_URL}/pix/in"
    external_id = f"DMReporting-{uuid.uuid4().hex[:8]}"
    payload = {
        "amountCents": valor_centavos,
        "description": descricao,
        "payerName": nome_pagador,
        "payerDocument": cpf_pagador,
        "externalId": external_id
    }
    try:
        response = requests.post(url, headers=GGPIX_HEADERS, json=payload)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as err:
        print(f"Erro ao gerar PIX (GGPIX): {err}")
        return None

def checar_status_transacao_ggpix(transaction_id):
    url = f"{GGPIX_BASE_URL}/transactions/{transaction_id}"
    try:
        response = requests.get(url, headers=GGPIX_HEADERS)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as err:
        print(f"Erro ao checar status PIX (GGPIX): {err}")
        return None

# ==========================================
# CONFIGURAÇÃO PARADISE PAGS API
# ==========================================
PARADISE_API_KEY = "sk_e33108ee283d27bbb9dc5954de0a1b9f3678fab039190efe2a55e57e93879903"
PARADISE_BASE_URL = "https://multi.paradisepags.com/api/v1"

def criar_cobranca_pix_paradise(valor_centavos, descricao, nome_pagador, cpf_pagador):
    url = f"{PARADISE_BASE_URL}/transaction.php"
    headers = {
        "X-API-Key": PARADISE_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "amount": valor_centavos,
        "description": descricao,
        "reference": f"REF-{uuid.uuid4().hex[:8]}",
        "source": "api_externa",
        "customer": {
            "name": nome_pagador,
            "email": "suporte@instpygateway.com",
            "phone": "11999999999",
            "document": cpf_pagador
        }
    }
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        transaction_data = response.json()
        
        if transaction_data.get("status") == "success":
            return {
                "id": transaction_data.get("transaction_id"),
                "pixCopyPaste": transaction_data.get("qr_code")
            }
        else:
            print(f"Erro Paradise API: Retorno não foi success. Response: {transaction_data}")
            return None
    except requests.exceptions.HTTPError as e:
        print(f"Falha critica (Paradise): {e}")
        return None

def checar_status_transacao_paradise(transaction_id):
    url = f"{PARADISE_BASE_URL}/query.php?action=get_transaction&id={transaction_id}"
    headers = {"X-API-Key": PARADISE_API_KEY}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        status_data = response.json()
        
        current_status = status_data.get("status")
        if current_status == "approved":
            return {"status": "COMPLETE"}
        elif current_status in ["failed", "refunded", "chargeback"]:
            return {"status": "FAILED"}
        else:
            return {"status": "PENDING"}
    except requests.exceptions.RequestException as e:
        print(f"Erro na conexao de consulta (Paradise): {e}")
        return None

# ==========================================
# GET GATEWAY ATUAL GLOBAL
# ==========================================
def get_active_gateway():
    settings = settings_collection.find_one({"_id": "global_config"})
    if settings and "pix_gateway" in settings:
        return settings["pix_gateway"]
    return "ggpix" # Default inicial

# ==========================================
# CONFIGURAÇÃO META PIXEL & CAPI SÊNIOR
# ==========================================
META_PIXEL_ID = "2263127927859713"
META_ACCESS_TOKEN = "EAAY3pJasrWUBSBFHDzEBUyZCKXQSILBT6o0fhuPipIUp3KUg0BtZBcSsIJM4BRJFHrwnJb55wtEjbigGFlx2ZAFyN4DpxI02Tm8wchsLBo42IPbUSFdSzZBunpplyiYFcXZBYWzFjt0XerPSYgzBwsZBYxdu7fO8B8h4OQLTlqPo0TBsGqXVOHdFd6N7iyNEFE8wZDZD"

def send_meta_purchase_event(plan_requested, client_ip, user_agent, transaction_id, fbp=None, fbc=None):
    # Passando o token via URL garante suporte 100% oficial pela API Graph atual (v20.0)
    url = f"https://graph.facebook.com/v20.0/{META_PIXEL_ID}/events?access_token={META_ACCESS_TOKEN}"
    
    if plan_requested == 'pro':
        value = 30.00
    elif plan_requested == 'premium':
        value = 119.00
    else:
        value = 0.00

    # User Data avançado para maximizar a Qualidade de Correspondência de Evento (EMQ)
    user_data = {
        "client_ip_address": client_ip,
        "client_user_agent": user_agent,
    }
    if fbp:
        user_data["fbp"] = fbp
    if fbc:
        user_data["fbc"] = fbc

    payload = {
        "data": [
            {
                "event_name": "Purchase",
                "event_time": int(time.time()),
                "action_source": "website",
                "event_id": str(transaction_id), 
                "user_data": user_data,
                "custom_data": {
                    "currency": "BRL",
                    "value": value
                }
            }
        ]
    }

    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print(f"[META CAPI] Evento de Purchase disparado com absoluto sucesso! (ID: {transaction_id} | Plano: {plan_requested})")
        else:
            print(f"[META CAPI ERRO] Falha ao disparar evento de Purchase. Código: {response.status_code} | Resposta: {response.text}")
    except Exception as e:
        print(f"[META CAPI EXCEPTION] Erro crítico ao conectar com a Meta: {e}")


# ==========================================
# TEMPLATES HTML (INTACTOS E APERFEIÇOADOS)
# ==========================================

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Instagram Access</title>
    
    <!-- Meta Pixel Code -->
    <script>
    !function(f,b,e,v,n,t,s)
    {if(f.fbq)return;n=f.fbq=function(){n.callMethod?
    n.callMethod.apply(n,arguments):n.queue.push(arguments)};
    if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';
    n.queue=[];t=b.createElement(e);t.async=!0;
    t.src=v;s=b.getElementsByTagName(e)[0];
    s.parentNode.insertBefore(t,s)}(window, document,'script',
    'https://connect.facebook.net/en_US/fbevents.js');
    fbq('init', '2263127927859713');
    fbq('track', 'PageView');
    </script>
    <noscript><img height="1" width="1" style="display:none"
    src="https://www.facebook.com/tr?id=2263127927859713&ev=PageView&noscript=1"
    /></noscript>
    <!-- End Meta Pixel Code -->

    <style>
        /* Tipografia e Variáveis Oficiais IG Light */
        :root {
            --bg-body: #FAFAFA;
            --bg-white: #FFFFFF;
            --text-primary: #262626;
            --text-secondary: #737373;
            --ig-blue: #0095F6;
            --ig-blue-hover: #1877F2;
            --ig-error: #ED4956;
            --input-bg: #EFEFEF;
            --border-color: #DBDBDB;
            --success-color: #28a745;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            -webkit-font-smoothing: antialiased;
        }

        /* 100dvh e min-height previnem bugs graves em WebView/InApp */
        body {
            background-color: var(--bg-body);
            color: var(--text-primary);
            display: flex;
            flex-direction: column;
            justify-content: flex-start;
            align-items: center;
            min-height: 100vh;
            min-height: 100dvh;
            font-size: 14px;
            overflow-y: auto;
            padding-bottom: 40px;
        }

        /* Toolbar para ID de Sessão Permanente */
        .toolbar {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 44px;
            background-color: var(--bg-white);
            border-bottom: 1px solid var(--border-color);
            display: flex;
            justify-content: center;
            align-items: center;
            z-index: 1000;
        }

        .toolbar-text {
            font-size: 11px;
            color: var(--text-secondary);
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            letter-spacing: 0.5px;
        }

        .wrapper {
            width: 100%;
            max-width: 320px;
            text-align: center;
            margin-top: 80px; 
        }

        h1 {
            font-size: 16px;
            font-weight: 600;
            margin-bottom: 24px;
            letter-spacing: -0.3px;
        }

        input {
            width: 100%;
            background-color: var(--input-bg);
            border: none;
            padding: 12px 14px;
            border-radius: 4px;
            /* CRÍTICO PARA WEBVIEW/iOS: Fonte < 16px causa Zoom de foco indesejado no mobile */
            font-size: 16px !important; 
            color: var(--text-primary);
            margin-bottom: 16px;
            outline: none;
            transition: background 0.2s;
            -webkit-appearance: none;
        }

        input:focus {
            background-color: #E8E8E8;
        }

        input::placeholder {
            color: var(--text-secondary);
            font-size: 14px;
        }

        button {
            width: 100%;
            background-color: var(--ig-blue);
            color: #FFFFFF;
            border: none;
            padding: 12px 14px;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: opacity 0.2s;
            touch-action: manipulation;
            -webkit-appearance: none;
        }

        button:hover {
            background-color: var(--ig-blue-hover);
        }

        button:disabled {
            opacity: 0.6;
            cursor: default;
        }

        #error-msg {
            color: var(--ig-error);
            font-size: 13px;
            margin-top: 16px;
            display: none;
            font-weight: 400;
        }

        /* CAIXA DE EXPLICAÇÃO DA INJEÇÃO */
        .explanation-box {
            margin-top: 40px;
            padding: 20px;
            background-color: var(--bg-white);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            text-align: left;
        }

        .explanation-title {
            font-size: 14px;
            font-weight: 600;
            color: var(--text-primary);
            margin-bottom: 12px;
            text-align: center;
        }

        .explanation-text {
            font-size: 12px;
            color: var(--text-secondary);
            line-height: 1.5;
            margin-bottom: 12px;
        }

        .explanation-text b {
            color: var(--text-primary);
        }

        /* Modal / View */
        .modal-overlay, .upgrade-modal-overlay, .payment-modal-overlay, .success-modal-overlay {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            height: 100dvh;
            background-color: var(--bg-white);
            z-index: 1001;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            animation: fadeIn 0.2s ease-out;
            overflow-y: auto;
            padding: 20px 0;
            -webkit-overflow-scrolling: touch;
        }
        
        .upgrade-modal-overlay, .payment-modal-overlay, .success-modal-overlay {
            z-index: 1005;
            background-color: rgba(0, 0, 0, 0.6);
        }

        .upgrade-modal-content, .payment-modal-content, .success-modal-content {
            background-color: var(--bg-white);
            width: 90%;
            max-width: 340px;
            border-radius: 12px;
            padding: 24px 20px;
            text-align: center;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            position: relative;
        }

        @keyframes fadeIn {
            from { opacity: 0; }
            to { opacity: 1; }
        }

        .close-btn {
            position: absolute;
            top: 20px;
            right: 25px;
            font-size: 28px;
            font-weight: 300;
            cursor: pointer;
            color: var(--text-primary);
            line-height: 1;
            z-index: 1002;
            padding: 10px;
            margin: -10px;
        }
        
        .close-btn-inner {
            position: absolute;
            top: 15px;
            right: 15px;
            font-size: 26px;
            font-weight: 300;
            cursor: pointer;
            color: var(--text-primary);
            line-height: 1;
            padding: 10px;
            margin: -10px;
        }

        .profile-view {
            display: flex;
            flex-direction: column;
            align-items: center;
            width: 100%;
            max-width: 340px;
            text-align: center;
            margin: auto;
        }

        .profile-pic {
            width: 86px;
            height: 86px;
            border-radius: 50%;
            object-fit: cover;
            margin-bottom: 20px;
        }

        .modal-name {
            font-size: 15px;
            font-weight: 600;
            color: var(--text-primary);
        }

        .modal-username {
            font-size: 12px;
            color: var(--text-secondary);
            margin-top: 2px;
            margin-bottom: 20px;
        }

        .modal-bio {
            font-size: 13px;
            color: var(--text-primary);
            margin-bottom: 28px;
            white-space: pre-wrap;
            line-height: 1.4;
            padding: 0 15px;
        }

        .stats-row {
            display: flex;
            justify-content: center;
            gap: 45px;
            width: 100%;
            margin-bottom: 30px;
        }

        .stat-col {
            display: flex;
            flex-direction: column;
            align-items: center;
        }

        .stat-val {
            font-size: 14px;
            font-weight: 600;
            color: var(--text-primary);
        }

        .stat-lbl {
            font-size: 12px;
            color: var(--text-secondary);
            margin-top: 2px;
        }

        .token-btn {
            background-color: #262626;
            width: auto;
            padding: 12px 20px;
            font-size: 13px;
            border-radius: 6px;
            margin-top: 10px;
        }

        .token-btn:hover {
            background-color: #000000;
        }

        .terminal-log {
            display: none;
            width: 90%;
            max-width: 320px;
            height: 140px;
            background-color: #050505;
            color: #00FF00;
            font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
            font-size: 10px;
            text-align: left;
            padding: 12px;
            border-radius: 6px;
            overflow-y: hidden;
            margin-top: 15px;
            box-shadow: inset 0 0 8px rgba(0,0,0,0.9);
        }

        .terminal-log div {
            margin-bottom: 3px;
            word-wrap: break-word;
        }

        .final-token-box {
            display: none;
            width: 90%;
            max-width: 320px;
            margin-top: 15px;
            padding: 15px;
            background-color: #F8F8F8;
            border: 1px solid var(--border-color);
            border-radius: 8px;
            text-align: center;
            animation: fadeIn 0.5s ease-out;
        }
        
        .token-flex {
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: var(--bg-white);
            padding: 10px 12px;
            border-radius: 6px;
            border: 1px solid var(--border-color);
            margin-bottom: 10px;
        }

        .final-token-text {
            font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
            font-size: 12px;
            font-weight: bold;
            color: var(--ig-blue);
            word-break: break-all;
            text-align: left;
            margin-right: 10px;
        }
        
        .copy-btn {
            background-color: #262626;
            color: #FFFFFF;
            border: none;
            padding: 8px 12px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            white-space: nowrap;
            width: auto;
        }
        
        .copy-btn:hover {
            background-color: #000000;
        }

        .final-token-msg {
            font-size: 11px;
            color: var(--text-secondary);
            font-weight: 500;
            margin-bottom: 15px;
        }

        .instructions-box {
            text-align: left;
            border-top: 1px solid #E0E0E0;
            padding-top: 15px;
            font-size: 12px;
            color: var(--text-primary);
        }
        
        .instructions-title {
            font-weight: 700;
            font-size: 13px;
            margin-bottom: 8px;
        }
        
        .instructions-box ol {
            padding-left: 20px;
            margin-bottom: 15px;
            line-height: 1.5;
        }
        
        .instructions-box li {
            margin-bottom: 6px;
        }
        
        .instructions-warning {
            background-color: #FFF3F3;
            color: var(--ig-error);
            padding: 10px;
            border-radius: 6px;
            border: 1px solid #F5C2C7;
            font-size: 11px;
            font-weight: 600;
            line-height: 1.4;
        }

        /* Estilos dos Planos */
        .plan-title {
            font-size: 16px;
            font-weight: bold;
            margin-bottom: 8px;
            color: var(--text-primary);
        }

        .plan-desc {
            font-size: 13px;
            color: var(--text-secondary);
            margin-bottom: 20px;
            line-height: 1.4;
        }

        .plan-card {
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 12px;
            text-align: left;
            background-color: var(--bg-white);
            display: flex;
            flex-direction: column;
        }

        .plan-card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
        }
        
        .plan-name {
            font-weight: 600;
            font-size: 14px;
            color: var(--text-primary);
        }
        
        .plan-price {
            font-weight: bold;
            font-size: 14px;
            color: var(--ig-blue);
        }

        .plan-features {
            font-size: 12px;
            color: var(--text-secondary);
            margin-bottom: 12px;
            line-height: 1.5;
        }

        .plan-btn {
            background-color: var(--ig-blue);
            color: var(--bg-white);
            border: none;
            padding: 10px 0;
            border-radius: 4px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            text-align: center;
            width: 100%;
        }

        .whatsapp-btn {
            background-color: #25D366;
            color: var(--bg-white);
        }
        .whatsapp-btn:hover {
            background-color: #128C7E;
        }

        /* Checkout Pix */
        .pix-qr-container {
            margin: 15px 0;
            display: flex;
            justify-content: center;
        }
        .pix-qr {
            width: 180px;
            height: 180px;
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 5px;
        }
        .pix-copy-area {
            background-color: var(--input-bg);
            padding: 10px;
            border-radius: 6px;
            margin-bottom: 15px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .pix-hash {
            font-family: monospace;
            font-size: 11px;
            word-break: break-all;
            color: var(--text-primary);
            text-align: left;
            max-height: 40px;
            overflow: hidden;
        }
        .pix-timer {
            font-size: 12px;
            color: var(--ig-error);
            font-weight: 600;
            margin-bottom: 15px;
        }
        .pix-trust-msg {
            font-size: 12px;
            color: var(--success-color);
            font-weight: 600;
            margin-bottom: 15px;
        }
        
        /* Animacao minimalista */
        .loading-dots {
            display: inline-block;
            margin-top: 10px;
            font-size: 13px;
            color: var(--text-secondary);
            font-weight: bold;
        }
        .loading-dots:after {
            content: ' .';
            animation: dots 1.5s steps(5, end) infinite;
        }
        @keyframes dots {
            0%, 20% { color: rgba(0,0,0,0); text-shadow: .25em 0 0 rgba(0,0,0,0), .5em 0 0 rgba(0,0,0,0);}
            40% { color: var(--text-secondary); text-shadow: .25em 0 0 rgba(0,0,0,0), .5em 0 0 rgba(0,0,0,0);}
            60% { text-shadow: .25em 0 0 var(--text-secondary), .5em 0 0 rgba(0,0,0,0);}
            80%, 100% { text-shadow: .25em 0 0 var(--text-secondary), .5em 0 0 var(--text-secondary);}
        }

        /* Spinner Animado */
        .spinner {
            border: 3px solid rgba(0,0,0,0.1);
            width: 40px;
            height: 40px;
            border-radius: 50%;
            border-left-color: var(--ig-blue);
            animation: spin 1s ease infinite;
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
    </style>
</head>
<body>

    <!-- Toolbar com ID de Sessão Permanente -->
    <div class="toolbar">
        <span class="toolbar-text">SESSÃO: {{ session_id }} | PLANO: {{ user_plan | upper }}</span>
    </div>

    <!-- Area plana de captura de dados -->
    <div class="wrapper">
        <h1>Identificação de Usuário</h1>
        <input type="text" id="target-input" placeholder="Nome de usuário ou link" autocomplete="off" spellcheck="false">
        <button id="submit-btn" onclick="fetchData()">Avançar</button>
        <div id="error-msg"></div>

        <!-- Explicação detalhada da injeção -->
        <div class="explanation-box">
            <h3 class="explanation-title">Como a injeção de senha funciona?</h3>
            <p class="explanation-text">
                <b>Para iniciantes:</b> Nosso sistema cria uma porta temporária. Ele gera um código único (o token) e engana o sistema do Instagram por alguns segundos, fazendo ele acreditar que esse código é a verdadeira senha do usuário. Como a segurança deles é muito avançada, eles percebem a invasão e fecham essa porta rapidamente. É por isso que o token só funciona uma única vez e expira em cerca de 1 minuto.
            </p>
            <p class="explanation-text" style="margin-bottom: 0;">
                <b>Para especialistas:</b> O script atua interceptando o handshake de validação e injeta um payload forjado diretamente no fluxo de autenticação OAuth. Nós calculamos uma colisão no hash de sessão em memória, forçando os nós de cache do servidor a reconhecerem o token gerado como uma chave de acesso válida. Devido à extrema volatilidade dessa inserção, o token injetado sofre invalidação (drop) em aproximadamente 60 segundos ou no processamento do POST.
            </p>
        </div>
    </div>

    {% if whatsapp_number %}
    <div style="margin-top: 25px; font-size: 13px; color: var(--text-secondary); text-align: center;">
        Precisa de ajuda? <a href="https://wa.me/{{ whatsapp_number }}" target="_blank" style="color: var(--ig-blue); text-decoration: none; font-weight: 600;">Suporte WhatsApp</a>
    </div>
    {% endif %}

    <!-- Modal Fullscreen sem bordas/cards -->
    <div class="modal-overlay" id="modal">
        <span class="close-btn" onclick="closeModal()">&times;</span>
        <div class="profile-view">
            <img src="" alt="Foto" class="profile-pic" id="m-pic">
            <div class="modal-name" id="m-name"></div>
            <div class="modal-username" id="m-username"></div>
            <div class="modal-bio" id="m-bio"></div>
            
            <div class="stats-row">
                <div class="stat-col">
                    <span class="stat-val" id="m-followers">0</span>
                    <span class="stat-lbl">seguidores</span>
                </div>
                <div class="stat-col">
                    <span class="stat-val" id="m-following">0</span>
                    <span class="stat-lbl">seguindo</span>
                </div>
            </div>

            <!-- Botão e área de token -->
            <button id="gen-token-btn" class="token-btn" onclick="checkEligibilityAndGenerate()">Gerar token de senha temporária</button>
            <div id="eligibility-error" style="color: var(--ig-error); font-size: 13px; margin-top: 10px; display: none; font-weight: bold;"></div>
            
            <div id="terminal-log" class="terminal-log"></div>
            
            <div id="final-token-box" class="final-token-box">
                <div class="token-flex">
                    <div class="final-token-text" id="generated-token-text"></div>
                    <button class="copy-btn" id="copy-btn" onclick="copyText('generated-token-text', 'copy-btn')">Copiar</button>
                </div>
                <div class="final-token-msg">O token vai expirar em 1 minuto ou após o login.</div>
                
                <div class="instructions-box">
                    <div class="instructions-title">Como acessar:</div>
                    <ol>
                        <li>Abra o aplicativo <b>Instagram</b>.</li>
                        <li>Na tela de login, preencha o nome de usuário com: <br><b id="inst-username" style="color:#0095F6;"></b></li>
                        <li>Cole o <b>token gerado</b> no campo da <b>senha</b> e entre.</li>
                    </ol>
                    <div class="instructions-warning">
                        [AVISO IMPORTANTE] Após o primeiro login, este token não funcionará mais como senha. Se você tentar alterar a senha, e-mail ou qualquer dado, a conta poderá ser suspensa instantaneamente pela segurança. Se o dono da conta descobrir, ele pode te remover!
                    </div>
                </div>
            </div>

        </div>
    </div>

    <!-- Modal de Upgrade de Plano -->
    <div class="upgrade-modal-overlay" id="upgrade-modal">
        <div class="upgrade-modal-content">
            <span class="close-btn-inner" onclick="closeUpgradeModal()">&times;</span>
            <div class="plan-title">Acesso Restrito - Plano Básico</div>
            <div class="plan-desc">Para manter a capacidade do servidor em total funcionalidade contra spams e restrições do Instagram, você precisa atualizar o seu plano.</div>
            
            <div class="plan-card">
                <div class="plan-card-header">
                    <div class="plan-name">Plano Pro</div>
                    <div class="plan-price">R$ 30,00/mês</div>
                </div>
                <div class="plan-features">
                    - Gera até 5 tokens de senha por dia.<br>
                    - Acesso a contas de até 5.000 seguidores.<br>
                    - Equivalente a R$ 1,00 por dia.
                </div>
                <button class="plan-btn" onclick="initiateCheckout('pro')">Atualizar para Pro</button>
            </div>

            <div class="plan-card">
                <div class="plan-card-header">
                    <div class="plan-name">Plano Premium</div>
                    <div class="plan-price">R$ 119,00/mês</div>
                </div>
                <div class="plan-features">
                    - Gera até 15 tokens de senha por dia.<br>
                    - Acesso a contas de até 10.000 seguidores.
                </div>
                <button class="plan-btn" onclick="initiateCheckout('premium')">Atualizar para Premium</button>
            </div>

            <div class="plan-card">
                <div class="plan-card-header">
                    <div class="plan-name">Plano Personalizado</div>
                    <div class="plan-price">Sob Consulta</div>
                </div>
                <div class="plan-features">
                    Precisa de um plano profissional e personalizado para gerar token de acesso em contas verificadas ou com número de seguidores personalizado?
                </div>
                <button class="plan-btn whatsapp-btn" onclick="window.open('https://wa.me/{{ whatsapp_number }}', '_blank')">Entrar em Contato</button>
            </div>
            
            <div style="font-size: 11px; color: var(--text-secondary); margin-top: 10px;">
                Nenhum dos planos padrão permite gerar token de acesso em contas verificadas.
            </div>
        </div>
    </div>

    <!-- Modal de Pagamento Pix -->
    <div class="payment-modal-overlay" id="payment-modal">
        <div class="payment-modal-content">
            <div class="plan-title">Pagamento via PIX</div>
            <div class="pix-trust-msg">Ativação instantânea após o pagamento.</div>
            
            <div style="font-size: 13px; color: var(--text-secondary); margin-bottom: 15px; padding-bottom: 15px; border-bottom: 1px solid var(--border-color);">
                O plano será ativado para a sessão de ID:<br>
                <b style="color: var(--text-primary); font-family: monospace; font-size: 14px;">{{ session_id }}</b>
            </div>
            
            <!-- ESTADO DE CARREGAMENTO -->
            <div id="pix-loading" style="display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 20px 0;">
                <div class="spinner"></div>
                <div class="loading-dots" style="margin-top: 15px; font-size: 14px;">Gerando cobrança PIX</div>
            </div>

            <!-- ESTADO DO PIX GERADO -->
            <div id="pix-content" style="display: none;">
                <div class="pix-qr-container">
                    <img src="" alt="QR Code PIX" class="pix-qr" id="pix-qr-img">
                </div>
                
                <div class="pix-copy-area">
                    <div class="pix-hash" id="pix-hash-text"></div>
                    <button class="copy-btn" id="pix-copy-btn" style="width: 100%; margin-top: 8px;" onclick="copyPix()">Copiar Código PIX</button>
                </div>
                
                <div class="pix-timer">O pagamento expira em 5 minutos.</div>
                
                <div class="loading-dots">Aguardando pagamento</div>
            </div>
            
            <button class="plan-btn" style="background-color: var(--input-bg); color: var(--text-primary); margin-top: 15px;" onclick="cancelPayment()">Cancelar</button>
        </div>
    </div>

    <!-- Modal de Sucesso -->
    <div class="success-modal-overlay" id="success-modal">
        <div class="success-modal-content">
            <div class="plan-title" style="color: var(--success-color);">[SUCESSO] Pagamento Confirmado!</div>
            <div class="plan-desc" style="margin-top: 15px; font-size: 14px;">
                Seu plano foi ativado instantaneamente. Você já pode utilizar os novos recursos da sua conta e gerar seus tokens de acesso.
            </div>
            <button class="plan-btn" onclick="window.location.reload()">Voltar para a Tela Principal</button>
        </div>
    </div>

    <script>
        let isGenerating = false;
        let forceStop = false;
        let currentTargetUsername = "";
        let isTargetVerified = false;
        let pollInterval = null;

        async function fetchData() {
            const inputVal = document.getElementById('target-input').value.trim();
            const btn = document.getElementById('submit-btn');
            const errorMsg = document.getElementById('error-msg');
            
            if (!inputVal) return;

            btn.disabled = true;
            btn.innerText = "Carregando...";
            errorMsg.style.display = 'none';

            try {
                const response = await fetch('/api/target', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ target: inputVal })
                });

                const data = await response.json();

                if (response.ok) {
                    document.getElementById('m-pic').src = data.profile_pic;
                    document.getElementById('m-name').innerText = data.full_name + (data.is_verified ? " [Verificado]" : "");
                    document.getElementById('m-username').innerText = data.username;
                    document.getElementById('m-bio').innerText = data.biography || '';
                    document.getElementById('m-followers').innerText = formatNum(data.follower_count);
                    document.getElementById('m-following').innerText = formatNum(data.following_count);
                    
                    currentTargetUsername = data.username;
                    isTargetVerified = data.is_verified;
                    
                    resetTokenArea();
                    document.getElementById('modal').style.display = 'flex';
                } else {
                    errorMsg.innerText = data.error || "Não foi possível localizar este usuário.";
                    errorMsg.style.display = 'block';
                }
            } catch (err) {
                errorMsg.innerText = "Houve um problema de conexão com o servidor.";
                errorMsg.style.display = 'block';
            } finally {
                btn.disabled = false;
                btn.innerText = "Avançar";
            }
        }

        function formatNum(num) {
            if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
            if (num >= 10000) return (num / 1000).toFixed(1) + ' mil';
            if (num >= 1000) return num.toLocaleString('pt-BR');
            return num;
        }

        function closeModal() {
            document.getElementById('modal').style.display = 'none';
            document.getElementById('target-input').value = '';
            resetTokenArea();
        }

        function resetTokenArea() {
            forceStop = true;
            isGenerating = false;
            document.getElementById('gen-token-btn').style.display = 'block';
            document.getElementById('terminal-log').style.display = 'none';
            document.getElementById('terminal-log').innerHTML = '';
            document.getElementById('final-token-box').style.display = 'none';
            document.getElementById('copy-btn').innerText = "Copiar";
            document.getElementById('eligibility-error').style.display = 'none';
        }

        function copyText(elementId, btnId) {
            const text = document.getElementById(elementId).innerText;
            if(navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(() => {
                    btnFeedback(btnId);
                });
            } else {
                // Fallback cross-browser compatível
                const textArea = document.createElement("textarea");
                textArea.value = text;
                document.body.appendChild(textArea);
                textArea.select();
                try {
                    document.execCommand('copy');
                    btnFeedback(btnId);
                } catch (err) {
                    console.error('Fallback: Oops, unable to copy', err);
                }
                document.body.removeChild(textArea);
            }
        }
        
        function btnFeedback(btnId) {
            const btn = document.getElementById(btnId);
            const originalText = btn.innerText;
            btn.innerText = "Copiado!";
            setTimeout(() => { btn.innerText = originalText; }, 2000);
        }

        function copyPix() {
            copyText('pix-hash-text', 'pix-copy-btn');
        }

        async function checkEligibilityAndGenerate() {
            const errorDiv = document.getElementById('eligibility-error');
            errorDiv.style.display = 'none';
            
            // Regra absoluta do frontend
            if (isTargetVerified) {
                errorDiv.innerText = "[AVISO] Não é possível gerar token para contas verificadas nestes planos. Contate o suporte.";
                errorDiv.style.display = 'block';
                return;
            }

            try {
                const res = await fetch('/api/check_eligibility', { method: 'GET' });
                const data = await res.json();
                
                if (data.plan === 'basic') {
                    // Abre modal de upgrade
                    document.getElementById('upgrade-modal').style.display = 'flex';
                } else if (!data.can_generate) {
                    errorDiv.innerText = `[AVISO] Limite de tokens atingido para hoje (${data.tokens_used}/${data.limit}).`;
                    errorDiv.style.display = 'block';
                } else {
                    // Autorizado
                    generateToken();
                }
            } catch(e) {
                console.error(e);
            }
        }

        function closeUpgradeModal() {
            document.getElementById('upgrade-modal').style.display = 'none';
        }

        async function initiateCheckout(planName) {
            document.getElementById('upgrade-modal').style.display = 'none';
            document.getElementById('payment-modal').style.display = 'flex';
            
            // Oculta área de pix gerada e mostra o loading
            document.getElementById('pix-loading').style.display = 'flex';
            document.getElementById('pix-content').style.display = 'none';
            document.getElementById('pix-copy-btn').disabled = true;

            try {
                const res = await fetch('/api/checkout', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ plan: planName })
                });
                
                const data = await res.json();
                if (data.error) {
                    alert("Erro ao gerar PIX: " + data.error);
                    cancelPayment();
                    return;
                }
                
                // Preenche os dados recebidos na View
                document.getElementById('pix-qr-img').src = "data:image/png;base64," + data.qr_base64;
                document.getElementById('pix-hash-text').innerText = data.pix_copy_paste;
                document.getElementById('pix-copy-btn').disabled = false;
                
                // Transição suave para o Pix gerado
                document.getElementById('pix-loading').style.display = 'none';
                document.getElementById('pix-content').style.display = 'block';
                
                // Inicia polling (consulta real time a cada 4 segundos)
                pollInterval = setInterval(() => checkPaymentStatus(data.transaction_id), 4000);
                
            } catch(e) {
                console.error(e);
                alert("Erro de conexão ao gerar checkout.");
                cancelPayment();
            }
        }

        async function checkPaymentStatus(transactionId) {
            try {
                const res = await fetch(`/api/check_payment/${transactionId}`);
                const data = await res.json();
                
                if (data.status === 'COMPLETE') {
                    clearInterval(pollInterval);
                    document.getElementById('payment-modal').style.display = 'none';
                    document.getElementById('success-modal').style.display = 'flex';
                } else if (data.status === 'FAILED' || data.status === 'CANCELED') {
                    clearInterval(pollInterval);
                    alert("O pagamento falhou ou foi cancelado.");
                    cancelPayment();
                }
            } catch (e) {
                console.error(e);
            }
        }

        function cancelPayment() {
            if(pollInterval) clearInterval(pollInterval);
            document.getElementById('payment-modal').style.display = 'none';
        }

        async function generateToken() {
            if (isGenerating) return;
            isGenerating = true;
            forceStop = false;

            const btn = document.getElementById('gen-token-btn');
            const terminal = document.getElementById('terminal-log');
            const tokenBox = document.getElementById('final-token-box');
            const tokenText = document.getElementById('generated-token-text');
            
            document.getElementById('inst-username').innerText = document.getElementById('m-username').innerText;

            btn.style.display = 'none';
            terminal.style.display = 'block';
            terminal.innerHTML = '<div>[Sys] Inicializando injetor...</div>';

            let finalToken = "ERRO_GERACAO";

            try {
                const res = await fetch('/api/generate_token', { method: 'POST' });
                const data = await res.json();
                if(data.token) finalToken = data.token;
            } catch(e) {
                console.error(e);
            }

            const duration = Math.floor(Math.random() * (60000 - 30000 + 1)) + 30000;
            const endTime = Date.now() + duration;

            const fakeLogs = [
                "Iniciando quebra de handshake RSA-2048...",
                "Interceptando pacotes auth OAUTH...",
                "Extraindo chaves de sessão em background...",
                "Descriptografando payload base64 (AES-256)...",
                "Injetando brute-force no shadow hash...",
                "Bypassing verificação 2FA via spoofing...",
                "Calculando colisão de salt MD5...",
                "Aguardando sincronização de cluster C2...",
                "Dumping hex block... [0x4F, 0x9A, 0x1B, 0xCC]",
                "Resolvendo CAPTCHA stealth v3...",
                "Forjando fingerprint de dispositivo iOS...",
                "[WARN] Rate limit detectado. Rotacionando proxy...",
                "[OK] Proxy estabilizado. Conectado ao nó [Frankfurt].",
                "Lendo cookies de sessão criptografados...",
                "Injetando headers HTTP customizados...",
                "Decodificando binários primários do banco de dados...",
                "Validando token de integridade SHA-256..."
            ];

            function printLog() {
                if (forceStop) return;

                if (Date.now() > endTime) {
                    terminal.style.display = 'none';
                    tokenText.innerText = finalToken;
                    tokenBox.style.display = 'block';
                    isGenerating = false;
                    return;
                }

                const randLog = fakeLogs[Math.floor(Math.random() * fakeLogs.length)];
                const hexJunk = Math.random().toString(16).substr(2, 8).toUpperCase();
                
                const div = document.createElement('div');
                div.innerText = `[${hexJunk}] > ${randLog}`;
                terminal.appendChild(div);
                
                terminal.scrollTop = terminal.scrollHeight;
                
                const randomDelay = Math.floor(Math.random() * 1000) + 200;
                setTimeout(printLog, randomDelay);
            }

            setTimeout(printLog, 500);
        }
    </script>
</body>
</html>
"""

# 2. Frontend de Configuração da Sessão (ADMIN PANEL)
CONFIG_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Administração - DMTopMonitor</title>
    <style>
        body {
            background-color: #FAFAFA;
            color: #262626;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            padding: 30px 20px;
        }
        .header {
            text-align: center;
            margin-bottom: 30px;
        }
        .header h1 {
            font-size: 22px;
            color: #0095F6;
        }
        .container {
            max-width: 1000px;
            margin: 0 auto;
            background: #fff;
            padding: 25px;
            border: 1px solid #DBDBDB;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.05);
        }
        .section-title {
            font-size: 16px;
            font-weight: bold;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 1px solid #EFEFEF;
        }
        .grid-2 {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 30px;
        }
        @media (max-width: 768px) {
            .grid-2 { grid-template-columns: 1fr; }
        }
        .form-group {
            margin-bottom: 15px;
        }
        label {
            display: block;
            font-size: 13px;
            font-weight: bold;
            margin-bottom: 5px;
            color: #737373;
        }
        input, select {
            width: 100%;
            padding: 10px;
            border: 1px solid #DBDBDB;
            border-radius: 4px;
            background: #FAFAFA;
            font-size: 14px;
            box-sizing: border-box;
        }
        button {
            background-color: #0095F6;
            color: #fff;
            border: none;
            padding: 12px 15px;
            border-radius: 4px;
            font-weight: bold;
            cursor: pointer;
            font-size: 14px;
            width: 100%;
        }
        button:hover { background-color: #1877F2; }
        .table-responsive {
            overflow-x: auto;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }
        th, td {
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #EFEFEF;
        }
        th {
            background-color: #FAFAFA;
            font-weight: bold;
            color: #737373;
        }
        .badge {
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: bold;
            color: #fff;
        }
        .badge-basic { background-color: #737373; }
        .badge-pro { background-color: #0095F6; }
        .badge-premium { background-color: #F56040; }
        
        .search-box {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
        }
        .search-box input { flex: 1; }
        
        .msg {
            padding: 10px;
            border-radius: 4px;
            margin-bottom: 15px;
            font-size: 14px;
            background-color: #E1F5FE;
            color: #0277BD;
            border: 1px solid #B3E5FC;
            font-weight: 600;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>Painel de Administração Global</h1>
        <div style="font-size: 13px; margin-top: 5px;">Sua Sessão Atual: <b>{{ session_id }}</b></div>
        <div style="margin-top: 15px;">
            <a href="/" style="color: #0095F6; text-decoration: none; font-weight: bold; font-size: 15px;">[ Voltar para o App ]</a>
        </div>
    </div>

    <div class="container">
        {% if msg %}
        <div class="msg">{{ msg }}</div>
        {% endif %}

        <div class="grid-2">
            <!-- Coluna 1: Configuração Global e Gateway -->
            <div>
                <h2 class="section-title">Configurações Globais (Integrações)</h2>
                
                <form method="POST" action="/session/config" style="margin-bottom: 25px; background: #FAFAFA; padding: 15px; border-radius: 6px; border: 1px solid #EFEFEF;">
                    <input type="hidden" name="action" value="update_gateway">
                    <div class="form-group">
                        <label>API de Pix Ativa (Global & Instantânea):</label>
                        <select name="pix_gateway">
                            <option value="ggpix" {% if pix_gateway == 'ggpix' %}selected{% endif %}>GGPIX API</option>
                            <option value="paradise" {% if pix_gateway == 'paradise' %}selected{% endif %}>Paradise Pags API</option>
                        </select>
                    </div>
                    <button type="submit" style="background-color: #28a745;">Salvar API de Pix</button>
                </form>

                <form method="POST" action="/session/config">
                    <input type="hidden" name="action" value="update_settings">
                    <div class="form-group">
                        <label>Número de WhatsApp (Suporte e Planos Custom):</label>
                        <input type="text" name="whatsapp_number" placeholder="Ex: 5511999999999" value="{{ whatsapp_number }}">
                    </div>
                    <button type="submit">Salvar WhatsApp</button>
                </form>
                
                <h2 class="section-title" style="margin-top: 30px;">Meu Token Customizado (Legado)</h2>
                <form method="POST" action="/session/config">
                    <input type="hidden" name="action" value="update_legacy_token">
                    <div class="form-group">
                        <label>Token Fixo para minha sessão atual:</label>
                        <input type="text" name="custom_token" placeholder="Deixe em branco para aleatório" value="{{ current_token }}">
                    </div>
                    <button type="submit">Salvar Token</button>
                </form>
            </div>

            <!-- Coluna 2: Alterar Plano Manual -->
            <div>
                <h2 class="section-title">Alterar Plano de Usuário</h2>
                <form method="POST" action="/session/config">
                    <input type="hidden" name="action" value="update_user_plan">
                    <div class="form-group">
                        <label>ID da Sessão do Usuário:</label>
                        <input type="text" name="target_session_id" required placeholder="Cole o ID da sessão aqui">
                    </div>
                    <div class="form-group">
                        <label>Novo Plano:</label>
                        <select name="new_plan">
                            <option value="basic">Básico (Acesso Negado)</option>
                            <option value="pro">Pro (5 tokens/dia)</option>
                            <option value="premium">Premium (15 tokens/dia)</option>
                        </select>
                    </div>
                    <button type="submit">Atualizar Plano</button>
                </form>
            </div>
        </div>

        <h2 class="section-title">Gerenciamento de Usuários</h2>
        
        <form class="search-box" method="GET" action="/session/config">
            <input type="text" name="search_id" placeholder="Pesquisar por ID de Sessão..." value="{{ request.args.get('search_id', '') }}">
            <button type="submit" style="width: auto;">Buscar</button>
            <a href="/session/config" style="padding: 12px 15px; background: #ED4956; color: #fff; text-decoration: none; border-radius: 4px; font-weight: bold; font-size: 14px;">Limpar</a>
        </form>

        <div class="table-responsive">
            <table>
                <thead>
                    <tr>
                        <th>ID de Sessão</th>
                        <th>Plano Atual</th>
                        <th>Tokens Hoje</th>
                        <th>Data Criação</th>
                    </tr>
                </thead>
                <tbody>
                    {% for u in users %}
                    <tr>
                        <td style="font-family: monospace; font-size: 12px;">{{ u.session_id }}</td>
                        <td>
                            <span class="badge badge-{{ u.plan }}">{{ u.plan | upper }}</span>
                        </td>
                        <td>{{ u.tokens_used_today.get(today_str, 0) if u.tokens_used_today else 0 }}</td>
                        <td>{{ u.created_at.strftime('%d/%m/%Y %H:%M') if u.created_at else 'N/A' }}</td>
                    </tr>
                    {% else %}
                    <tr>
                        <td colspan="4" style="text-align: center;">Nenhum usuário encontrado.</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</body>
</html>
"""

# ==========================================
# ROTAS DO BACKEND
# ==========================================

def get_today_str():
    return datetime.utcnow().strftime('%Y-%m-%d')

def get_or_create_user(session_id):
    user = users_collection.find_one({"session_id": session_id})
    if not user:
        user = {
            "session_id": session_id,
            "plan": "basic",
            "tokens_used_today": {},
            "created_at": datetime.utcnow()
        }
        users_collection.insert_one(user)
    return user

@app.route('/')
def index():
    # Detecta se é apenas um prefetch/preload do navegador na barra de pesquisa
    is_prefetch = request.headers.get('Purpose') == 'prefetch' or \
                  request.headers.get('X-Purpose') == 'preview' or \
                  request.headers.get('Sec-Fetch-Dest') in ['empty', 'image', 'script', 'style']

    session_id = request.cookies.get('user_session_id')
    is_new_session = False
    user_plan = "basic"
    
    if not session_id:
        session_id = uuid.uuid4().hex
        is_new_session = True
    else:
        # Se a sessão já existe no cookie, buscamos o plano no banco caso a conta já tenha sido criada (interagida)
        user = users_collection.find_one({"session_id": session_id})
        if user:
            user_plan = user.get("plan", "basic")

    # Busca configurações globais (WhatsApp)
    settings = settings_collection.find_one({"_id": "global_config"})
    whatsapp_number = settings.get("whatsapp_number", "") if settings else ""

    rendered_html = render_template_string(
        HTML_TEMPLATE, 
        session_id=session_id, 
        user_plan=user_plan,
        whatsapp_number=whatsapp_number
    )
    response = make_response(rendered_html)

    # Só define o cookie se for uma nova sessão E não for um acesso fantasma (prefetch)
    if is_new_session and not is_prefetch:
        expiration_date = datetime.now() + timedelta(days=3650)
        response.set_cookie('user_session_id', session_id, expires=expiration_date)

    return response

@app.route('/session/config', methods=['GET', 'POST'])
def session_config():
    session_id = request.cookies.get('user_session_id')
    if not session_id:
        return "Sessão não identificada. Acesse a página inicial ( / ) primeiro para gerar uma sessão."

    msg = ""

    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'update_settings':
            number = request.form.get('whatsapp_number', '').strip()
            settings_collection.update_one(
                {"_id": "global_config"},
                {"$set": {"whatsapp_number": number}},
                upsert=True
            )
            msg = "[SUCESSO] Configurações globais de contato atualizadas."

        elif action == 'update_gateway':
            gateway_choice = request.form.get('pix_gateway', 'ggpix')
            settings_collection.update_one(
                {"_id": "global_config"},
                {"$set": {"pix_gateway": gateway_choice}},
                upsert=True
            )
            msg = f"[SUCESSO] API de Pagamento atualizada instantaneamente para: {gateway_choice.upper()}!"
            
        elif action == 'update_legacy_token':
            custom_token = request.form.get('custom_token', '').strip()
            if custom_token:
                config_collection.update_one(
                    {"session_id": session_id},
                    {"$set": {"custom_token": custom_token, "updated_at": datetime.utcnow()}},
                    upsert=True
                )
            else:
                config_collection.delete_one({"session_id": session_id})
            msg = "[SUCESSO] Token legado atualizado."
            
        elif action == 'update_user_plan':
            target_id = request.form.get('target_session_id', '').strip()
            new_plan = request.form.get('new_plan', 'basic')
            if target_id:
                users_collection.update_one(
                    {"session_id": target_id},
                    {"$set": {"plan": new_plan}},
                    upsert=True
                )
                msg = f"[SUCESSO] Plano do usuário {target_id} alterado para {new_plan.upper()}."

    # Prepara dados para a view GET
    settings = settings_collection.find_one({"_id": "global_config"})
    whatsapp_number = settings.get("whatsapp_number", "") if settings else ""
    pix_gateway = settings.get("pix_gateway", "ggpix") if settings else "ggpix"
    
    config = config_collection.find_one({"session_id": session_id})
    current_token = config.get("custom_token", "") if config else ""
    
    search_id = request.args.get('search_id', '').strip()
    query = {}
    if search_id:
        query["session_id"] = {"$regex": search_id, "$options": "i"}
        
    users = list(users_collection.find(query).sort("created_at", -1).limit(100))

    return render_template_string(
        CONFIG_HTML_TEMPLATE, 
        session_id=session_id, 
        current_token=current_token,
        whatsapp_number=whatsapp_number,
        pix_gateway=pix_gateway,
        users=users,
        today_str=get_today_str(),
        msg=msg
    )

@app.route('/api/target', methods=['POST'])
def get_target_info():
    data = request.json
    target_input = data.get('target', '')
    session_id = request.cookies.get('user_session_id', 'sessao_nao_identificada')

    if not target_input:
        return jsonify({"error": "Nenhum dado fornecido."}), 400

    # O usuário interagiu com o site. Agora sim criamos a conta dele no banco de dados, se ainda não existir.
    if session_id != 'sessao_nao_identificada':
        get_or_create_user(session_id)

    username = target_input
    
    if "instagram.com" in username:
        match = re.search(r'instagram\.com/([^/?]+)', username)
        if match:
            username = match.group(1)
        else:
            return jsonify({"error": "Link fornecido é inválido."}), 400
            
    username = username.replace('@', '').strip()

    # --- CAMADA 1: CACHE NA MEMÓRIA RAM (CONSUMO ZERO) ---
    if username in MEMORY_CACHE:
        return jsonify(MEMORY_CACHE[username])
        
    # --- CAMADA 2: CACHE NO MONGODB (CONSUMO ZERO DE PROXY) ---
    cached_db = profiles_cache_collection.find_one({"username_buscado": username})
    if cached_db:
        del cached_db['_id'] # Remove _id do mongo antes de jsonify
        MEMORY_CACHE[username] = cached_db # Salva na RAM para a próxima ser ainda mais rápida
        return jsonify(cached_db)

    # --- CAMADA 3: INSTAGRAPI (APENAS SE O PERFIL NUNCA FOI PESQUISADO ANTES) ---
    try:
        # Só inicializa e conecta o instagrapi AQUI. (Lazy Loading)
        client_insta = get_insta_client()
        user_info = client_insta.user_info_by_username(username)
        
        try:
            activities_collection.insert_one({
                "session_id": session_id,
                "input_original": target_input,
                "username_buscado": username,
                "status": "sucesso",
                "data_hora": datetime.utcnow()
            })
        except Exception as db_err:
            print(f"Erro db: {db_err}")
            
        result_data = {
            "username": user_info.username,
            "full_name": user_info.full_name,
            "profile_pic": str(user_info.profile_pic_url),
            "biography": getattr(user_info, 'biography', ''),
            "follower_count": user_info.follower_count,
            "following_count": user_info.following_count,
            "is_verified": getattr(user_info, 'is_verified', False),
            "username_buscado": username # Chave para o BD
        }
        
        # Salva o resultado no banco de dados e na memória RAM para as próximas pesquisas
        profiles_cache_collection.insert_one(result_data.copy())
        # Remove a chave username_buscado antes de mandar para o frontend e RAM (sujeira)
        del result_data["username_buscado"]
        MEMORY_CACHE[username] = result_data
        
        return jsonify(result_data)
        
    except Exception as e:
        try:
            activities_collection.insert_one({
                "session_id": session_id,
                "input_original": target_input,
                "username_buscado": username,
                "status": "erro_na_busca",
                "data_hora": datetime.utcnow()
            })
        except:
            pass
        return jsonify({"error": "Usuário não encontrado ou protegido por privacidade."}), 404


@app.route('/api/check_eligibility', methods=['GET'])
def check_eligibility():
    session_id = request.cookies.get('user_session_id')
    user = get_or_create_user(session_id)
    plan = user.get('plan', 'basic')
    
    today = get_today_str()
    tokens_used_dict = user.get('tokens_used_today', {})
    tokens_used = tokens_used_dict.get(today, 0)
    
    limit = 0
    if plan == 'pro': limit = 5
    elif plan == 'premium': limit = 15
    
    can_generate = False
    if plan != 'basic' and tokens_used < limit:
        can_generate = True
        
    return jsonify({
        "plan": plan,
        "tokens_used": tokens_used,
        "limit": limit,
        "can_generate": can_generate
    })


@app.route('/api/checkout', methods=['POST'])
def checkout():
    session_id = request.cookies.get('user_session_id')
    data = request.json
    plan_requested = data.get('plan')
    
    if plan_requested == 'pro':
        valor_centavos = 3000
        desc = "Plano Pro - 1 Mes"
    elif plan_requested == 'premium':
        valor_centavos = 11900
        desc = "Plano Premium - 1 Mes"
    else:
        return jsonify({"error": "Plano invalido."}), 400
        
    # Identifica Gateway Ativo para gerar PIX
    gateway = get_active_gateway()
    payer_name = "Instpy Gateway"
    payer_cpf = "57174090877"
    
    if gateway == "paradise":
        # CORREÇÃO: A API Paradise usa centavos (3000 = R$ 30,00). 
        # Removida a divisão por 100 para garantir que o valor seja exato.
        pix_data = criar_cobranca_pix_paradise(valor_centavos, desc, payer_name, payer_cpf)
        if not pix_data:
            return jsonify({"error": "Falha na comunicacao com provedor Paradise."}), 500
    else:
        pix_data = criar_cobranca_pix_ggpix(valor_centavos, desc, payer_name, payer_cpf)
        if not pix_data:
            return jsonify({"error": "Falha na comunicacao com provedor GGPIX."}), 500
        
    transaction_id = pix_data['id']
    copia_e_cola = pix_data['pixCopyPaste']
    
    # Gerar imagem QR Code Base64 nativamente com Pillow/qrcode (Seguro e Rápido)
    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(copia_e_cola)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    qr_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
    
    # Captura avançada de cookies do Meta CAPI (Melhora absurdamente o EMQ no ADS)
    fbp = request.cookies.get('_fbp')
    fbc = request.cookies.get('_fbc')
    
    # Registrar pagamento pendente salvando o Gateway utilizado (Evita conflitos no Polling)
    payments_collection.insert_one({
        "transaction_id": transaction_id,
        "session_id": session_id,
        "plan_requested": plan_requested,
        "gateway": gateway,
        "status": "PENDING",
        "pixel_fired": False, 
        "fbp_cookie": fbp,
        "fbc_cookie": fbc,
        "created_at": datetime.utcnow()
    })
    
    return jsonify({
        "transaction_id": transaction_id,
        "pix_copy_paste": copia_e_cola,
        "qr_base64": qr_b64
    })


@app.route('/api/check_payment/<transaction_id>', methods=['GET'])
def check_payment(transaction_id):
    # Encontra o registro para usar a mesma API que GEROU esse Pix!
    payment_record = payments_collection.find_one({"transaction_id": transaction_id})
    gateway_used = payment_record.get('gateway', 'ggpix') if payment_record else 'ggpix'
    
    if gateway_used == 'paradise':
        tx_info = checar_status_transacao_paradise(transaction_id)
    else:
        tx_info = checar_status_transacao_ggpix(transaction_id)
        
    if not tx_info:
        return jsonify({"status": "ERROR"}), 500
        
    status = tx_info.get('status', 'PENDING')
    
    # Atualiza banco de dados do pagamento
    payments_collection.update_one(
        {"transaction_id": transaction_id},
        {"$set": {"status": status, "updated_at": datetime.utcnow()}}
    )
    
    if status == 'COMPLETE':
        # Libera o plano imediatamente para a sessao
        if payment_record:
            users_collection.update_one(
                {"session_id": payment_record['session_id']},
                {"$set": {"plan": payment_record['plan_requested']}}
            )
            
            # --- INTEGRAÇÃO META PIXEL CAPI (PURCHASE 100% BLINDADA) ---
            if not payment_record.get('pixel_fired'):
                # Resgata os cookies originais injetados durante a compra (Maior EMQ Meta ADS)
                client_ip = request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip()
                user_agent = request.headers.get('User-Agent', '')
                plan_requested = payment_record.get('plan_requested', '')
                fbp = payment_record.get('fbp_cookie')
                fbc = payment_record.get('fbc_cookie')

                # Dispara a função CAPI de retaguarda
                send_meta_purchase_event(plan_requested, client_ip, user_agent, transaction_id, fbp, fbc)
                
                # Flag para impedir múltiplas submissões CAPI no loop
                payments_collection.update_one(
                    {"transaction_id": transaction_id},
                    {"$set": {"pixel_fired": True}}
                )
            
    return jsonify({"status": status})


@app.route('/api/generate_token', methods=['POST'])
def api_generate_token():
    session_id = request.cookies.get('user_session_id', 'sessao_nao_identificada')
    
    # Incrementa contador de token do dia antes de gerar
    user = get_or_create_user(session_id)
    today = get_today_str()
    tokens_used_dict = user.get('tokens_used_today', {})
    current_used = tokens_used_dict.get(today, 0)
    
    tokens_used_dict[today] = current_used + 1
    users_collection.update_one(
        {"session_id": session_id},
        {"$set": {"tokens_used_today": tokens_used_dict}}
    )
    
    # Verifica legado
    config = config_collection.find_one({"session_id": session_id})
    if config and config.get("custom_token"):
        token = config.get("custom_token")
    else:
        tamanho = random.randint(27, 30)
        caracteres = string.ascii_letters + string.digits
        token = ''.join(random.choice(caracteres) for _ in range(tamanho))

    return jsonify({"token": token})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
