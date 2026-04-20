"""
ZIA PLATFORM — Multi-Client WhatsApp Webhook
=============================================
Un solo servidor. Múltiples clientes.
El cliente activo se determina por CLIENT_ID en .env

Para cambiar de cliente:
  1. Cambia CLIENT_ID en .env
  2. Redeploy en Railway (30 segundos)

Archivo: platform/app.py
"""

import os
import sys
from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator
import logging

# Añadir el directorio raíz al path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.engine import get_engine

# ══════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Cliente activo — se lee del .env
CLIENT_ID = os.environ.get('CLIENT_ID', 'zia-nutricion')
logger.info(f"🚀 ZIA Platform iniciando para cliente: {CLIENT_ID}")

# Inicializar engine del cliente
engine = get_engine(CLIENT_ID)
logger.info(f"✅ Engine cargado: {engine.config['branding']['company_name']}")


# ══════════════════════════════════════════════════════════
# WEBHOOK PRINCIPAL
# ══════════════════════════════════════════════════════════

@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Endpoint principal que recibe mensajes de Twilio WhatsApp.
    Funciona igual para todos los clientes.
    """
    try:
        # Datos del mensaje entrante
        incoming_msg = request.values.get('Body', '').strip()
        sender = request.values.get('From', '')
        
        logger.info(f"📨 Mensaje de {sender}: {incoming_msg[:50]}...")
        
        if not incoming_msg or not sender:
            logger.warning("Mensaje vacío o sin remitente")
            return Response('', status=200)
        
        # Determinar el plan del usuario (en producción viene de Supabase)
        plan_type = get_user_plan(sender)
        
        # Procesar el mensaje con el engine del cliente activo
        reply = engine.process_message(
            user_id=sender,
            message=incoming_msg,
            plan_type=plan_type
        )
        
        logger.info(f"✅ Respuesta generada ({len(reply)} chars)")
        
        # Enviar respuesta por Twilio
        resp = MessagingResponse()
        resp.message(reply)
        return str(resp)
        
    except Exception as e:
        logger.error(f"❌ Error en webhook: {str(e)}", exc_info=True)
        resp = MessagingResponse()
        resp.message("Lo siento, ha ocurrido un error. Por favor inténtalo de nuevo.")
        return str(resp)


# ══════════════════════════════════════════════════════════
# HEALTH CHECK
# ══════════════════════════════════════════════════════════

@app.route('/health', methods=['GET'])
def health():
    """Health check para Railway"""
    return {
        "status": "ok",
        "client": CLIENT_ID,
        "company": engine.config['branding']['company_name'],
        "version": engine.config['_meta']['version']
    }


@app.route('/', methods=['GET'])
def index():
    return f"ZIA Platform · {engine.config['branding']['company_name']} · Running ✅"


# ══════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════

def get_user_plan(user_id: str) -> str:
    """
    Obtiene el plan del usuario.
    En producción: consulta Supabase.
    En dev: devuelve 'free' por defecto.
    
    Para clientes B2B: siempre devuelve 'pro' (acceso completo).
    """
    config = engine.config
    
    # Clientes B2B tienen acceso completo para todos sus usuarios
    if config['_meta']['type'] == 'B2B':
        return 'pro'
    
    # B2C: en producción consulta Supabase
    # TODO: implementar con Supabase
    # user = supabase.table('users').select('plan').eq('whatsapp', user_id).single()
    # return user['plan'] if user else 'free'
    
    return 'free'  # default para desarrollo


# ══════════════════════════════════════════════════════════
# COMANDOS ESPECIALES
# ══════════════════════════════════════════════════════════

RESET_COMMANDS = ['reset', 'reiniciar', 'empezar', 'start', '/reset']
HELP_COMMANDS = ['ayuda', 'help', '/help', '?']

def handle_special_commands(user_id: str, message: str) -> str | None:
    """
    Gestiona comandos especiales del sistema.
    Devuelve None si no es un comando especial.
    """
    msg_lower = message.lower().strip()
    
    if msg_lower in RESET_COMMANDS:
        engine.reset_user(user_id)
        return engine.get_welcome_message()
    
    if msg_lower in HELP_COMMANDS:
        return (
            "📋 *Comandos disponibles:*\n\n"
            "• Escribe cualquier cosa para empezar\n"
            "• *reset* — reiniciar la conversación\n"
            "• *plan* — ver tu plan actual\n"
            "• *ayuda* — ver este mensaje\n\n"
            f"💬 Powered by ZIA Platform"
        )
    
    return None


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
