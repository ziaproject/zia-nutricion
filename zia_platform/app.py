"""
ZIA PLATFORM — Multi-Client WhatsApp Webhook
"""

import os
import sys
import threading
from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.engine import get_engine

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

CLIENT_ID = os.environ.get('CLIENT_ID', 'zia-nutricion')
logger.info(f"🚀 ZIA Platform iniciando para cliente: {CLIENT_ID}")

engine = get_engine(CLIENT_ID)
logger.info(f"✅ Engine cargado: {engine.config['branding']['company_name']}")

# Cliente Twilio para enviar mensajes adicionales
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_WHATSAPP_FROM = os.environ.get('TWILIO_WHATSAPP_FROM', 'whatsapp:+14155238886')


def send_extra_messages(to: str, messages: list):
    """Envía mensajes adicionales via Twilio API en un hilo separado."""
    try:
        twilio = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        for msg in messages:
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

        logger.info(f"📨 Mensaje de {sender}: {incoming_msg[:50]}...")

        if not incoming_msg or not sender:
            logger.warning("Mensaje vacío o sin remitente")
            return Response('', status=200)

        plan_type = get_user_plan(sender)

        reply = engine.process_message(
            user_id=sender,
            message=incoming_msg,
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
    if config['_meta']['type'] == 'B2B':
        return 'pro'
    return 'free'


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
