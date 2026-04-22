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

RESET_WORDS = ['hola','inicio','reset','empezar','reiniciar','start','menu','nuevo']

def is_reset(m):
    return m.strip().lower() in RESET_WORDS

def parse_datos(text):
    d = {}
    t = text.lower()
    if any(w in t for w in ['hombre','masculino','chico']): d['genero'] = 'Hombre'
    elif any(w in t for w in ['mujer','femenino','chica']): d['genero'] = 'Mujer'
    else: d['genero'] = 'No especificado'
    m = re.match(r'^([A-Za-zÀ-ÿ][a-zÀ-ÿ]+)', text)
    if m and m.group(1).lower() not in ['hombre','mujer','soy','tengo']:
        d['nombre'] = m.group(1).capitalize()
    else:
        d['nombre'] = ''
    nums = [int(n) for n in re.findall(r'\d+', text)]
    for n in nums:
        if 10 <= n <= 100 and 'edad' not in d: d['edad'] = str(n)
        elif 40 <= n <= 200 and 'peso' not in d and str(n) != d.get('edad',''): d['peso'] = str(n)
        elif 130 <= n <= 230 and 'altura' not in d: d['altura'] = str(n)
    return d

def faltan_datos(d):
    missing = []
    if 'edad' not in d: missing.append('edad')
    if 'peso' not in d: missing.append('peso en kg')
    if 'altura' not in d: missing.append('altura en cm')
    return missing

def calorias(d):
    try:
        w = float(d.get('peso', 70))
        h = float(d.get('altura', 170))
        a = float(d.get('edad', 30))
        if d.get('genero') == 'Hombre': bmr = 10*w + 6.25*h - 5*a + 5
        else: bmr = 10*w + 6.25*h - 5*a - 161
        return int(bmr * 1.55)
    except:
        return 2000

class ZiaEngine:
    def __init__(self, client_id):
        self.client_id = client_id
        self.config = load_client_config(client_id)
        self.openai = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
        self._users = {}

    def _get_user(self, uid):
        if uid not in self._users:
            self._users[uid] = {'state': 'welcome', 'data': {}, 'plan': None, 'history': [], 'plan_count': 0}
        return self._users[uid]

    def reset_user(self, uid):
        count = self._users.get(uid, {}).get('plan_count', 0)
        self._users[uid] = {'state': 'welcome', 'data': {}, 'plan': None, 'history': [], 'plan_count': count}

    def get_welcome_message(self):
        return self.config['bot']['welcome_message']

    def process_message(self, user_id, message, plan_type='pro'):
        u = self._get_user(user_id)
        m = message.strip()
        company = self.config['branding']['company_name']
        nombre = u['data'].get('nombre', '')
        nombre_str = ', ' + nombre if nombre else ''
        if is_reset(m):
            self.reset_user(user_id)
            u = self._get_user(user_id)
        s = u['state']
        if s == 'welcome':
            u['state'] = 'datos'
            return 'Hola! Soy ZIA, tu asesora nutricional de ' + company + ' 🌿\n\nEn 2 minutos te preparo tu menu semanal personalizado con productos naturales y ecologicos + lista de la compra lista para el carrito 🛒\n\nPara empezar necesito conocerte:\n\n*Nombre, genero, edad, peso (kg) y altura (cm)*\n\n_Ejemplo: Maria, mujer, 34, 65kg, 165cm_'
        elif s == 'datos':
            parsed = parse_datos(m)
            missing = faltan_datos(parsed)
            if missing:
                return 'Solo me falta: *' + ', '.join(missing) + '*\n\n_Ejemplo: Carlos, hombre, 38, 82kg, 178cm_'
            for k, v in parsed.items():
                u['data'][k] = v
            nombre = u['data'].get('nombre', '')
            u['state'] = 'personas'
            return 'Perfecto' + (', ' + nombre if nombre else '') + '! 💪\n\nEl plan nutricional es para...\n\n  👤 Solo para mi\n  👫 Para 2 personas (pareja o amigo/a)\n  👨\u200d👩\u200d👧\u200d👦 Familiar (3 o mas personas)'
        elif s == 'personas':
            ml = m.lower()
            if any(w in ml for w in ['2','dos','pareja','amigo']): u['data']['personas'] = '2 personas'
            elif any(w in ml for w in ['3','familia','familiar','mas','tres']): u['data']['personas'] = 'familia (3 o mas personas)'
            else: u['data']['personas'] = '1 persona'
            u['state'] = 'objetivo'
            return 'Cual es vuestro objetivo principal? 🎯\n\n  1️⃣ Perder peso\n  2️⃣ Ganar musculo\n  3️⃣ Mas energia y vitalidad\n  4️⃣ Comer mas sano y natural\n  5️⃣ Mejorar la digestion'
        elif s == 'objetivo':
            u['data']['objetivo'] = m
            u['state'] = 'cocina'
            return 'Como es vuestra relacion con la cocina? 🍳\n\n  ⚡ Poco tiempo, recetas rapidas\n  👨\u200d🍳 Me gusta cocinar\n  🥗 Solo platos sencillos\n  📦 Batch cooking (preparar el domingo)'
        elif s == 'cocina':
            u['data']['cocina'] = m
            u['state'] = 'restricciones'
            return 'Teneis alguna restriccion alimentaria? 🚫\n\n  ✅ Ninguna\n  🌱 Vegano/Vegetariano\n  🌾 Sin gluten\n  🥛 Sin lactosa\n  🐟 Sin pescado\n  ✏️ Otra (escribela)'
        elif s == 'restricciones':
            u['data']['restricciones'] = m
            u['state'] = 'presupuesto'
            return ('Cuanto quieres gastar a la semana en la compra?\n\n_Escribe la cantidad en euros, ej: 60_')
        elif s == 'presupuesto':
            nums = re.findall(r'\d+', m)
            u['data']['presupuesto'] = nums[0] if nums else '65'
            u['state'] = 'supermercado'
            return 'En que supermercado compras habitualmente?\n\n_ej: Mercadona_'
        elif s == 'supermercado':
            u['data']['supermercado'] = m.strip() if m.strip() else 'Mercadona'
            u['state'] = 'plan_listo'
            partes = self._generar_plan_partes(u['data'])
            u['plan'] = '\n\n'.join(partes)
            u['plan_count'] = u.get('plan_count', 0) + 1
            intro = 'Analizando tu perfil…' + nombre_str
            return [intro] + partes
        elif s == 'plan_listo':
            return self._gpt_libre(m, u)
        else:
            u['state'] = 'welcome'
            return 'Escribe *Hola* para empezar 👋'

    def _generar_plan(self, data):
        company = self.config['branding']['company_name']
        checkout = self.config.get('integrations', {}).get('cart', {}).get('checkout_url', self.config['branding']['website'])
        cal = calorias(data)
        personas = data.get('personas', '1 persona')
        cats = self.config.get('catalog', {}).get('categories', [])
        catalogo = ''
        if cats:
            catalogo = 'PRODUCTOS DE ' + company.upper() + ':\n'
            for cat in cats[:3]:
                catalogo += '\n' + cat['name'] + ':\n'
                for p in cat.get('products', [])[:3]:
                    line = '  - ' + p['name']
                    if p.get('price'): line += ' (' + p['price'] + ')'
                    if p.get('bestseller'): line += ' ESTRELLA'
                    catalogo += line + '\n'

        prompt = ('Eres ZIA nutricionista de ' + company + '.\n\n'
                  'PERFIL: ' + data.get('nombre','') + ', ' + data.get('genero','') + ', '
                  + data.get('edad','') + ' anos, ' + data.get('peso','') + 'kg, '
                  + data.get('altura','') + 'cm, ' + str(cal) + ' kcal/dia\n'
                  'Plan para: ' + personas + '\n'
                  'Objetivo: ' + data.get('objetivo','') + '\n'
                  'Restricciones: ' + data.get('restricciones','Ninguna') + '\n'
                  'Presupuesto: ' + data.get('presupuesto','') + ' euros/semana\n\n'
                  + catalogo +
                  '\nGENERA (MUY CORTO, maximo 180 palabras):\n'
                  '1. MENU: solo Lunes Mie Vie con Desayuno Comida Cena. Sin gramos.\n'
                  '2. LISTA COMPRA: 6-8 productos con precio. Total estimado.\n'
                  '3. Un producto ESTRELLA recomendado.\n'
                  '4. Anadir al carrito: ' + checkout + '\n\n'
                  'Usa emojis. Tono motivador. MAXIMO 180 PALABRAS.')

        try:
            r = self.openai.chat.completions.create(
                model=self.config.get('ai',{}).get('model','gpt-4o-mini'),
                messages=[
                    {'role': 'system', 'content': 'Eres ZIA nutricionista de ' + company + '. Responde en espanol con emojis. Maximo 180 palabras.'},
                    {'role': 'user', 'content': prompt}
                ],
                max_tokens=400,
                temperature=0.7,
                timeout=20
            )
            return r.choices[0].message.content
        except Exception as e:
            return 'Error generando plan: ' + str(e)[:60] + '. Escribe *Hola* para reintentar.'

    def _generar_plan_partes(self, data):
        return [self._generar_plan(data)]

    def _gpt_libre(self, message, u):
        company = self.config['branding']['company_name']
        checkout = self.config.get('integrations',{}).get('cart',{}).get('checkout_url', self.config['branding']['website'])
        data = u['data']
        plan = u.get('plan','')[:200] if u.get('plan') else ''
        system = ('Eres ZIA de ' + company + '. Perfil: ' + data.get('nombre','')
                  + ', objetivo: ' + data.get('objetivo','')
                  + ', restricciones: ' + data.get('restricciones','Ninguna')
                  + '. Plan actual: ' + plan
                  + '. Carrito: ' + checkout
                  + '. Ayuda con modificaciones y preguntas. Usa emojis. MAXIMO 100 palabras. Espanol.')
        history = u.get('history', [])
        history.append({'role':'user','content':message})
        if len(history) > 6: history = history[-6:]
        try:
            r = self.openai.chat.completions.create(
                model=self.config.get('ai',{}).get('model','gpt-4o-mini'),
                messages=[{'role':'system','content':system}]+history,
                max_tokens=250,
                temperature=0.7,
                timeout=20
            )
            reply = r.choices[0].message.content
            history.append({'role':'assistant','content':reply})
            u['history'] = history
            return reply
        except Exception as e:
            return 'Error: ' + str(e)[:50]
