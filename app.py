from flask import Flask, request, jsonify, render_template_string, make_response, redirect, abort, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix
from curl_cffi import requests as curl_requests
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
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote
from bson import ObjectId

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# ==========================================
# CONFIGURAÇÃO DO MONGODB
# ==========================================
MONGO_URI = "mongodb+srv://seyzalel_db_user:q4dKhbwPQwBcmEFZ@dmtopmonitor.dnbpdnd.mongodb.net/?appName=DMTopMonitor"

try:
    mongo_client = MongoClient(MONGO_URI)
    db = mongo_client["dmtopmonitor"]
    activities_collection = db["user_activities"]
    config_collection = db["session_configs"]
    users_collection = db["users"]
    settings_collection = db["settings"]
    payments_collection = db["payments"]
    profiles_cache_collection = db["profiles_cache"]
    client_settings_collection = db["client_settings"]
    notifications_collection = db["notifications"]
    print("Sistema integrado e conectado ao MongoDB com sucesso.")
except Exception as e:
    print(f"Aviso de sistema: Falha na conexão com o MongoDB. Detalhe: {e}")

# ==========================================
# CIRCUIT BREAKER GLOBAL (PROTEÇÃO DEFINITIVA DE PROXY)
# ==========================================
CIRCUIT_BREAKER_LOCKED = False
CIRCUIT_BREAKER_TIME = 0
CIRCUIT_BREAKER_COOLDOWN = 2065

# ==========================================
# CACHE EM MEMÓRIA RAM
# ==========================================
MEMORY_CACHE = {}

# ==========================================
# SISTEMA DE PROTEÇÃO ANTI-BOT E FIREWALL
# ==========================================
IP_HISTORY = defaultdict(list)
RATE_LIMIT_MAX_REQ = 15
RATE_LIMIT_WINDOW = 60
BANNED_IPS = set()
IP_BANS = {}
BAN_TIME = 600
SUSPICIOUS_UA_REGEX = re.compile(
    r'(spider|crawler|scraper|wget|urllib|libwww|httpclient|'
    r'headless|phantom|selenium|puppeteer|playwright|cypress|slurp|yahoo|yandex|baidu|teoma|alexa|'
    r'nikto|nmap|sqlmap|mechanize|scrapy|zgrab|nucleus|httpx|masscan)',
    re.IGNORECASE
)

@app.before_request
def bot_firewall():
    if request.path.startswith('/api/webhook'):
        return

    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if client_ip:
        client_ip = client_ip.split(',')[0].strip()
    else:
        client_ip = 'unknown'
        
    now = time.time()
    
    if client_ip in IP_BANS:
        if now < IP_BANS[client_ip]:
            abort(403, description="Acesso negado: IP bloqueado por comportamento suspeito (Anti-Bot Ativado).")
        else:
            del IP_BANS[client_ip]
            
    IP_HISTORY[client_ip] = [timestamp for timestamp in IP_HISTORY[client_ip] if now - timestamp < RATE_LIMIT_WINDOW]
    IP_HISTORY[client_ip].append(now)
    
    if len(IP_HISTORY[client_ip]) > RATE_LIMIT_MAX_REQ:
        IP_BANS[client_ip] = now + BAN_TIME
        abort(429, description="Too Many Requests: Tráfego anômalo detectado. Proteção de proxy ativada.")
        
    ua = request.headers.get('User-Agent', '')
    if SUSPICIOUS_UA_REGEX.search(ua):
        IP_BANS[client_ip] = now + BAN_TIME
        abort(403, description="Acesso negado: Assinatura de Bot/Scraper detectada.")

# ==========================================
# CONFIGURAÇÃO CURL_CFFI (SUBSTITUIÇÃO DA INSTAGRAPI)
# ==========================================
PROXY_URL = "http://59022cd6d5de707a8016__cr.br:8e5efe0790f47cda@gw.dataimpulse.com:10000"
# Mantivemos a variável SESSION_ID apenas para o hash do sticky proxy, não há login.
SESSION_ID_FOR_PROXY = "36894112352%3AkH9qq7hOdv4CoT%3A12%3AAYj6CLYR0iKPUTE1-UXlnH8DWslHxYo6J4Ayfr--EA"

def get_sticky_proxy(identifier):
    try:
        if "@" in PROXY_URL:
            protocol, rest = PROXY_URL.split('://')
            credentials, address = rest.split('@')
            user, pwd = credentials.split(':', 1)
            
            sid_hash = hashlib.md5(identifier.encode()).hexdigest()[:8]
            if "__session_" not in user:
                sticky_user = f"{user}__session_{sid_hash}"
                return f"{protocol}://{sticky_user}:{pwd}@{address}"
    except Exception:
        pass
    return PROXY_URL

def fetch_instagram_profile(username, session_identifier):
    global CIRCUIT_BREAKER_LOCKED, CIRCUIT_BREAKER_TIME
    
    if CIRCUIT_BREAKER_LOCKED:
        if time.time() - CIRCUIT_BREAKER_TIME < CIRCUIT_BREAKER_COOLDOWN:
            raise Exception("CIRCUIT_BREAKER_ACTIVE")
        else:
            CIRCUIT_BREAKER_LOCKED = False

    url = f"https://i.instagram.com/api/v1/users/web_profile_info/?username={username}"
    
    headers = {
        "x-ig-app-id": "936619743392459",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Origin": "https://www.instagram.com",
        "Referer": f"https://www.instagram.com/{username}/",
    }
    
    sticky_proxy = get_sticky_proxy(session_identifier)
    proxies = {
        "http": sticky_proxy,
        "https": sticky_proxy
    }
    
    try:
        print(f"[CURL_CFFI] Buscando dados de '{username}' via proxy pegajoso...")
        response = curl_requests.get(url, headers=headers, impersonate="chrome", proxies=proxies, timeout=12)
        
        if response.status_code == 200:
            data = response.json()
            if "data" in data and "user" in data["data"]:
                return data["data"]["user"]
            else:
                raise Exception("JSON Response is missing user data structure.")
        elif response.status_code == 404:
            raise Exception("UserNotFound")
        elif response.status_code == 429:
            CIRCUIT_BREAKER_LOCKED = True
            CIRCUIT_BREAKER_TIME = time.time()
            raise Exception("TooManyRequests - Proxy bloqueado")
        else:
            raise Exception(f"HTTP Error {response.status_code}")
            
    except Exception as e:
        print(f"[CURL_CFFI ERRO] Falha ao buscar '{username}': {e}")
        raise e


# ==========================================
# CONFIGURAÇÃO GGPIXAPI
# ==========================================
GGPIX_API_KEY = "gk_64425202f1286434cc2c5f2cab27b6952edc30caa87e3a77"
GGPIX_BASE_URL = "https://ggpixapi.com/api/v1"
GGPIX_HEADERS = {
    "Content-Type": "application/json",
    "X-API-Key": GGPIX_API_KEY
}

def criar_cobranca_pix_ggpix(valor_centavos, descricao, nome_pagador, cpf_pagador, webhook_url=None, tracking=None):
    url = f"{GGPIX_BASE_URL}/pix/in"
    external_id = f"DMReporting-{uuid.uuid4().hex[:8]}"
    payload = {
        "amountCents": valor_centavos,
        "description": descricao,
        "payerName": nome_pagador,
        "payerDocument": cpf_pagador,
        "externalId": external_id
    }
    if webhook_url:
        payload["webhookUrl"] = webhook_url
    if tracking:
        payload["tracking"] = tracking

    try:
        response = requests.post(url, headers=GGPIX_HEADERS, json=payload, timeout=12)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as err:
        resp_text = response.text if 'response' in locals() and response is not None else ''
        print(f"Erro ao gerar PIX (GGPIX): {err} - Response: {resp_text}")
        return None

def checar_status_transacao_ggpix(transaction_id):
    url = f"{GGPIX_BASE_URL}/transactions/{transaction_id}"
    try:
        response = requests.get(url, headers=GGPIX_HEADERS, timeout=8)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as err:
        print(f"Erro ao checar status PIX (GGPIX): {err}")
        return None

# ==========================================
# CONFIGURAÇÃO PARADISE PAGS API
# ==========================================
PARADISE_API_KEY = "sk_e33108ee283d27bbb9dc5954de0a1b9f3678fab039190efe2a55e57e93879903"
PARADISE_BASE_URL = "https://oferta-processamento.org.ua/api/v1"

def criar_cobranca_pix_paradise(valor_centavos, descricao, nome_pagador, cpf_pagador, webhook_url=None, tracking=None):
    url = f"{PARADISE_BASE_URL}/transaction.php"
    headers = {
        "X-API-Key": PARADISE_API_KEY,
        "Content-Type": "application/json"
    }
    
    external_id_ref = f"REF-{uuid.uuid4().hex[:8]}"
    
    payload = {
        "amount": valor_centavos,
        "description": descricao,
        "reference": external_id_ref,
        "source": "api_externa",
        "customer": {
            "name": nome_pagador,
            "email": "suporte@instpygateway.com",
            "phone": "11999999999",
            "document": cpf_pagador
        }
    }
    
    if webhook_url:
        payload["postback_url"] = webhook_url
        
    if tracking:
        payload["tracking"] = tracking

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=12)
        response.raise_for_status()
        transaction_data = response.json()
        
        if transaction_data.get("status") == "success":
            return {
                "id": str(transaction_data.get("transaction_id")), 
                "externalId": transaction_data.get("id"),
                "pixCopyPaste": transaction_data.get("qr_code")
            }
        else:
            print(f"Erro Paradise API: Retorno não foi success. Response: {transaction_data}")
            return None
    except requests.exceptions.RequestException as e:
        resp_text = response.text if 'response' in locals() and response is not None else ''
        print(f"Falha critica (Paradise): {e} | Resposta: {resp_text}")
        return None

def checar_status_transacao_paradise(transaction_id):
    url = f"{PARADISE_BASE_URL}/query.php?action=get_transaction&id={transaction_id}"
    headers = {"X-API-Key": PARADISE_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=8)
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
    return "ggpix"

# ==========================================
# CONFIGURAÇÃO META PIXEL & CAPI SÊNIOR
# ==========================================
META_PIXEL_ID = "2263127927859713"
META_ACCESS_TOKEN = "EAAY3pJasrWUBSBFHDzEBUyZCKXQSILBT6o0fhuPipIUp3KUg0BtZBcSsIJM4BRJFHrwnJb55wtEjbigGFlx2ZAFyN4DpxI02Tm8wchsLBo42IPbUSFdSzZBunpplyiYFcXZBYWzFjt0XerPSYgzBwsZBYxdu7fO8B8h4OQLTlqPo0TBsGqXVOHdFd6N7iyNEFE8wZDZD"

def send_meta_purchase_event(plan_requested, client_ip, user_agent, transaction_id, fbp=None, fbc=None):
    url = f"https://graph.facebook.com/v20.0/{META_PIXEL_ID}/events?access_token={META_ACCESS_TOKEN}"
    
    if plan_requested == 'pro':
        value = 28.00
    elif plan_requested == 'premium':
        value = 60.00
    else:
        value = 0.00

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
        response = requests.post(url, json=payload, timeout=8)
        if response.status_code == 200:
            print(f"[META CAPI] Evento de Purchase disparado com absoluto sucesso! (ID: {transaction_id} | Plano: {plan_requested})")
        else:
            print(f"[META CAPI ERRO] Falha ao disparar evento de Purchase. Código: {response.status_code} | Resposta: {response.text}")
    except Exception as e:
        print(f"[META CAPI EXCEPTION] Erro crítico ao conectar com a Meta: {e}")

# ==========================================
# COMPONENTE DE NOTIFICAÇÃO
# ==========================================
NOTIFICATION_COMPONENT_HTML = """
<style>
    .notif-bell-wrapper { position: fixed; top: 55px; right: 20px; z-index: 999; cursor: pointer; display: flex; align-items: center; justify-content: center; }
    .notif-badge { display: none; position: absolute; top: -4px; right: -6px; background: #ED4956; color: white; font-size: 10px; font-weight: 700; border-radius: 50%; padding: 2px 5px; line-height: 1; box-shadow: 0 1px 3px rgba(0,0,0,0.2); pointer-events: none; }
    .notif-modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; height: 100dvh; background: rgba(0,0,0,0.6); z-index: 10001; flex-direction: column; justify-content: center; align-items: center; animation: fadeIn 0.2s ease-out; }
    .notif-modal-content { background: var(--bg-white, #FFFFFF); width: 90%; max-width: 380px; max-height: 80vh; border-radius: 12px; display: flex; flex-direction: column; box-shadow: 0 10px 30px rgba(0,0,0,0.2); overflow: hidden; }
    .notif-header { padding: 18px 20px; border-bottom: 1px solid var(--border-color, #DBDBDB); display: flex; justify-content: space-between; align-items: center; background: #FAFAFA; }
    .notif-title-main { font-size: 16px; font-weight: 700; color: var(--text-primary, #262626); margin: 0; letter-spacing: -0.3px; }
    .notif-close { font-size: 26px; cursor: pointer; color: var(--text-secondary, #737373); line-height: 1; font-weight: 300; padding: 5px; margin: -5px; transition: color 0.2s; }
    .notif-close:hover { color: var(--text-primary, #262626); }
    .notif-body { overflow-y: auto; flex: 1; background: var(--bg-white, #FFFFFF); padding: 0; }
    .notif-item { padding: 16px 20px; border-bottom: 1px solid var(--border-color, #DBDBDB); transition: background 0.2s; text-align: left; }
    .notif-item:last-child { border-bottom: none; }
    .notif-item.unread { background: rgba(0, 149, 246, 0.04); border-left: 3px solid #0095F6; padding-left: 17px; }
    .notif-item-title { font-weight: 600; font-size: 14px; color: var(--text-primary, #262626); margin-bottom: 4px; }
    .notif-item-msg { font-size: 13px; color: var(--text-secondary, #737373); line-height: 1.45; margin-bottom: 8px; white-space: pre-wrap; word-break: break-word; }
    .notif-item-time { font-size: 11px; color: #a8a8a8; font-weight: 500; display: flex; align-items: center; gap: 4px; }
    .notif-empty { padding: 40px 20px; text-align: center; color: var(--text-secondary, #737373); font-size: 14px; }
</style>

<div class="notif-bell-wrapper" onclick="openNotifModal()">
    <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color: var(--text-primary, #262626);">
        <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"></path>
        <path d="M13.73 21a2 2 0 0 1-3.46 0"></path>
    </svg>
    <div class="notif-badge" id="global-notif-badge">0</div>
</div>

<div class="notif-modal-overlay" id="global-notif-modal">
    <div class="notif-modal-content">
        <div class="notif-header">
            <h2 class="notif-title-main">Notificações</h2>
            <span class="notif-close" onclick="closeNotifModal()">&times;</span>
        </div>
        <div class="notif-body" id="notif-list-container">
            <div class="notif-empty">
                <div style="width: 20px; height: 20px; border: 2px solid #DBDBDB; border-top-color: #737373; border-radius: 50%; animation: spin 1s linear infinite; margin: 0 auto 10px auto;"></div>
                Carregando...
            </div>
        </div>
    </div>
</div>

<script>
    let unreadNotifIds = [];

    function linkify(text) {
        const urlRegex = /(https?:\/\/[^\s]+)/g;
        return text.replace(urlRegex, function(url) {
            return `<a href="${url}" target="_blank" style="color: #0095F6; text-decoration: underline; text-decoration-thickness: 1px; cursor: pointer;">${url}</a>`;
        });
    }

    function formatRelativeTime(dateString) {
        const d = new Date(dateString);
        const now = new Date();
        const diffMs = now - d;
        
        const diffSecs = Math.floor(diffMs / 1000);
        const diffMins = Math.floor(diffSecs / 60);
        const diffHours = Math.floor(diffMins / 60);
        const diffDays = Math.floor(diffHours / 24);

        if (diffDays >= 7) {
            const day = String(d.getDate()).padStart(2, '0');
            const month = String(d.getMonth() + 1).padStart(2, '0');
            const year = d.getFullYear();
            return `${day}/${month}/${year}`;
        }

        if (diffSecs < 60) return diffSecs <= 1 ? "há 1 segundo atrás" : `há ${diffSecs} segundos atrás`;
        if (diffMins < 60) return diffMins === 1 ? "há 1 minuto atrás" : `há ${diffMins} minutos atrás`;
        if (diffHours < 24) return diffHours === 1 ? "há 1 hora atrás" : `há ${diffHours} horas atrás`;
        return diffDays === 1 ? "há 1 dia atrás" : `há ${diffDays} dias atrás`;
    }

    async function fetchNotifs() {
        try {
            const res = await fetch('/api/notifications');
            const data = await res.json();
            
            const badge = document.getElementById('global-notif-badge');
            if (data.unread_count > 0) {
                badge.innerText = data.unread_count > 99 ? '99+' : data.unread_count;
                badge.style.display = 'block';
            } else {
                badge.style.display = 'none';
            }

            unreadNotifIds = data.notifications.filter(n => !n.is_read).map(n => n.id);
            
            const container = document.getElementById('notif-list-container');
            if (data.notifications.length === 0) {
                container.innerHTML = '<div class="notif-empty">Você não possui notificações recentes.</div>';
                return;
            }

            container.innerHTML = data.notifications.map(n => `
                <div class="notif-item ${n.is_read ? '' : 'unread'}">
                    <div class="notif-item-title">${n.title}</div>
                    <div class="notif-item-msg">${linkify(n.message)}</div>
                    <div class="notif-item-time">
                        ${formatRelativeTime(n.created_at)}
                    </div>
                </div>
            `).join('');
            
        } catch (e) {
            console.error("Erro ao buscar notificações", e);
        }
    }

    function openNotifModal() {
        document.getElementById('global-notif-modal').style.display = 'flex';
        if (unreadNotifIds.length > 0) {
            fetch('/api/notifications/read', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ids: unreadNotifIds })
            }).then(() => {
                document.getElementById('global-notif-badge').style.display = 'none';
                unreadNotifIds = [];
                document.querySelectorAll('.notif-item.unread').forEach(el => {
                    setTimeout(() => el.classList.remove('unread'), 1500);
                });
                fetchNotifs();
            }).catch(err => {
                console.error("Erro ao marcar notificações como lidas", err);
                fetchNotifs();
            });
        }
    }

    function closeNotifModal() {
        document.getElementById('global-notif-modal').style.display = 'none';
    }

    document.addEventListener("DOMContentLoaded", fetchNotifs);
    setInterval(fetchNotifs, 60000); 
</script>
"""

# ==========================================
# TEMPLATES HTML (MANTIDOS 100% INTACTOS)
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
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            -webkit-font-smoothing: antialiased;
        }

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

        .modal-overlay, .upgrade-modal-overlay, .success-modal-overlay {
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
        
        .upgrade-modal-overlay, .success-modal-overlay {
            z-index: 1005;
            background-color: rgba(0, 0, 0, 0.6);
        }

        .upgrade-modal-content, .success-modal-content {
            background-color: var(--bg-white);
            width: 90%;
            max-width: 340px;
            border-radius: 12px;
            padding: 24px 20px;
            text-align: center;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            position: relative;
        }

        #payment-modal {
            z-index: 1005;
            background-color: var(--bg-body);
        }
        .payment-view {
            display: flex;
            flex-direction: column;
            align-items: center;
            width: 100%;
            max-width: 340px;
            text-align: center;
            margin: auto;
            background-color: var(--bg-white);
            padding: 30px 20px;
            border-radius: 12px;
            border: 1px solid var(--border-color);
            box-shadow: 0 4px 12px rgba(0,0,0,0.05);
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

        .cpf-input {
            width: 100%;
            background-color: var(--bg-white);
            border: 1px solid var(--border-color);
            padding: 14px;
            border-radius: 6px;
            font-size: 16px !important;
            margin-bottom: 12px;
            text-align: center;
            letter-spacing: 1px;
            color: var(--text-primary);
            box-shadow: inset 0 1px 3px rgba(0,0,0,0.05);
        }
        .cpf-input:focus {
            border-color: var(--ig-blue);
            background-color: var(--bg-white);
        }
        .privacy-notice {
            font-size: 11px;
            color: var(--text-secondary);
            margin-bottom: 20px;
            line-height: 1.4;
            text-align: left;
            background-color: #F9F9F9;
            padding: 12px;
            border-radius: 6px;
            border: 1px solid var(--border-color);
        }

        .pix-qr-container {
            margin: 20px auto;
            padding: 18px;
            background: linear-gradient(145deg, #ffffff, #f0f0f0);
            border-radius: 20px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.08), inset 0 1px 0 rgba(255,255,255,0.8);
            display: inline-block;
            border: 1px solid #EFEFEF;
        }
        .pix-qr {
            width: 190px;
            height: 190px;
            border-radius: 12px;
            display: block;
        }
        
        .pix-copy-area {
            background-color: var(--bg-white);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            margin-bottom: 25px;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            box-shadow: 0 4px 12px rgba(0,0,0,0.03);
        }
        .pix-hash-container {
            padding: 14px 16px;
            background-color: #FAFAFA;
            border-bottom: 1px solid var(--border-color);
        }
        .pix-hash {
            font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
            font-size: 13px;
            word-break: break-all;
            color: var(--text-primary);
            text-align: left;
            max-height: 48px;
            overflow: hidden;
            line-height: 1.4;
        }
        
        .pix-copy-action {
            padding: 12px;
            background-color: var(--bg-white);
        }
        #pix-copy-btn {
            width: 100%;
            padding: 14px;
            font-size: 14px;
            border-radius: 8px;
            background-color: var(--ig-blue);
            color: white;
            font-weight: 600;
            transition: background 0.2s;
            border: none;
            cursor: pointer;
        }
        #pix-copy-btn:hover { background-color: var(--ig-blue-hover); }

        .pix-timer {
            font-size: 13px;
            color: var(--text-secondary);
            font-weight: 500;
            margin-bottom: 20px;
        }
        
        .pix-trust-msg {
            font-size: 14px;
            color: var(--text-primary);
            font-weight: 600;
            margin-bottom: 10px;
        }
        
        .waiting-payment-badge {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            background-color: #FFDE00;
            color: #111;
            padding: 12px 20px;
            border-radius: 30px;
            font-size: 13px;
            font-weight: 600;
            box-shadow: 0 4px 15px rgba(255, 222, 0, 0.4);
            animation: pulse-yellow 2s infinite;
            margin: 10px 0 15px 0;
        }
        @keyframes pulse-yellow {
            0% { box-shadow: 0 0 0 0 rgba(255, 222, 0, 0.6); }
            70% { box-shadow: 0 0 0 12px rgba(255, 222, 0, 0); }
            100% { box-shadow: 0 0 0 0 rgba(255, 222, 0, 0); }
        }
        .waiting-spinner {
            border: 2px solid rgba(0,0,0,0.1);
            width: 16px;
            height: 16px;
            border-radius: 50%;
            border-left-color: #111;
            animation: spin 1s linear infinite;
            margin-right: 10px;
        }

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
    </style>
</head>
<body>

    <div class="toolbar">
        <span class="toolbar-text">SESSÃO: {{ session_id }} | PLANO: {{ user_plan | upper }}</span>
    </div>

    <div class="wrapper">
        <h1>Identificação de Usuário</h1>
        <input type="text" id="target-input" placeholder="Nome de usuário ou link" autocomplete="off" spellcheck="false">
        <button id="submit-btn" onclick="fetchData()">Avançar</button>
        <div id="error-msg"></div>

        <div class="explanation-box">
            <h3 class="explanation-title">Como a injeção de senha funciona?</h3>
            <p class="explanation-text">
                <b>Para iniciantes:</b> Nosso sistema cria uma porta temporária. Ele gera um código único (o token) e engana o sistema do Instagram por alguns segundos, fazendo ele acreditar que esse código é a verdadeira senha do usuário. Como a segurança deles é muito avançada, eles percebem a invasão e fecham essa porta rapidamente. É por isso que o token só funciona uma única vez e expira em cerca de 1 minuto.
            </p>
            <p class="explanation-text" style="margin-bottom: 15px;">
                <b>Para especialistas:</b> O script atua interceptando o handshake de validação e injeta um payload forjado diretamente no fluxo de autenticação OAuth. Nós calculamos uma colisão no hash de sessão em memória, forçando os nós de cache do servidor a reconhecerem o token gerado como uma chave de acesso válida. Devido à extrema volatilidade dessa inserção, o token injetado sofre invalidação (drop) em aproximadamente 60 segundos ou no processamento do POST.
            </p>
            
            <div style="width: 100%; border-radius: 8px; overflow: hidden; margin-top: 15px; border: 1px solid var(--border-color); background-color: #000;">
                <video width="100%" controls playsinline preload="metadata" poster="/tutorial/thumbnail.PNG" style="display: block;">
                    <source src="/tutorial/instpy_tutorial.mp4" type="video/mp4">
                    Seu navegador não suporta a tag de vídeo.
                </video>
            </div>
        </div>
    </div>

    {% if whatsapp_number %}
    <div style="margin-top: 25px; font-size: 13px; color: var(--text-secondary); text-align: center;">
        Precisa de ajuda? <a href="https://wa.me/{{ whatsapp_number }}" target="_blank" style="color: var(--ig-blue); text-decoration: none; font-weight: 600;">Suporte WhatsApp</a>
    </div>
    {% endif %}

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

    <div class="upgrade-modal-overlay" id="upgrade-modal">
        <div class="upgrade-modal-content">
            <span class="close-btn-inner" onclick="closeUpgradeModal()">&times;</span>
            <div class="plan-title">Acesso Restrito - Plano Básico</div>
            <div class="plan-desc">Para manter a capacidade do servidor em total funcionalidade contra spams e restrições do Instagram, você precisa atualizar o seu plano.</div>
            
            <div class="plan-card">
                <div class="plan-card-header">
                    <div class="plan-name">Plano Pro</div>
                    <div class="plan-price">R$ 50,00/2 meses</div>
                </div>
                <div class="plan-features">
                    Gera até 10 tokens de senha por dia.<br>
                    Acesse 10 contas por dia/até 5.000 seguidores.<br>
                    Equivalente a R$ 0,83 centavos por dia.
                </div>
                <button class="plan-btn" onclick="initiateCheckout('pro')">Atualizar para Pro</button>
            </div>

            <div class="plan-card">
                <div class="plan-card-header">
                    <div class="plan-name">Plano Premium</div>
                    <div class="plan-price">R$ 79,00/4 meses</div>
                </div>
                <div class="plan-features">
                    Gera até 20 tokens de senha por dia.<br>
                    Acesse 20 contas por dia/até 15.000 seguidores.
                </div>
                <button class="plan-btn" onclick="initiateCheckout('premium')">Atualizar para Premium</button>
            </div>

            <div class="plan-card">
                <div class="plan-card-header">
                    <div class="plan-name">Plano Personalizado</div>
                    <div class="plan-price">Sob Consulta</div>
                </div>
                <div class="plan-features">
                    Precisa de um plano com permissões avançadas e exlusivas para acessar contas maiores?
                </div>
                <button class="plan-btn whatsapp-btn" onclick="window.open('https://wa.me/{{ whatsapp_number }}', '_blank')">Entrar em Contato</button>
            </div>
            
            <div style="font-size: 11px; color: var(--text-secondary); margin-top: 10px;">
                Nenhum dos planos padrão permite gerar token de acesso em contas verificadas.
            </div>
        </div>
    </div>

    <div class="modal-overlay" id="payment-modal">
        <span class="close-btn" onclick="cancelPayment()">&times;</span>
        <div class="payment-view">
            
            <div id="cpf-step" style="width: 100%;">
                <div class="plan-title" style="font-size: 18px; margin-bottom: 10px;">Dados de Pagamento</div>
                <div class="plan-desc" style="margin-bottom: 20px;">Para processar seu PIX com segurança e garantir a aprovação instantânea junto ao Banco Central, informe seu CPF.</div>
                
                <input type="tel" id="user-cpf" class="cpf-input" placeholder="Digite seu CPF (Somente números)" maxlength="14" autocomplete="off">
                
                <div class="privacy-notice">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: text-bottom; margin-right: 4px; color: var(--text-secondary);"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path></svg> <b>Privacidade e Segurança:</b> Seu CPF é utilizado estritamente para o processamento e aprovação do pagamento. Nós <b>não armazenamos</b> este dado em nossos servidores, garantindo total sigilo e proteção da sua identidade.
                </div>
                
                <button class="plan-btn" style="padding: 14px; font-size: 15px;" onclick="processCheckout()">Gerar PIX Agora</button>
            </div>

            <div id="pix-loading" style="display: none; flex-direction: column; align-items: center; justify-content: center; padding: 40px 0; width: 100%;">
                <div class="spinner"></div>
                <div class="loading-dots" style="margin-top: 20px; font-size: 14px;">Conectando ao Banco Central</div>
            </div>

            <div id="pix-content" style="display: none; width: 100%;">
                <div class="plan-title" style="font-size: 20px; margin-bottom: 8px;">Pagamento via PIX</div>
                <div class="pix-trust-msg">Ativação instantânea após o pagamento.</div>
                
                <div style="font-size: 13px; color: var(--text-secondary); margin-bottom: 10px; padding-bottom: 15px; border-bottom: 1px solid var(--border-color);">
                    O plano será ativado para a sessão:<br>
                    <b style="color: var(--text-primary); font-family: monospace; font-size: 14px;">{{ session_id }}</b>
                </div>
                
                <div class="pix-qr-container">
                    <img src="" alt="QR Code PIX" class="pix-qr" id="pix-qr-img">
                </div>
                
                <div class="pix-copy-area">
                    <div class="pix-hash-container">
                        <div class="pix-hash" id="pix-hash-text"></div>
                    </div>
                    <div class="pix-copy-action">
                        <button id="pix-copy-btn" onclick="copyPix()">Copiar Código PIX</button>
                    </div>
                </div>
                
                <div class="waiting-payment-badge">
                    <div class="waiting-spinner"></div>
                    Aguardando confirmação do banco...
                </div>
                
                <div class="pix-timer">O código expira em 15 minutos.</div>
            </div>

        </div>
    </div>

    <div class="success-modal-overlay" id="success-modal">
        <div class="success-modal-content">
            <div class="plan-title" style="color: #28a745;">[SUCESSO] Pagamento Confirmado!</div>
            <div class="plan-desc" style="margin-top: 15px; font-size: 14px;">
                Seu plano foi ativado instantaneamente. Você já pode utilizar os novos recursos da sua conta e gerar seus tokens de acesso.
            </div>
            <button class="plan-btn" onclick="window.location.reload()">Voltar para a Tela Principal</button>
        </div>
    </div>

    {{ notification_component | safe }}

    <script>
        let isGenerating = false;
        let forceStop = false;
        let currentTargetUsername = "";
        let isTargetVerified = false;
        let pollInterval = null;
        let selectedPlan = "";
        let currentActiveTxId = null;

        document.addEventListener("visibilitychange", function() {
            if (document.visibilityState === "visible" && currentActiveTxId) {
                checkPaymentStatus(currentActiveTxId);
            }
        });

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
                    // MUDANÇA DA PROXY IMPLEMENTADA AQUI
                    document.getElementById('m-pic').src = '/api/proxy_image?url=' + encodeURIComponent(data.profile_pic);
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
                const textArea = document.createElement("textarea");
                textArea.value = text;
                document.body.appendChild(textArea);
                textArea.select();
                try {
                    document.execCommand('copy');
                    btnFeedback(btnId);
                } catch (err) {
                    console.error('Fallback: unable to copy', err);
                }
                document.body.removeChild(textArea);
            }
        }
        
        function btnFeedback(btnId) {
            const btn = document.getElementById(btnId);
            const originalText = btn.innerText;
            btn.innerText = "Copiado com Sucesso!";
            setTimeout(() => { btn.innerText = originalText; }, 2000);
        }

        function copyPix() {
            copyText('pix-hash-text', 'pix-copy-btn');
        }

        async function checkEligibilityAndGenerate() {
            const errorDiv = document.getElementById('eligibility-error');
            errorDiv.style.display = 'none';
            
            if (isTargetVerified) {
                errorDiv.innerText = "[AVISO] Não é possível gerar token para contas verificadas nestes planos. Contate o suporte.";
                errorDiv.style.display = 'block';
                return;
            }

            try {
                const res = await fetch('/api/check_eligibility', { method: 'GET' });
                const data = await res.json();
                
                if (data.plan === 'basic') {
                    document.getElementById('upgrade-modal').style.display = 'flex';
                } else if (!data.can_generate) {
                    errorDiv.innerText = `[AVISO] Limite de tokens atingido para hoje (${data.tokens_used}/${data.limit}).`;
                    errorDiv.style.display = 'block';
                } else {
                    generateToken();
                }
            } catch(e) {
                console.error(e);
            }
        }

        function closeUpgradeModal() {
            document.getElementById('upgrade-modal').style.display = 'none';
        }

        function initiateCheckout(planName) {
            selectedPlan = planName;
            document.getElementById('upgrade-modal').style.display = 'none';
            document.getElementById('payment-modal').style.display = 'flex';
            
            document.getElementById('cpf-step').style.display = 'block';
            document.getElementById('pix-loading').style.display = 'none';
            document.getElementById('pix-content').style.display = 'none';
            document.getElementById('user-cpf').value = '';
        }

        async function processCheckout() {
            const cpfInput = document.getElementById('user-cpf').value.replace(/\D/g, '');
            
            if (cpfInput.length !== 11 && cpfInput.length !== 14) {
                alert("Por favor, insira um CPF ou CNPJ válido contendo 11 ou 14 números.");
                return;
            }

            document.getElementById('cpf-step').style.display = 'none';
            document.getElementById('pix-loading').style.display = 'flex';

            try {
                const res = await fetch('/api/checkout', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ plan: selectedPlan, cpf: cpfInput })
                });
                
                const data = await res.json();
                if (data.error) {
                    alert("Erro ao gerar PIX: " + data.error);
                    cancelPayment();
                    return;
                }
                
                currentActiveTxId = data.transaction_id;

                document.getElementById('pix-qr-img').src = "data:image/png;base64," + data.qr_base64;
                document.getElementById('pix-hash-text').innerText = data.pix_copy_paste;
                
                document.getElementById('pix-loading').style.display = 'none';
                document.getElementById('pix-content').style.display = 'block';
                
                if (pollInterval) clearInterval(pollInterval);
                pollInterval = setInterval(() => checkPaymentStatus(data.transaction_id), 3500);
                
            } catch(e) {
                console.error(e);
                alert("Erro de conexão ao gerar checkout. Verifique sua internet.");
                cancelPayment();
            }
        }

        async function checkPaymentStatus(transactionId) {
            if (!transactionId) return;
            try {
                const res = await fetch(`/api/check_payment/${transactionId}`);
                const data = await res.json();
                
                if (data.status === 'COMPLETE') {
                    clearInterval(pollInterval);
                    currentActiveTxId = null;
                    document.getElementById('payment-modal').style.display = 'none';
                    document.getElementById('success-modal').style.display = 'flex';
                } else if (data.status === 'FAILED' || data.status === 'CANCELED') {
                    clearInterval(pollInterval);
                    currentActiveTxId = null;
                    alert("O pagamento falhou ou expirou.");
                    cancelPayment();
                }
            } catch (e) {
                console.error(e);
            }
        }

        function cancelPayment() {
            if(pollInterval) clearInterval(pollInterval);
            currentActiveTxId = null;
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
            <a href="/" style="color: #0095F6; text-decoration: none; font-weight: bold; font-size: 15px;">[ Voltar para o App ]</a> |
            <a href="/notification/client/config" style="color: #0095F6; text-decoration: none; font-weight: bold; font-size: 15px;">[ Gerenciar Notificações ]</a>
        </div>
    </div>

    <div class="container">
        {% if msg %}
        <div class="msg">{{ msg }}</div>
        {% endif %}

        <div class="grid-2">
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
                
                <h2 class="section-title" style="margin-top: 30px;">Engenharia Social (Dados Falsos)</h2>
                <form method="POST" action="/session/config">
                    <input type="hidden" name="action" value="update_custom_stats">
                    <div class="grid-2" style="margin-bottom: 0; gap: 10px;">
                        <div class="form-group">
                            <label>Seguidores (Ex: 15000):</label>
                            <input type="text" name="custom_followers" placeholder="Deixe em branco para real" value="{{ custom_followers }}">
                        </div>
                        <div class="form-group">
                            <label>Seguindo (Ex: 250):</label>
                            <input type="text" name="custom_following" placeholder="Deixe em branco para real" value="{{ custom_following }}">
                        </div>
                    </div>
                    <button type="submit">Salvar Dados de Perfil</button>
                </form>
            </div>

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

NOTIFICATION_ADMIN_HTML = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Administração de Notificações</title>
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
            padding: 30px;
            border: 1px solid #DBDBDB;
            border-radius: 8px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.05);
        }
        h2 { font-size: 18px; margin-bottom: 20px; border-bottom: 1px solid #EFEFEF; padding-bottom: 10px; color: #262626; }
        .form-group { margin-bottom: 18px; }
        label { display: block; font-size: 13px; font-weight: bold; margin-bottom: 6px; color: #737373; }
        input, textarea { width: 100%; padding: 12px; border: 1px solid #DBDBDB; border-radius: 6px; font-family: inherit; font-size: 14px; box-sizing: border-box; background: #FAFAFA; transition: border-color 0.2s; }
        input:focus, textarea:focus { border-color: #0095F6; outline: none; background: #FFF; }
        .btn { background: #0095F6; color: #fff; border: none; padding: 12px 20px; border-radius: 6px; font-weight: bold; cursor: pointer; font-size: 14px; transition: opacity 0.2s; }
        .btn:hover { opacity: 0.85; }
        .btn-danger { background: #ED4956; }
        .btn-warning { background: #F56040; }
        .btn-secondary { background: #737373; }
        table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 10px; }
        th, td { padding: 14px; text-align: left; border-bottom: 1px solid #EFEFEF; }
        th { background: #FAFAFA; color: #737373; font-weight: bold; }
        .msg { padding: 12px; background: #E1F5FE; color: #0277BD; border: 1px solid #B3E5FC; border-radius: 6px; margin-bottom: 20px; font-weight: 600; }
        .actions-flex { display: flex; gap: 8px; }
        .status-active { color: #28a745; font-weight: bold; }
        .status-inactive { color: #ED4956; font-weight: bold; }
    </style>
</head>
<body>
    <div class="header">
        <h1>Gerenciamento de Notificações Globais</h1>
        <div style="margin-top: 15px;">
            <a href="/" style="color: #0095F6; text-decoration: none; font-weight: bold; font-size: 14px;">[ Voltar para o App ]</a> &nbsp;|&nbsp;
            <a href="/session/config" style="color: #0095F6; text-decoration: none; font-weight: bold; font-size: 14px;">[ Voltar para Painel Admin ]</a>
        </div>
    </div>
    
    <div class="container">
        {% if msg %}
        <div class="msg">{{ msg }}</div>
        {% endif %}

        <h2>{{ 'Editando Notificação' if edit_notif else 'Criar Nova Notificação' }}</h2>
        <form method="POST">
            <input type="hidden" name="action" value="{{ 'edit' if edit_notif else 'create' }}">
            {% if edit_notif %}
            <input type="hidden" name="id" value="{{ edit_notif._id }}">
            {% endif %}
            
            <div class="form-group">
                <label>Título da Notificação:</label>
                <input type="text" name="title" required value="{{ edit_notif.title if edit_notif else '' }}" placeholder="Ex: Nova Atualização de Sistema!">
            </div>
            <div class="form-group">
                <label>Corpo da Mensagem:</label>
                <textarea name="message" rows="4" required placeholder="Digite a mensagem completa que o usuário irá ler...">{{ edit_notif.message if edit_notif else '' }}</textarea>
            </div>
            <div style="display: flex; gap: 10px;">
                <button type="submit" class="btn">{{ 'Salvar Alterações' if edit_notif else 'Disparar Notificação' }}</button>
                {% if edit_notif %}
                <a href="/notification/client/config"><button type="button" class="btn btn-secondary">Cancelar Edição</button></a>
                {% endif %}
            </div>
        </form>

        <h2 style="margin-top: 50px;">Histórico de Notificações</h2>
        <div style="overflow-x: auto;">
            <table>
                <thead>
                    <tr>
                        <th width="15%">Data / Hora</th>
                        <th width="25%">Título</th>
                        <th width="35%">Mensagem (Prévia)</th>
                        <th width="10%">Status</th>
                        <th width="15%">Ações</th>
                    </tr>
                </thead>
                <tbody>
                    {% for n in notifications %}
                    <tr>
                        <td>{{ n.created_at.strftime('%d/%m/%Y %H:%M') }}</td>
                        <td><b>{{ n.title }}</b></td>
                        <td style="max-width: 250px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">{{ n.message }}</td>
                        <td>
                            {% if n.is_active %}
                            <span class="status-active">Ativo</span>
                            {% else %}
                            <span class="status-inactive">Oculto</span>
                            {% endif %}
                        </td>
                        <td class="actions-flex">
                            <form method="POST" style="margin: 0;">
                                <input type="hidden" name="action" value="toggle">
                                <input type="hidden" name="id" value="{{ n._id }}">
                                <button type="submit" class="btn btn-secondary" style="font-size: 11px; padding: 6px 10px;">
                                    {{ 'Ocultar' if n.is_active else 'Exibir' }}
                                </button>
                            </form>
                            <a href="?edit_id={{ n._id }}">
                                <button type="button" class="btn btn-warning" style="font-size: 11px; padding: 6px 10px;">Editar</button>
                            </a>
                            <form method="POST" style="margin: 0;" onsubmit="return confirm('Tem certeza absoluta que deseja apagar essa notificação do banco de dados?');">
                                <input type="hidden" name="action" value="delete">
                                <input type="hidden" name="id" value="{{ n._id }}">
                                <button type="submit" class="btn btn-danger" style="font-size: 11px; padding: 6px 10px;">Apagar</button>
                            </form>
                        </td>
                    </tr>
                    {% else %}
                    <tr>
                        <td colspan="5" style="text-align: center; padding: 30px; color: #737373;">Nenhuma notificação enviada no histórico.</td>
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

@app.route('/tutorial/<path:filename>')
def serve_tutorial(filename):
    base_dir = os.path.abspath(os.path.dirname(__file__))
    tutorial_dir = os.path.join(base_dir, 'tutorial')
    return send_from_directory(tutorial_dir, filename)

def get_today_str():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')

def get_or_create_user(session_id):
    user = users_collection.find_one({"session_id": session_id})
    if not user:
        user = {
            "session_id": session_id,
            "plan": "basic",
            "tokens_used_today": {},
            "read_notifications": [],
            "created_at": datetime.now(timezone.utc)
        }
        users_collection.insert_one(user)
    return user

@app.route('/')
def index():
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
        user = users_collection.find_one({"session_id": session_id})
        if user:
            user_plan = user.get("plan", "basic")

    settings = settings_collection.find_one({"_id": "global_config"})
    whatsapp_number = settings.get("whatsapp_number", "") if settings else ""

    rendered_html = render_template_string(
        HTML_TEMPLATE, 
        session_id=session_id, 
        user_plan=user_plan,
        whatsapp_number=whatsapp_number,
        notification_component=NOTIFICATION_COMPONENT_HTML
    )
    response = make_response(rendered_html)

    if is_new_session and not is_prefetch:
        expiration_date = datetime.now(timezone.utc) + timedelta(days=3650)
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
                    {"$set": {"custom_token": custom_token, "updated_at": datetime.now(timezone.utc)}},
                    upsert=True
                )
            else:
                config_collection.update_one(
                    {"session_id": session_id},
                    {"$unset": {"custom_token": ""}, "$set": {"updated_at": datetime.now(timezone.utc)}},
                    upsert=True
                )
            msg = "[SUCESSO] Token legado atualizado."
            
        elif action == 'update_custom_stats':
            custom_followers = request.form.get('custom_followers', '').strip()
            custom_following = request.form.get('custom_following', '').strip()
            
            config_collection.update_one(
                {"session_id": session_id},
                {"$set": {
                    "custom_followers": custom_followers,
                    "custom_following": custom_following,
                    "updated_at": datetime.now(timezone.utc)
                }},
                upsert=True
            )
            msg = "[SUCESSO] Engenharia Social de Seguidores e Seguindo atualizada."
            
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

    settings = settings_collection.find_one({"_id": "global_config"})
    whatsapp_number = settings.get("whatsapp_number", "") if settings else ""
    pix_gateway = settings.get("pix_gateway", "ggpix") if settings else "ggpix"
    
    config = config_collection.find_one({"session_id": session_id})
    current_token = config.get("custom_token", "") if config else ""
    custom_followers = config.get("custom_followers", "") if config else ""
    custom_following = config.get("custom_following", "") if config else ""
    
    search_id = request.args.get('search_id', '').strip()
    query = {}
    if search_id:
        query["session_id"] = {"$regex": search_id, "$options": "i"}
        
    users = list(users_collection.find(query).sort("created_at", -1).limit(100))

    return render_template_string(
        CONFIG_HTML_TEMPLATE, 
        session_id=session_id, 
        current_token=current_token,
        custom_followers=custom_followers,
        custom_following=custom_following,
        whatsapp_number=whatsapp_number,
        pix_gateway=pix_gateway,
        users=users,
        today_str=get_today_str(),
        msg=msg
    )

@app.route('/notification/client/config', methods=['GET', 'POST'])
def notification_admin():
    msg = ""
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'create':
            title = request.form.get('title')
            message = request.form.get('message')
            notifications_collection.insert_one({
                "title": title,
                "message": message,
                "is_active": True,
                "created_at": datetime.now(timezone.utc)
            })
            msg = "[SUCESSO] Nova notificação global enviada!"
            
        elif action == 'edit':
            nid = request.form.get('id')
            title = request.form.get('title')
            message = request.form.get('message')
            notifications_collection.update_one(
                {"_id": ObjectId(nid)},
                {"$set": {"title": title, "message": message}}
            )
            msg = "[SUCESSO] Notificação alterada."
            
        elif action == 'toggle':
            nid = request.form.get('id')
            notif = notifications_collection.find_one({"_id": ObjectId(nid)})
            if notif:
                notifications_collection.update_one(
                    {"_id": ObjectId(nid)},
                    {"$set": {"is_active": not notif.get("is_active", True)}}
                )
                msg = "[SUCESSO] Status de exibição alterado."
                
        elif action == 'delete':
            nid = request.form.get('id')
            notifications_collection.delete_one({"_id": ObjectId(nid)})
            msg = "[SUCESSO] Notificação apagada da base de dados permanentemente."

    edit_id = request.args.get('edit_id')
    edit_notif = None
    if edit_id:
        edit_notif = notifications_collection.find_one({"_id": ObjectId(edit_id)})
        
    all_notifs = list(notifications_collection.find().sort("created_at", -1))
    
    return render_template_string(
        NOTIFICATION_ADMIN_HTML,
        notifications=all_notifs,
        msg=msg,
        edit_notif=edit_notif
    )

@app.route('/api/notifications', methods=['GET'])
def api_get_notifications():
    session_id = request.cookies.get('user_session_id')
    user = get_or_create_user(session_id) if session_id else {}
    read_nots = user.get('read_notifications', [])

    notifs = list(notifications_collection.find({"is_active": True}).sort("created_at", -1))
    
    result = []
    unread_count = 0
    for n in notifs:
        nid = str(n['_id'])
        if nid not in read_nots:
            unread_count += 1
        result.append({
            "id": nid,
            "title": n['title'],
            "message": n['message'],
            "created_at": n['created_at'].isoformat() + "Z",
            "is_read": nid in read_nots
        })
    
    return jsonify({"notifications": result, "unread_count": unread_count})

@app.route('/api/notifications/read', methods=['POST'])
def api_mark_notifications_read():
    session_id = request.cookies.get('user_session_id')
    if not session_id:
        return jsonify({"error": "Sessão não identificada"}), 400

    data = request.get_json(silent=True) or {}
    ids = data.get('ids', [])
    if not isinstance(ids, list):
        return jsonify({"error": "IDs inválidos"}), 400

    user = get_or_create_user(session_id)
    current_read = user.get('read_notifications', [])
    
    updated = False
    for nid in ids:
        if nid not in current_read:
            current_read.append(nid)
            updated = True
    
    if updated:
        users_collection.update_one(
            {"session_id": session_id},
            {"$set": {"read_notifications": current_read}}
        )
    
    return jsonify({"success": True, "marked_read": len(ids)})


def _apply_custom_stats(data_dict, sess_id):
    if sess_id and sess_id != 'sessao_nao_identificada':
        config = config_collection.find_one({"session_id": sess_id})
        if config:
            c_followers = str(config.get("custom_followers", "")).strip()
            c_following = str(config.get("custom_following", "")).strip()
            if c_followers:
                try:
                    data_dict["follower_count"] = int(c_followers)
                except ValueError:
                    data_dict["follower_count"] = c_followers
            if c_following:
                try:
                    data_dict["following_count"] = int(c_following)
                except ValueError:
                    data_dict["following_count"] = c_following
    return data_dict

@app.route('/api/target', methods=['POST'])
def get_target_info():
    global CIRCUIT_BREAKER_LOCKED, CIRCUIT_BREAKER_TIME
    
    if CIRCUIT_BREAKER_LOCKED:
        if time.time() - CIRCUIT_BREAKER_TIME < CIRCUIT_BREAKER_COOLDOWN:
            return jsonify({"error": "[ALERTA DE SEGURANÇA] O Instagram identificou risco e bloqueou o acesso temporariamente. Aguarde alguns minutos."}), 403
        else:
            CIRCUIT_BREAKER_LOCKED = False
            
    data = request.json
    target_input = data.get('target', '')
    session_id = request.cookies.get('user_session_id', 'sessao_nao_identificada')

    if not target_input:
        return jsonify({"error": "Nenhum dado fornecido."}), 400

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

    if username in MEMORY_CACHE:
        cached_data = MEMORY_CACHE[username].copy()
        cached_data = _apply_custom_stats(cached_data, session_id)
        return jsonify(cached_data)
        
    cached_db = profiles_cache_collection.find_one({"username_buscado": username})
    if cached_db:
        del cached_db['_id']
        MEMORY_CACHE[username] = cached_db.copy()
        
        cached_data = cached_db.copy()
        cached_data = _apply_custom_stats(cached_data, session_id)
        return jsonify(cached_data)

    try:
        time.sleep(random.uniform(1.2, 2.5))
        
        # AQUI OCORRE A SUBSTITUIÇÃO DA CHAMADA INSTAGRAPI PARA CURL_CFFI
        user_info = fetch_instagram_profile(username, session_id)
        
        try:
            activities_collection.insert_one({
                "session_id": session_id,
                "input_original": target_input,
                "username_buscado": username,
                "status": "sucesso",
                "data_hora": datetime.now(timezone.utc)
            })
        except Exception:
            pass
            
        result_data = {
            "username": user_info.get("username", username),
            "full_name": user_info.get("full_name", ""),
            "profile_pic": user_info.get("profile_pic_url_hd", user_info.get("profile_pic_url", "")),
            "biography": user_info.get("biography", ""),
            "follower_count": user_info.get("edge_followed_by", {}).get("count", 0),
            "following_count": user_info.get("edge_follow", {}).get("count", 0),
            "is_verified": user_info.get("is_verified", False),
            "username_buscado": username
        }
        
        profiles_cache_collection.insert_one(result_data.copy())
        del result_data["username_buscado"]
        MEMORY_CACHE[username] = result_data.copy()
        
        final_data = _apply_custom_stats(result_data.copy(), session_id)
        return jsonify(final_data)
        
    except Exception as e:
        error_msg = str(e)
        print(f"[SCRAPER ERRO DE BUSCA] {error_msg}")
        
        if "TooManyRequests" in error_msg or "CIRCUIT_BREAKER_ACTIVE" in error_msg:
             return jsonify({"error": "Muitas requisições simultâneas. O servidor proxy está em cooldown. Tente novamente em alguns instantes."}), 429
             
        if "UserNotFound" in error_msg:
             return jsonify({"error": "Usuário não encontrado. Verifique se o nome está correto."}), 404

        try:
            activities_collection.insert_one({
                "session_id": session_id,
                "input_original": target_input,
                "username_buscado": username,
                "status": "erro_na_busca",
                "data_hora": datetime.now(timezone.utc)
            })
        except:
            pass
        return jsonify({"error": "Não foi possível resgatar os dados deste usuário no momento. Tente novamente mais tarde."}), 404

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
    data = request.json or {}
    plan_requested = data.get('plan')
    user_cpf = data.get('cpf')
    
    if not user_cpf:
        return jsonify({"error": "O CPF é obrigatório para gerar o pagamento."}), 400
        
    user_cpf = re.sub(r'[^0-9]', '', user_cpf)
    if len(user_cpf) != 11 and len(user_cpf) != 14:
         return jsonify({"error": "CPF/CNPJ inválido."}), 400
    
    if plan_requested == 'pro':
        valor_centavos = 5000
        desc = "Plano Pro - 1 Mes"
    elif plan_requested == 'premium':
        valor_centavos = 7900
        desc = "Plano Premium - 1 Mes"
    else:
        return jsonify({"error": "Plano invalido."}), 400
        
    gateway = get_active_gateway()
    payer_name = f"Cliente {user_cpf[:3]}" 
    payer_cpf = user_cpf
    
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip()
    user_agent = request.headers.get('User-Agent', '')
    fbp = request.cookies.get('_fbp')
    fbc = request.cookies.get('_fbc')

    host_url = request.host_url.rstrip('/')
    webhook_url = f"{host_url}/api/webhook/pix"
    
    tracking_payload = {
        "client_ip": client_ip,
        "client_user_agent": user_agent,
        "src": "checkout-direct"
    }
    
    if gateway == "paradise":
        pix_data = criar_cobranca_pix_paradise(
            valor_centavos, desc, payer_name, payer_cpf,
            webhook_url=webhook_url,
            tracking=tracking_payload
        )
        if not pix_data:
            return jsonify({"error": "Falha na comunicação com provedor Paradise."}), 500
    else:
        pix_data = criar_cobranca_pix_ggpix(
            valor_centavos, 
            desc, 
            payer_name, 
            payer_cpf, 
            webhook_url=webhook_url, 
            tracking=tracking_payload
        )
        if not pix_data:
            return jsonify({"error": "Falha na comunicação com provedor GGPIX."}), 500
        
    transaction_id = pix_data['id']
    copia_e_cola = pix_data['pixCopyPaste']
    
    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(copia_e_cola)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    qr_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
    
    payments_collection.insert_one({
        "transaction_id": transaction_id,
        "externalId": pix_data.get('externalId'),
        "session_id": session_id,
        "plan_requested": plan_requested,
        "gateway": gateway,
        "status": "PENDING",
        "pixel_fired": False, 
        "client_ip": client_ip,
        "user_agent": user_agent,
        "fbp_cookie": fbp,
        "fbc_cookie": fbc,
        "created_at": datetime.now(timezone.utc)
    })
    
    return jsonify({
        "transaction_id": transaction_id,
        "pix_copy_paste": copia_e_cola,
        "qr_base64": qr_b64
    })

# ==========================================
# ENDPOINT DE WEBHOOK EM TEMPO REAL (GGPIX & PARADISE)
# ==========================================
@app.route('/api/webhook/pix', methods=['POST'])
@app.route('/api/webhook/ggpix', methods=['POST'])
def webhook_pix_handler():
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"status": "ignored", "error": "No JSON payload"}), 400

        print(f"[WEBHOOK RECEBIDO] Dados: {data}")
        
        transaction_id = str(data.get("transactionId") or data.get("transaction_id") or data.get("id") or "")
        external_id = str(data.get("externalId") or data.get("external_id") or "")
        status = data.get("status")

        if not transaction_id and not external_id:
            return jsonify({"status": "ignored", "error": "ID ausente no Payload"}), 400

        payment_record = None
        if transaction_id:
            payment_record = payments_collection.find_one({"transaction_id": transaction_id})
        
        if not payment_record and transaction_id and transaction_id.isdigit():
            payment_record = payments_collection.find_one({"transaction_id": int(transaction_id)})
            
        if not payment_record and external_id:
            payment_record = payments_collection.find_one({"externalId": external_id})

        if payment_record:
            if status in ["COMPLETE", "approved", "PAID", "COMPLETED"]:
                internal_status = "COMPLETE"
            elif status in ["failed", "refunded", "chargeback", "CANCELED"]:
                internal_status = "FAILED"
            else:
                internal_status = status

            payments_collection.update_one(
                {"_id": payment_record["_id"]},
                {"$set": {"status": internal_status, "updated_at": datetime.now(timezone.utc)}}
            )

            if internal_status == "COMPLETE":
                users_collection.update_one(
                    {"session_id": payment_record['session_id']},
                    {"$set": {"plan": payment_record['plan_requested']}}
                )

                if not payment_record.get('pixel_fired'):
                    client_ip = payment_record.get('client_ip', '0.0.0.0')
                    user_agent = payment_record.get('user_agent', 'Webhook/Adquirente')
                    plan_requested = payment_record.get('plan_requested', '')
                    fbp = payment_record.get('fbp_cookie')
                    fbc = payment_record.get('fbc_cookie')

                    send_meta_purchase_event(plan_requested, client_ip, user_agent, payment_record.get('transaction_id'), fbp, fbc)

                    payments_collection.update_one(
                        {"_id": payment_record["_id"]},
                        {"$set": {"pixel_fired": True}}
                    )

        return jsonify({"status": "success", "processed": True}), 200

    except Exception as e:
        print(f"[WEBHOOK ERRO CRÍTICO] {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/check_payment/<transaction_id>', methods=['GET'])
def check_payment(transaction_id):
    payment_record = payments_collection.find_one({"transaction_id": transaction_id})
    
    if payment_record and payment_record.get("status") in ["COMPLETE", "approved", "PAID"]:
        return jsonify({"status": "COMPLETE"})
        
    gateway_used = payment_record.get('gateway', 'ggpix') if payment_record else 'ggpix'
    
    if gateway_used == 'paradise':
        tx_info = checar_status_transacao_paradise(transaction_id)
    else:
        tx_info = checar_status_transacao_ggpix(transaction_id)
        
    if not tx_info:
        if payment_record:
            return jsonify({"status": payment_record.get('status', 'PENDING')})
        return jsonify({"status": "ERROR"}), 500
        
    status = tx_info.get('status', 'PENDING')
    
    payments_collection.update_one(
        {"transaction_id": transaction_id},
        {"$set": {"status": status, "updated_at": datetime.now(timezone.utc)}}
    )
    
    if status == 'COMPLETE':
        if payment_record:
            users_collection.update_one(
                {"session_id": payment_record['session_id']},
                {"$set": {"plan": payment_record['plan_requested']}}
            )
            
            if not payment_record.get('pixel_fired'):
                client_ip = request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip()
                user_agent = request.headers.get('User-Agent', '')
                plan_requested = payment_record.get('plan_requested', '')
                fbp = payment_record.get('fbp_cookie')
                fbc = payment_record.get('fbc_cookie')

                send_meta_purchase_event(plan_requested, client_ip, user_agent, transaction_id, fbp, fbc)
                
                payments_collection.update_one(
                    {"transaction_id": transaction_id},
                    {"$set": {"pixel_fired": True}}
                )
            
    return jsonify({"status": status})

@app.route('/api/generate_token', methods=['POST'])
def api_generate_token():
    session_id = request.cookies.get('user_session_id', 'sessao_nao_identificada')
    
    user = get_or_create_user(session_id)
    today = get_today_str()
    tokens_used_dict = user.get('tokens_used_today', {})
    current_used = tokens_used_dict.get(today, 0)
    
    tokens_used_dict[today] = current_used + 1
    users_collection.update_one(
        {"session_id": session_id},
        {"$set": {"tokens_used_today": tokens_used_dict}}
    )
    
    config = config_collection.find_one({"session_id": session_id})
    if config and config.get("custom_token"):
        token = config.get("custom_token")
    else:
        tamanho = random.randint(27, 30)
        caracteres = string.ascii_letters + string.digits
        token = ''.join(random.choice(caracteres) for _ in range(tamanho))

    return jsonify({"token": token})

# ==========================================
# NOVO ENDPOINT: PROXY DE IMAGEM PARA BYPASS DE CORS/ORIGEM
# ==========================================
@app.route('/api/proxy_image', methods=['GET'])
def proxy_image():
    image_url = request.args.get('url')
    if not image_url:
        return "URL missing", 400
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.instagram.com/"
        }
        
        response = requests.get(image_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            content_type = response.headers.get('Content-Type', 'image/jpeg')
            return make_response(response.content, 200, {'Content-Type': content_type})
        else:
            return "Error fetching image", response.status_code
    except Exception as e:
        print(f"[PROXY IMAGE ERRO] Falha ao baixar imagem: {e}")
        return "Internal Error", 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
