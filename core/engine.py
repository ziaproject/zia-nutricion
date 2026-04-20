import json
import os
import re
from openai import OpenAI

def load_client_config(client_id):
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'clients', client_id, 'config.json')
    with open(p, 'r', encoding='utf-8') as f:
        return json.load(f)

_cache = {}

def get_engine(client_id=None):
    if client_id is None:
        client_id = os.environ.get('CLIENT_ID', 'zia-nutricion')
    if client_id not in _cache:
        _cache[client_id] = ZiaEngine(client_id)
    return _cache[client_id]

class ZiaEngine:
    def __init__(self, client_id):
        self.client_id = client_id
        self.config = load_client_config(client_id)
        self.openai = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
        self._users = {}
    def _get_user(self, uid):
        if uid not in self._users:
            self._users[uid] = {'state': 'welcome', 'data': {}, 'plan': None}
        return self._users[uid]
    def reset_user(self, uid):
        self._users[uid] = {'state': 'welcome', 'data': {}, 'plan': None}
    def get_welcome_message(self):
        return self.config['bot']['welcome_message']
    def process_message(self, user_id, message, plan_type='pro'):
        u = self._get_user(user_id)
        m = message.strip()
        company = self.config['branding']['company_name']
        if m.lower() in ['hola','inicio','reset','empezar','start']:
            self.reset_user(user_id)
            u = self._get_user(user_id)
        s = u['state']
        if s == 'welcome':
            u['state'] = 'datos'
            return 'Hola! Soy ZIA de ' + company + '. Para crear tu plan perfecto necesito conocerte.\n\nEscribeme: Nombre, genero, edad, peso (kg) y altura (cm)\n\nEjemplo: Maria, mujer, 34, 65kg, 165cm'
        elif s == 'datos':
            nums = [int(x) for x in re.findall(r'\d+', m)]
            u['data']['raw'] = m
            u['data']['nums'] = nums
            u['state'] = 'objetivo'
            return 'Perfecto! Cual es tu objetivo?\n\n  1 Perder grasa\n  2 Ganar musculo\n  3 Mas energia\n  4 Comer mas sano'
        elif s == 'objetivo':
            u['data']['objetivo'] = m
            u['state'] = 'restricciones'
            return 'Tienes alguna restriccion alimentaria?\n\n  Ninguna\n  Vegano/Vegetariano\n  Sin gluten\n  Sin lactosa\n  Otra (escribela)'
        elif s == 'restricciones':
            u['data']['restricciones'] = m
            u['state'] = 'presupuesto'
            return 'Cuanto quieres gastar esta semana?\n\n  30-50 euros\n  50-80 euros\n  80-120 euros\n  Mas de 120 euros'
        elif s == 'presupuesto':
            u['data']['presupuesto'] = m
            u['state'] = 'generando'
            plan = self._generar_plan(u['data'])
            u['plan'] = plan
            u['state'] = 'plan_listo'
            return 'Analizando tu perfil...\n\n' + plan + '\n\n---\nQue quieres hacer?\n\n  Anadir al carrito\n  Cambiar algo\n  Guardar lista'
        elif s == 'plan_listo':
            return self._gpt_libre(m, u)
        else:
            u['state'] = 'welcome'
            return 'Hola! Escribe tu nombre para empezar.'
    def _generar_plan(self, data):
        company = self.config['branding']['company_name']
        checkout = self.config.get('integrations', {}).get('cart', {}).get('checkout_url', self.config['branding']['website'])
        prompt = 'Eres ZIA nutricionista de ' + company + '. Genera un menu semanal Lunes-Domingo con Desayuno Comida Merienda Cena con cantidades en gramos. Luego lista de compra por categorias con precios. Objetivo: ' + data.get('objetivo','') + '. Restricciones: ' + data.get('restricciones','Ninguna') + '. Presupuesto: ' + data.get('presupuesto','') + '. Al final pon el link: ' + checkout + '. Maximo 500 palabras. Tono motivador.'
        try:
            r = self.openai.chat.completions.create(model=self.config.get('ai',{}).get('model','gpt-4o-mini'), messages=[{'role':'system','content':'Eres ZIA nutricionista. Responde en espanol.'},{'role':'user','content':prompt}], max_tokens=1800, temperature=0.7)
            return r.choices[0].message.content
        except Exception as e:
            return 'Error generando plan: ' + str(e)[:50]
    def _gpt_libre(self, message, u):
        company = self.config['branding']['company_name']
        system = 'Eres ZIA asesora de ' + company + '. Ayuda con modificaciones al plan, preguntas de nutricion y productos. Responde en espanol. Maximo 300 palabras.'
        history = u.get('history', [])
        history.append({'role': 'user', 'content': message})
        if len(history) > 10: history = history[-10:]
        try:
            r = self.openai.chat.completions.create(model=self.config.get('ai',{}).get('model','gpt-4o-mini'), messages=[{'role':'system','content':system}]+history, max_tokens=800, temperature=0.7)
            reply = r.choices[0].message.content
            history.append({'role': 'assistant', 'content': reply})
            u['history'] = history
            return reply
        except Exception as e:
            return 'Error: ' + str(e)[:50]