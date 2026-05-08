"""
ZIA PLATFORM — Multi-Client WhatsApp Webhook
"""

import os
import sys
import threading
import base64
import time

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


@app.route('/web/health', methods=['GET'])
def web_health():
    from flask import jsonify
    return jsonify({"status": "ok", "service": "zia-nutricion-web"})


@app.route('/web/registro', methods=['POST'])
def web_registro():
    from flask import jsonify, request
    from supabase import create_client
    import os
    try:
        supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
        data = request.json
        res = supabase.auth.sign_up({"email": data.get("email"), "password": data.get("password")})
        user_id = res.user.id
        supabase.table("usuarios").insert({"id": user_id, "email": data.get("email"), "nombre": data.get("nombre"), "plan": "free"}).execute()
        return jsonify({"ok": True, "user_id": user_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@app.route('/web/login', methods=['POST'])
def web_login():
    from flask import jsonify, request
    from supabase import create_client
    import os
    try:
        supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
        data = request.json
        res = supabase.auth.sign_in_with_password({"email": data.get("email"), "password": data.get("password")})
        return jsonify({"ok": True, "token": res.session.access_token, "user_id": res.user.id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 401

@app.route('/web/perfil', methods=['POST'])
def web_perfil():
    from flask import jsonify, request
    from supabase import create_client
    import os
    try:
        supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        user = supabase.auth.get_user(token)
        user_id = user.user.id
        data = request.json
        supabase.table("perfiles").upsert({"user_id": user_id, "objetivo": data.get("objetivo"), "peso": data.get("peso"), "altura": data.get("altura"), "intolerancias": data.get("intolerancias", "ninguna"), "supermercado": data.get("supermercado"), "presupuesto": data.get("presupuesto")}).execute()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@app.route('/web/generar-plan', methods=['POST'])
def web_generar_plan():
    from flask import jsonify, request
    from supabase import create_client
    import os, json, openai
    try:
        supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        user = supabase.auth.get_user(token)
        user_id = user.user.id
        usuario = supabase.table("usuarios").select("plan").eq("id", user_id).single().execute()
        plan_usuario = usuario.data.get("plan", "free")
        dias = 7 if plan_usuario != "free" else 3
        perfil = supabase.table("perfiles").select("*").eq("user_id", user_id).single().execute()
        p = perfil.data
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        prompt = f"""Eres ZIA, nutricionista inteligente. Crea un plan de {dias} días para:
- Objetivo: {p['objetivo']}
- Peso: {p['peso']}kg, Altura: {p['altura']}cm
- Intolerancias: {p['intolerancias']}
- Supermercado: {p['supermercado']}
- Presupuesto: {p['presupuesto']}
Devuelve SOLO JSON: {{"dias": [{{"dia": "Lunes", "desayuno": {{"nombre": "", "calorias": 0, "proteinas": 0, "carbos": 0, "grasas": 0}}, "comida": {{"nombre": "", "calorias": 0, "proteinas": 0, "carbos": 0, "grasas": 0}}, "cena": {{"nombre": "", "calorias": 0, "proteinas": 0, "carbos": 0, "grasas": 0}}}}], "lista_compra": null}}
lista_compra es null si son 3 dias. Si son 7 incluye lista agrupada por categorias."""
        response = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}], response_format={"type": "json_object"}, max_tokens=4000)
        plan_json = json.loads(response.choices[0].message.content)
        plan_json["plan_usuario"] = plan_usuario
        plan_json["dias_generados"] = dias
        supabase.table("planes").upsert({"user_id": user_id, "plan_data": plan_json}).execute()
        return jsonify({"ok": True, "plan": plan_json})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
