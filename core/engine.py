"""
ZIA PLATFORM — Core Conversation Engine
========================================
Motor central de ZIA. NUNCA se toca para cambiar de cliente.
Todo lo que varía entre clientes va en su config JSON.

Archivo: core/engine.py
"""

import json
import os
import re
from openai import OpenAI
from typing import Optional

# ══════════════════════════════════════════════════════════
# CLIENT LOADER — Carga el config del cliente activo
# ══════════════════════════════════════════════════════════

def load_client_config(client_id: str) -> dict:
    """
    Carga la configuración completa de un cliente.
    Para cambiar de cliente solo cambia CLIENT_ID en .env
    
    Estructura:
    clients/
      ├── herbolario-navarro/config.json
      ├── fitlife-gym/config.json
      ├── mercadona/config.json
      └── zia-nutricion/config.json  ← el B2C base
    """
    config_path = os.path.join(
        os.path.dirname(__file__), 
        '..', 'clients', client_id, 'config.json'
    )
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


# ══════════════════════════════════════════════════════════
# SYSTEM PROMPT BUILDER — Construye el prompt del cliente
# ══════════════════════════════════════════════════════════

def build_system_prompt(config: dict) -> str:
    """
    Genera el system prompt dinámicamente desde el config del cliente.
    Cada cliente tiene su tono, sus productos y su flujo.
    """
    c = config
    brand = c['branding']
    bot = c['bot']
    catalog = c.get('catalog', {})
    flow = c['flow']

    # Productos del catálogo (para que ZIA los conozca)
    products_text = ""
    if catalog.get('categories'):
        products_text = "\n\nCATÁLOGO DE PRODUCTOS DISPONIBLES:\n"
        for cat in catalog['categories']:
            products_text += f"\n{cat['name']}:\n"
            for p in cat.get('products', [])[:10]:  # max 10 por categoría
                products_text += f"  - {p['name']}: {p.get('price','')}"
                if p.get('unit'): products_text += f" ({p['unit']})"
                products_text += "\n"

    prompt = f"""Eres {bot['name']}, el asistente nutricional inteligente de {brand['company_name']}.

PERSONALIDAD Y TONO:
{bot['personality']}

TU MISIÓN:
{bot['mission']}

FLUJO DE CONVERSACIÓN:
{json.dumps(flow['steps'], ensure_ascii=False, indent=2)}

RESTRICCIONES IMPORTANTES:
- Solo recomienda productos del catálogo de {brand['company_name']}
- Siempre genera planes con cantidades exactas en gramos
- Incluye tiempo de preparación en cada receta
- La lista de la compra debe incluir precio estimado de cada producto
- Cuando generes la lista, formatea con separadores claros para que sea fácil de leer
- Responde SIEMPRE en español
- Mantén el tono: {bot['tone']}
- Nunca menciones competidores
- Si el usuario pregunta algo fuera de nutrición, redirige amablemente

MENSAJE DE BIENVENIDA:
{bot['welcome_message']}

LÍMITES DEL PLAN SEGÚN SUSCRIPCIÓN:
- FREE: {flow.get('limits', {}).get('free', '1 plan semanal, sin lista')}
- INDIVIDUAL: {flow.get('limits', {}).get('individual', 'Planes ilimitados, lista incluida')}
- PRO: {flow.get('limits', {}).get('pro', 'Todo ilimitado, multiusuario')}
{products_text}
"""
    return prompt


# ══════════════════════════════════════════════════════════
# CONVERSATION MANAGER — Gestiona el historial por usuario
# ══════════════════════════════════════════════════════════

class ConversationManager:
    """
    Gestiona el historial de conversación de cada usuario.
    En producción esto va a Supabase, en dev va a memoria.
    """
    
    def __init__(self, storage_backend='memory'):
        self.storage_backend = storage_backend
        self._memory = {}  # dev only
    
    def get_history(self, user_id: str, client_id: str) -> list:
        key = f"{client_id}:{user_id}"
        return self._memory.get(key, [])
    
    def add_message(self, user_id: str, client_id: str, role: str, content: str):
        key = f"{client_id}:{user_id}"
        if key not in self._memory:
            self._memory[key] = []
        self._memory[key].append({"role": role, "content": content})
        
        # Mantener solo los últimos 20 mensajes para no saturar tokens
        if len(self._memory[key]) > 20:
            self._memory[key] = self._memory[key][-20:]
    
    def clear_history(self, user_id: str, client_id: str):
        key = f"{client_id}:{user_id}"
        self._memory[key] = []
    
    def get_user_data(self, user_id: str, client_id: str) -> dict:
        """Extrae datos del usuario del historial (objetivo, alergias, etc.)"""
        history = self.get_history(user_id, client_id)
        # En producción esto vendría de Supabase
        return {}


# ══════════════════════════════════════════════════════════
# ZIA ENGINE — El motor principal
# ══════════════════════════════════════════════════════════

class ZiaEngine:
    """
    Motor principal de ZIA. Funciona igual para todos los clientes.
    Solo cambia el config que se le pasa al inicializar.
    """
    
    def __init__(self, client_id: str):
        self.client_id = client_id
        self.config = load_client_config(client_id)
        self.system_prompt = build_system_prompt(self.config)
        self.conversation = ConversationManager()
        self.openai = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
    
    def process_message(self, user_id: str, message: str, 
                       plan_type: str = 'free') -> str:
        """
        Procesa un mensaje del usuario y devuelve la respuesta de ZIA.
        
        Args:
            user_id: Número de WhatsApp u otro ID único del usuario
            message: Mensaje del usuario
            plan_type: 'free' | 'individual' | 'pro'
        
        Returns:
            Respuesta de ZIA como string
        """
        
        # Añadir contexto del plan al mensaje si es relevante
        enhanced_message = message
        if plan_type == 'free' and self._is_requesting_premium(message):
            return self._get_upgrade_message(plan_type)
        
        # Guardar mensaje del usuario
        self.conversation.add_message(user_id, self.client_id, 'user', enhanced_message)
        
        # Obtener historial
        history = self.conversation.get_history(user_id, self.client_id)
        
        # Llamar a OpenAI
        try:
            response = self.openai.chat.completions.create(
                model=self.config.get('ai', {}).get('model', 'gpt-4o-mini'),
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    *history
                ],
                max_tokens=self.config.get('ai', {}).get('max_tokens', 1500),
                temperature=self.config.get('ai', {}).get('temperature', 0.7)
            )
            
            reply = response.choices[0].message.content
            
            # Guardar respuesta
            self.conversation.add_message(user_id, self.client_id, 'assistant', reply)
            
            # Post-procesar (añadir links de carrito si aplica)
            reply = self._post_process(reply, user_id)
            
            return reply
            
        except Exception as e:
            return f"Lo siento, hay un problema técnico. Por favor inténtalo de nuevo. ({str(e)[:50]})"
    
    def _is_requesting_premium(self, message: str) -> bool:
        """Detecta si el usuario free está pidiendo features de pago"""
        premium_keywords = [
            'lista de la compra', 'lista compra', 'supermercado',
            'comparar precios', 'foto nevera', 'plan familiar',
            'otro plan', 'nueva semana'
        ]
        msg_lower = message.lower()
        return any(kw in msg_lower for kw in premium_keywords)
    
    def _get_upgrade_message(self, current_plan: str) -> str:
        """Mensaje de upgrade personalizado según el cliente"""
        brand = self.config['branding']['company_name']
        upgrade_url = self.config.get('payments', {}).get('upgrade_url', 'https://zianutricion.com/#precios')
        
        return (
            f"Esta función está disponible en el plan Individual 🌟\n\n"
            f"Por solo 4,99€/mes tienes acceso a listas de la compra ilimitadas, "
            f"comparativa de supermercados, análisis de nevera y mucho más.\n\n"
            f"👉 {upgrade_url}"
        )
    
    def _post_process(self, reply: str, user_id: str) -> str:
        """
        Post-procesa la respuesta:
        - Añade links de carrito si hay lista de productos
        - Formatea para WhatsApp
        - Añade CTA según el contexto
        """
        config = self.config
        
        # Si hay integración de carrito y la respuesta contiene una lista
        cart_config = config.get('integrations', {}).get('cart', {})
        if cart_config.get('enabled') and '€' in reply and ('lista' in reply.lower() or 'compra' in reply.lower()):
            cart_url = cart_config.get('base_url', '')
            if cart_url:
                reply += f"\n\n🛒 *Añadir al carrito de {config['branding']['company_name']}:*\n{cart_url}"
        
        return reply
    
    def get_welcome_message(self) -> str:
        """Devuelve el mensaje de bienvenida configurado para este cliente"""
        return self.config['bot']['welcome_message']
    
    def reset_user(self, user_id: str):
        """Resetea la conversación de un usuario"""
        self.conversation.clear_history(user_id, self.client_id)


# ══════════════════════════════════════════════════════════
# FACTORY — Crea instancias de ZIA por cliente
# ══════════════════════════════════════════════════════════

_engine_cache = {}

def get_engine(client_id: str = None) -> ZiaEngine:
    """
    Factory que devuelve el engine del cliente correcto.
    Usa caché para no recargar el config en cada mensaje.
    
    Si no se pasa client_id, usa el CLIENT_ID del .env
    """
    if client_id is None:
        client_id = os.environ.get('CLIENT_ID', 'zia-nutricion')
    
    if client_id not in _engine_cache:
        _engine_cache[client_id] = ZiaEngine(client_id)
    
    return _engine_cache[client_id]
