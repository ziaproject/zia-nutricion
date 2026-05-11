"""
ZIA PLATFORM — Multi-Client WhatsApp Webhook
"""

import os
import re
import sys
import threading
import base64
import time
from typing import Optional

import requests
from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

try:
    from flask_cors import CORS

    CORS(
        app,
        resources={
            re.compile(r"^/web/.*$"): {
                "origins": "*",
                "allow_headers": [
                    "Content-Type",
                    "Authorization",
                    "X-Requested-With",
                    "Accept",
                ],
                "methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
                "max_age": 86400,
            }
        },
    )
except ImportError:
    logger.warning("flask-cors no instalado; el front en otro dominio puede fallar por CORS")

CLIENT_ID = os.environ.get('CLIENT_ID', 'zia-nutricion')
logger.info(f"🚀 ZIA Platform iniciando para cliente: {CLIENT_ID}")

if CLIENT_ID == 'naturvitia':
    from core.engine_naturvitia import get_naturvitia_engine as get_engine
else:
    from core.engine import get_engine

engine = get_engine() if CLIENT_ID == 'naturvitia' else get_engine(CLIENT_ID)
logger.info(f"✅ Engine cargado: {engine.config['branding']['company_name']}")

# Cliente Twilio para enviar mensajes adicionales
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_WHATSAPP_FROM = os.environ.get('TWILIO_WHATSAPP_FROM', 'whatsapp:+14155238886')


def send_extra_messages(to: str, messages: list):
    """Envía mensajes adicionales via Twilio API en un hilo separado."""
    try:
        pause_fn = None
        if CLIENT_ID == 'naturvitia':
            from core.engine_naturvitia import pause_between_plan_whatsapp_parts as pause_fn

        twilio = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        for msg in messages:
            time.sleep(2)
            if pause_fn:
                pause_fn()
            twilio.messages.create(
                body=msg,
                from_=TWILIO_WHATSAPP_FROM,
                to=to
            )
            logger.info(f"📤 Mensaje extra enviado a {to} ({len(msg)} chars)")
    except Exception as e:
        logger.error(f"❌ Error enviando mensaje extra: {str(e)}")


def process_photo_in_background(to: str, message_arg: dict, plan_type: str):
    """Procesa fotos lentas fuera del webhook y envía la respuesta por Twilio."""
    try:
        reply = engine.process_message(
            user_id=to,
            message=message_arg,
            plan_type=plan_type
        )
        messages = reply if isinstance(reply, list) else [reply]
        send_extra_messages(to, messages)
    except Exception as e:
        logger.error(f"❌ Error procesando foto en background: {str(e)}", exc_info=True)


def process_reply_skip_first_in_background(to: str, message_arg: dict, plan_type: str):
    """Genera una respuesta pesada y envía todo salvo el primer mensaje."""
    try:
        reply = engine.process_message(
            user_id=to,
            message=message_arg,
            plan_type=plan_type
        )
        messages = reply if isinstance(reply, list) else [reply]
        if len(messages) > 1:
            send_extra_messages(to, messages[1:])
    except Exception as e:
        logger.error(f"❌ Error procesando respuesta en background: {str(e)}", exc_info=True)
        send_extra_messages(
            to,
            ["No pude generar la respuesta por un error o timeout. Intentalo de nuevo en unos minutos."]
        )


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        incoming_msg = request.values.get('Body', '').strip()
        sender = request.values.get('From', '')
        media_url = (request.values.get('MediaUrl0') or '').strip()

        logger.info(f"📨 Mensaje de {sender}: {incoming_msg[:50]}...")

        if not sender:
            logger.warning("Webhook sin remitente")
            return Response('', status=200)
        if not incoming_msg and not media_url:
            logger.warning("Mensaje vacío sin texto ni MediaUrl0")
            return Response('', status=200)

        plan_type = get_user_plan(sender)

        message_arg = {
            'text': request.form.get('Body', ''),
            'MediaUrl0': request.form.get('MediaUrl0', ''),
            'MediaContentType0': request.form.get('MediaContentType0', ''),
        }
        meta = engine.config.get('_meta') or {}
        if media_url and (
            (isinstance(meta, dict) and meta.get('type') == 'retail-asesor')
            or CLIENT_ID == 'naturvitia'
        ):
            try:
                r = requests.get(
                    media_url,
                    auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
                    timeout=15,
                )
                if r.status_code != 200:
                    logger.warning("No se pudo descargar MediaUrl0 (status %s)", r.status_code)
                    return Response('', status=200)
                img_b64 = base64.b64encode(r.content).decode("utf-8")
                media_type = request.values.get('MediaContentType0', 'image/jpeg')
                detected_type = (
                    media_type if media_type.startswith("image/") else "image/jpeg"
                )
                message_arg = {
                    "text": incoming_msg,
                    "image_url": f"data:{detected_type};base64,{img_b64}",
                }
            except Exception as e:
                logger.error("Error descargando imagen para retail-asesor: %s", e, exc_info=True)
                return Response('', status=200)

        user = engine._get_user(sender)
        user_state = user.get('state')
        if media_url and user_state == 'esperando_foto_nevera':
            t = threading.Thread(
                target=process_photo_in_background,
                args=(sender, message_arg, plan_type),
                daemon=True
            )
            t.start()

            resp = MessagingResponse()
            resp.message("Dame un momento, estoy analizando la foto 📸")
            return str(resp)

        if user_state == 'supermercado':
            super_map = {
                '1': 'Mercadona', '2': 'Lidl', '3': 'Aldi', '4': 'Carrefour',
                '5': 'Dia', '6': 'Consum', '7': 'Supercor', '8': 'El Corte Ingles'
            }
            super_nombre = super_map.get(incoming_msg.strip(), incoming_msg.strip()) if incoming_msg.strip() else 'Mercadona'
            mensaje_espera = 'Perfecto! 🌿 Estoy preparando tu plan semanal personalizado y tu lista de la compra para ' + super_nombre + '. Dame un momento... ⏳'
            t = threading.Thread(
                target=process_reply_skip_first_in_background,
                args=(sender, message_arg, plan_type),
                daemon=True
            )
            t.start()

            resp = MessagingResponse()
            resp.message(mensaje_espera)
            return str(resp)

        reply = engine.process_message(
            user_id=sender,
            message=message_arg,
            plan_type=plan_type
        )

        # Si el engine devuelve una lista, el primer mensaje va por TwiML
        # y el resto se envían en background via API de Twilio
        if isinstance(reply, list):
            primer_mensaje = reply[0]
            mensajes_extra = reply[1:]
            resp = MessagingResponse()
            resp.message(primer_mensaje)

            if mensajes_extra:
                t = threading.Thread(
                    target=send_extra_messages,
                    args=(sender, mensajes_extra),
                    daemon=True
                )
                t.start()

            return str(resp)
        else:
            logger.info(f"✅ Respuesta generada ({len(reply)} chars)")
            resp = MessagingResponse()
            resp.message(reply)
            return str(resp)

    except Exception as e:
        logger.error(f"❌ Error en webhook: {str(e)}", exc_info=True)
        resp = MessagingResponse()
        resp.message("Lo siento, ha ocurrido un error. Por favor intentalo de nuevo.")
        return str(resp)


@app.route('/health', methods=['GET'])
def health():
    return {
        "status": "ok",
        "client": CLIENT_ID,
        "company": engine.config['branding']['company_name'],
        "version": engine.config['_meta']['version']
    }


@app.route('/', methods=['GET'])
def index():
    return f"ZIA Platform · {engine.config['branding']['company_name']} · Running ✅"


def get_user_plan(user_id: str) -> str:
    config = engine.config
    t = config.get('_meta', {}).get('type', '')
    if t == 'B2B' or t == 'B2B_nutricionista':
        return 'pro'
    return 'free'


# --- Supabase via HTTP (evita supabase-py / gotrue y facilita depurar DNS/URL) ---

def _supabase_base_url() -> str:
    u = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
    if not u:
        raise ValueError("SUPABASE_URL no está definida")
    if not u.startswith("http://") and not u.startswith("https://"):
        u = "https://" + u
    return u


def _supabase_anon_key() -> str:
    k = (os.getenv("SUPABASE_KEY") or "").strip()
    if not k:
        raise ValueError("SUPABASE_KEY no está definida")
    return k


def _sb_hdr_json(bearer: str) -> dict:
    """bearer = JWT de usuario o clave anónima/servicio (según config)."""
    key = _supabase_anon_key()
    return {
        "apikey": key,
        "Authorization": "Bearer " + bearer,
        "Content-Type": "application/json",
    }


def _sb_auth_signup(email: str, password: str) -> dict:
    r = requests.post(
        _supabase_base_url() + "/auth/v1/signup",
        headers=_sb_hdr_json(_supabase_anon_key()),
        json={"email": email, "password": password},
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(r.text or r.reason or str(r.status_code))
    return r.json()


def _sb_auth_sign_in(email: str, password: str) -> dict:
    r = requests.post(
        _supabase_base_url() + "/auth/v1/token?grant_type=password",
        headers=_sb_hdr_json(_supabase_anon_key()),
        json={"email": email, "password": password},
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(r.text or r.reason or str(r.status_code))
    return r.json()


def _sb_auth_get_user(jwt: str) -> dict:
    r = requests.get(
        _supabase_base_url() + "/auth/v1/user",
        headers=_sb_hdr_json(jwt),
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(r.text or r.reason or str(r.status_code))
    return r.json()


def _sb_rest_insert(table: str, row: dict, jwt: Optional[str]) -> None:
    bearer = jwt if jwt else _supabase_anon_key()
    r = requests.post(
        _supabase_base_url() + "/rest/v1/" + table,
        headers={**_sb_hdr_json(bearer), "Prefer": "return=minimal"},
        json=[row],
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(r.text or r.reason or str(r.status_code))


def _sb_rest_upsert(table: str, row: dict, jwt: str) -> None:
    r = requests.post(
        _supabase_base_url() + "/rest/v1/" + table,
        headers={
            **_sb_hdr_json(jwt),
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        json=[row],
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(r.text or r.reason or str(r.status_code))


def _sb_rest_select_one(table: str, jwt: str, **eq_filters: str) -> Optional[dict]:
    from urllib.parse import quote
    parts = []
    for col, val in eq_filters.items():
        parts.append(f"{col}=eq.{quote(str(val), safe='')}")
    qs = "&".join(parts) + "&select=*"
    r = requests.get(
        _supabase_base_url() + "/rest/v1/" + table + "?" + qs,
        headers=_sb_hdr_json(jwt),
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(r.text or r.reason or str(r.status_code))
    rows = r.json()
    return rows[0] if isinstance(rows, list) and rows else None


def _token_es_anonimo(token: str) -> bool:
    t = (token or "").strip().lower()
    return t in ("anonimo", "anonymous", "anónimo")


def _perfil_desde_json_body(body: dict) -> dict:
    if not isinstance(body, dict):
        body = {}
    intol = body.get("intolerancias")
    if isinstance(intol, list):
        intol = ", ".join(str(x) for x in intol)
    return {
        "objetivo": body.get("objetivo") or "",
        "peso": body.get("peso") or "",
        "altura": body.get("altura") or "",
        "intolerancias": intol or "ninguna",
        "supermercado": body.get("supermercado") or "",
        "presupuesto": body.get("presupuesto") or "",
        "nombre": body.get("nombre") or "",
        "edad": body.get("edad") or "",
        "sexo": body.get("sexo") or "",
        "ejercicio": body.get("ejercicio") or "",
        "cocina": body.get("cocina") or "",
        "comidas_dia": body.get("comidas_dia") or "",
    }


def _desanidar_plan_bruto(raw: dict) -> dict:
    if not isinstance(raw, dict):
        return {}
    inner = raw.get("plan")
    if isinstance(inner, dict) and isinstance(inner.get("dias"), list):
        return inner
    inner2 = raw.get("plan_data")
    if isinstance(inner2, dict) and isinstance(inner2.get("dias"), list):
        return inner2
    return raw


def _normalizar_lista_compra(lc) -> dict:
    if not isinstance(lc, dict):
        lc = {}
    out = dict(lc)
    cats = out.get("categorias")
    out["categorias"] = cats if isinstance(cats, dict) else {}
    return out


@app.route('/web/health', methods=['GET'])
def web_health():
    from flask import jsonify
    return jsonify({"status": "ok", "service": "zia-nutricion-web"})


@app.route('/web/registro', methods=['POST'])
def web_registro():
    from flask import jsonify, request
    try:
        data = request.json or {}
        email = data.get("email")
        password = data.get("password")
        nombre = data.get("nombre") or "Usuario"
        body = _sb_auth_signup(email, password)
        sess = body.get("session") or {}
        user = body.get("user") or sess.get("user") or {}
        user_id = user.get("id")
        if not user_id:
            raise RuntimeError("Respuesta signup sin user.id: " + str(body)[:300])
        jwt = body.get("access_token") or sess.get("access_token")
        _sb_rest_insert(
            "usuarios",
            {"id": user_id, "email": email, "nombre": nombre, "plan": "free"},
            jwt,
        )
        return jsonify({"ok": True, "user_id": user_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@app.route('/web/login', methods=['POST'])
def web_login():
    from flask import jsonify, request
    try:
        data = request.json or {}
        body = _sb_auth_sign_in(data.get("email"), data.get("password"))
        sess = body.get("session") or {}
        user = body.get("user") or sess.get("user") or {}
        user_id = user.get("id")
        token = body.get("access_token") or sess.get("access_token")
        if not token or not user_id:
            raise RuntimeError(body.get("msg") or body.get("error_description") or str(body)[:400])
        return jsonify({"ok": True, "token": token, "user_id": user_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 401

@app.route('/web/perfil', methods=['POST'])
def web_perfil():
    from flask import jsonify, request
    try:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if _token_es_anonimo(token):
            return jsonify({"ok": True})
        me = _sb_auth_get_user(token)
        if isinstance(me.get("user"), dict):
            me = me["user"]
        user_id = me.get("id")
        data = request.json or {}
        _sb_rest_upsert(
            "perfiles",
            {
                "user_id": user_id,
                "objetivo": data.get("objetivo"),
                "peso": data.get("peso"),
                "altura": data.get("altura"),
                "intolerancias": data.get("intolerancias", "ninguna"),
                "supermercado": data.get("supermercado"),
                "presupuesto": data.get("presupuesto"),
            },
            token,
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/web/plan-simple", methods=["POST"])
def web_plan_simple():
    """Plan semanal vía JSON; sin Supabase (ideal para onboarding con Bearer anonimo)."""
    from flask import jsonify, request
    import json, openai

    try:
        body = request.json or {}
        p = _perfil_desde_json_body(body)
        try:
            dias = int(body.get("dias") or 7)
        except (TypeError, ValueError):
            dias = 7
        dias = max(1, min(dias, 7))
        nm = p.get("nombre") or ""
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        schema = (
            '{"dias":[{"dia":"Lunes","comidas":[{"tipo":"Desayuno","nombre":"","descripcion_breve":"",'
            '"ingredientes_texto":"","kcal":0,"proteinas_g":0,"carbos_g":0,"grasas_g":0}]}],'
            '"lista_compra":{"categorias":{"Fruta y Verdura":[{"producto":"","cantidad":"","peso_o_unidad":"",'
            '"precio_estimado_eur":0}]},"total_estimado_eur":0}}'
        )
        prompt = f"""Eres ZIA, nutricionista inteligente. Crea un plan de {dias} días para:
- Nombre: {nm}, edad {p.get('edad','')}, sexo {p.get('sexo','')}, {p.get('peso','')} kg, {p.get('altura','')} cm
- Objetivo: {p.get('objetivo','')}
- Ejercicio: {p.get('ejercicio','')}
- Cocina / tiempo: {p.get('cocina','')}
- Comidas al día (ritmo): {p.get('comidas_dia','')}
- Intolerancias: {p.get('intolerancias','')}
- Supermercado: {p.get('supermercado','')}
- Presupuesto semanal: {p.get('presupuesto','')}

En cada comida, "ingredientes_texto" debe listar cantidades en g, ml o ud (ej. Pechuga 150g, Huevos 2 ud).
Respeta el ritmo comidas_dia.

Devuelve SOLO un objeto JSON válido (sin markdown) con esta forma lógica:
{schema}
Genera exactamente {dias} elementos en "dias". lista_compra siempre con "categorias" (mapa) y "total_estimado_eur" número."""
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=4000,
        )
        raw = json.loads(response.choices[0].message.content or "{}")
        raw = _desanidar_plan_bruto(raw)
        dias_arr = raw.get("dias")
        if not isinstance(dias_arr, list):
            dias_arr = []
        lista_compra = _normalizar_lista_compra(raw.get("lista_compra"))
        limpio = {"dias": dias_arr, "lista_compra": lista_compra}
        return jsonify({"ok": True, **limpio})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/web/generar-plan', methods=['POST'])
def web_generar_plan():
    from flask import jsonify, request
    import json, openai
    try:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        body = request.json or {}
        anon = _token_es_anonimo(token)
        user_id = None
        plan_usuario = "free"
        if anon:
            dias = 7
            p = _perfil_desde_json_body(body)
        else:
            me = _sb_auth_get_user(token)
            if isinstance(me.get("user"), dict):
                me = me["user"]
            user_id = me.get("id")
            usuario = _sb_rest_select_one("usuarios", token, id=user_id)
            plan_usuario = (usuario or {}).get("plan", "free")
            dias = 7 if plan_usuario != "free" else 3
            perfil = _sb_rest_select_one("perfiles", token, user_id=user_id)
            p = perfil if perfil else _perfil_desde_json_body(body)
        nm = p.get("nombre") or ""
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        schema = (
            '{"dias":[{"dia":"Lunes","comidas":[{"tipo":"Desayuno","nombre":"","descripcion_breve":"",'
            '"ingredientes_texto":"","kcal":0,"proteinas_g":0,"carbos_g":0,"grasas_g":0}]}],'
            '"lista_compra":{"categorias":{"Fruta y Verdura":[{"producto":"","cantidad":"","peso_o_unidad":"",'
            '"precio_estimado_eur":0}]},"total_estimado_eur":0}}'
        )
        prompt = f"""Eres ZIA, nutricionista inteligente. Crea un plan de {dias} días para:
- Nombre: {nm}, edad {p.get('edad','')}, sexo {p.get('sexo','')}, {p.get('peso','')} kg, {p.get('altura','')} cm
- Objetivo: {p.get('objetivo','')}
- Ejercicio: {p.get('ejercicio','')}
- Cocina / tiempo: {p.get('cocina','')}
- Comidas al día (ritmo): {p.get('comidas_dia','')}
- Intolerancias: {p.get('intolerancias','')}
- Supermercado: {p.get('supermercado','')}
- Presupuesto semanal: {p.get('presupuesto','')}

En cada comida, "ingredientes_texto" debe listar cantidades en g, ml o ud (ej. Pechuga 150g, Huevos 2 ud).
Respeta el ritmo comidas_dia: si es 2 comidas con ayuno, solo 2 bloques tipo relevantes por día; si incluye snacks, usa tipo "Snack" o "Merienda".

Devuelve SOLO un objeto JSON válido (sin markdown) con exactamente esta forma lógica (mismas claves):
{schema}
Genera exactamente {dias} elementos en "dias". "lista_compra.categorias" debe agrupar productos del plan; si el plan es corto puedes usar categorías mínimas pero lista_compra siempre es objeto con "categorias" (mapa) y "total_estimado_eur" número."""
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=4000,
        )
        raw = json.loads(response.choices[0].message.content or "{}")
        raw = _desanidar_plan_bruto(raw)
        dias_arr = raw.get("dias")
        if not isinstance(dias_arr, list):
            dias_arr = []
        lista_compra = _normalizar_lista_compra(raw.get("lista_compra"))
        limpio = {"dias": dias_arr, "lista_compra": lista_compra}
        if not anon and user_id:
            plan_db = {**limpio, "plan_usuario": plan_usuario, "dias_generados": dias}
            _sb_rest_upsert("planes", {"user_id": user_id, "plan_data": plan_db}, token)
        return jsonify({"ok": True, **limpio})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/web/receta", methods=["POST"])
def web_receta():
    from flask import jsonify, request
    import openai

    try:
        body = request.json or {}
        nombre = (body.get("nombre") or "").strip()
        ingredientes = (
            body.get("ingredientes")
            or body.get("ingredientes_texto")
            or ""
        ).strip()
        intol = body.get("intolerancias")
        if isinstance(intol, list):
            intol = ", ".join(str(x) for x in intol)
        intol = (intol or "ninguna").strip()
        if not nombre and not ingredientes:
            return jsonify({"ok": False, "error": "Indica nombre del plato o ingredientes"}), 400
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        prompt = f"""Eres ZIA, nutricionista. Escribe la receta completa para el plato: {nombre or '(sin título)'}
Ingredientes de referencia del plan (respétalos; ajusta cantidades si hace falta): {ingredientes or '(no indicados)'}
Intolerancias o restricciones del usuario: {intol}

Formato de salida:
1) INGREDIENTES — lista con cantidades en g, ml o ud.
2) PASOS — numerados (1., 2., …), claros y en español.

Texto plano; puedes usar líneas en blanco entre secciones. Sin bloques de código markdown."""
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2500,
        )
        texto = (response.choices[0].message.content or "").strip()
        return jsonify({"ok": True, "receta": texto})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/web/lista-compra", methods=["POST"])
def web_lista_compra():
    from flask import jsonify, request
    import json, openai

    try:
        body = request.json or {}
        plan = body.get("plan") or body.get("plan_actual")
        if plan is None:
            return jsonify({"ok": False, "error": "falta el campo plan"}), 400
        if isinstance(plan, str):
            plan = json.loads(plan)
        supermercado = body.get("supermercado") or ""
        presupuesto = body.get("presupuesto") or ""
        intol = body.get("intolerancias")
        if isinstance(intol, list):
            intol = ", ".join(str(x) for x in intol)
        intol = intol or "ninguna"
        plan_str = json.dumps(plan, ensure_ascii=False)[:14000]
        schema = (
            '{"categorias":{"Nombre de categoría":[{"producto":"","cantidad":"","peso_o_unidad":"",'
            '"precio_estimado_eur":0}]},"total_estimado_eur":0}'
        )
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        prompt = f"""Con este plan semanal del usuario (JSON):
{plan_str}

Perfil: supermercado {supermercado}, presupuesto semanal {presupuesto} €, intolerancias: {intol}.

Genera la LISTA DE LA COMPRA agrupada por CATEGORÍAS con cantidades EXACTAS en g, ml o ud coherentes con el plan.
Precios orientativos realistas en España (2025–2026); si el supermercado no es Mercadona, ajusta a precios típicos de esa cadena.

Devuelve SOLO JSON válido con esta estructura:
{schema}"""
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=4000,
        )
        raw = json.loads(response.choices[0].message.content or "{}")
        lista = _normalizar_lista_compra(raw)
        return jsonify({"ok": True, **lista})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.after_request
def _cors_headers_web(resp):
    """Cabeceras CORS para /web/* (complementa flask-cors y cubre si no está instalado)."""
    if request.path.startswith("/web/"):
        resp.headers.setdefault("Access-Control-Allow-Origin", "*")
        resp.headers.setdefault(
            "Access-Control-Allow-Headers",
            "Content-Type, Authorization, X-Requested-With, Accept",
        )
        resp.headers.setdefault(
            "Access-Control-Allow-Methods",
            "GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS",
        )
        resp.headers.setdefault("Access-Control-Max-Age", "86400")
    return resp


@app.route("/web/", defaults={"subpath": ""}, methods=["OPTIONS"])
@app.route("/web/<path:subpath>", methods=["OPTIONS"])
def _web_options_preflight(subpath):
    """Respuesta vacía a preflight CORS para cualquier ruta bajo /web/."""
    return Response(status=204)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
