"""
ZIA PLATFORM — Multi-Client WhatsApp Webhook
"""

import os
import sys
import threading
import base64

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

            if mensajes_extra:
                t = threading.Thread(
                    target=send_extra_messages,
                    args=(sender, mensajes_extra),
                    daemon=True
                )
                t.start()

            resp = MessagingResponse()
            resp.message(primer_mensaje)
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


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
