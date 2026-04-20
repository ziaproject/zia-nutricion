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
        checkout = self.config.get('integrations', {}).get('cart', {}).get('checkout_url', self.config['branding']['website'])
        nombre = u['data'].get('nombre', '')
        nombre_str = ', ' + nombre if nombre else ''
        if is_reset(m):
            self.reset_user(user_id)
            u = self._get_user(user_id)
        s = u['state']

        if s == 'welcome':
            u['state'] = 'datos'
            return ('Hola! Soy ZIA, tu asesora nutricional de ' + company + ' 🌿\n\n'
                    'En 2 minutos te preparo tu menu semanal personalizado + lista de la compra lista para el carrito 🛒\n\n'
                    'Para empezar necesito conocerte:\n\n'
                    '*Nombre, genero, edad, peso (kg) y altura (cm)*\n\n'
                    '_Ejemplo: Maria, mujer, 34, 65kg, 165cm_')

        elif s == 'datos':
            parsed = parse_datos(m)
            missing = faltan_datos(parsed)
            if missing:
                return 'Solo me falta: *' + ', '.join(missing) + '*\n\n_Ejemplo: Carlos, hombre, 38, 82kg, 178cm_'
            for k, v in parsed.items():
                u['data'][k] = v
            nombre = u['data'].get('nombre', '')
            u['state'] = 'personas'
            return ('Perfecto' + (', ' + nombre if nombre else '') + '! 💪\n\n'
                    'El plan nutricional es para...\n\n'
                    '  1️⃣ Solo para mi\n'
                    '  2️⃣ Para 2 personas\n'
                    '  3️⃣ Familiar (3 o mas personas)')

        elif s == 'personas':
            ml = m.strip()
            if ml == '2' or any(w in ml.lower() for w in ['dos','pareja','amigo']): u['data']['personas'] = '2 personas'
            elif ml == '3' or any(w in ml.lower() for w in ['familia','familiar','tres']): u['data']['personas'] = 'familia (3 o mas personas)'
            else: u['data']['personas'] = '1 persona'
            u['state'] = 'objetivo'
            return ('Cual es tu objetivo principal? 🎯\n\n'
                    '  1️⃣ Perder grasa\n'
                    '  2️⃣ Ganar musculo\n'
                    '  3️⃣ Mas energia y vitalidad\n'
                    '  4️⃣ Comer mas sano y natural\n'
                    '  5️⃣ Mejorar la digestion')

        elif s == 'objetivo':
            opts = {'1':'Perder grasa','2':'Ganar musculo','3':'Mas energia y vitalidad','4':'Comer mas sano','5':'Mejorar la digestion'}
            u['data']['objetivo'] = opts.get(m.strip(), m)
            u['state'] = 'cocina'
            return ('Como es tu relacion con la cocina? 🍳\n\n'
                    '  1️⃣ Poco tiempo, recetas rapidas\n'
                    '  2️⃣ Me gusta cocinar\n'
                    '  3️⃣ Solo platos sencillos\n'
                    '  4️⃣ Batch cooking (preparar el domingo)')

        elif s == 'cocina':
            opts = {'1':'Poco tiempo, recetas rapidas','2':'Me gusta cocinar','3':'Solo platos sencillos','4':'Batch cooking'}
            u['data']['cocina'] = opts.get(m.strip(), m)
            u['state'] = 'restricciones'
            return ('Tienes alguna restriccion alimentaria? 🚫\n\n'
                    '  1️⃣ Ninguna\n'
                    '  2️⃣ Vegano/Vegetariano\n'
                    '  3️⃣ Sin gluten\n'
                    '  4️⃣ Sin lactosa\n'
                    '  5️⃣ Sin pescado\n'
                    '  6️⃣ Otra (escribela)')

        elif s == 'restricciones':
            opts = {'1':'Ninguna','2':'Vegano/Vegetariano','3':'Sin gluten','4':'Sin lactosa','5':'Sin pescado'}
            u['data']['restricciones'] = opts.get(m.strip(), m)
            u['state'] = 'presupuesto'
            return ('Ultimo paso' + nombre_str + '! 💰\n\n'
                    'Cuanto quieres gastar esta semana?\n\n'
                    '  1️⃣ 30-50 euros\n'
                    '  2️⃣ 50-80 euros\n'
                    '  3️⃣ 80-120 euros\n'
                    '  4️⃣ Mas de 120 euros')

        elif s == 'presupuesto':
            opts = {'1':'40','2':'65','3':'100','4':'130'}
            u['data']['presupuesto'] = opts.get(m.strip(), '65')
            u['state'] = 'plan_listo'
            partes = self._generar_plan_partes(u['data'])
            u['plan'] = '\n\n'.join(partes)
            u['plan_count'] = u.get('plan_count', 0) + 1
            intro = '🔍 Analizando tu perfil...\n🌿 Seleccionando productos de ' + company + '...\n✨ Aqui va tu plan' + nombre_str + '!'
            return [intro] + partes

        elif s == 'plan_listo':
            ml = m.strip().lower()
            if ml in ['1','carrito','anadir al carrito','añadir al carrito']:
                return ('🛒 Aqui tienes tu carrito con todos los productos' + nombre_str + '!\n\n'
                        + checkout + '\n\n'
                        '_Pulsa el link para anadir todo directamente_ ✅')
            elif ml in ['2','cambiar','cambiar algo']:
                u['state'] = 'cambiar'
                return '✏️ Que quieres cambiar' + nombre_str + '? Dime el dia o el plato y te preparo una alternativa 🍽️'
            elif ml in ['3','guardar','guardar lista']:
                lista = u.get('plan','')
                partes = lista.split('\n\n')
                lista_limpia = partes[-1] if partes else lista
                return '💾 Aqui tienes tu lista de la compra' + nombre_str + ':\n\n' + lista_limpia
            else:
                return self._gpt_libre(m, u)

        elif s == 'cambiar':
            u['state'] = 'plan_listo'
            return self._gpt_libre(m, u)

        else:
            u['state'] = 'welcome'
            return 'Escribe *Hola* para empezar 👋'

    def _generar_plan_partes(self, data):
        company = self.config['branding']['company_name']
        checkout = self.config.get('integrations', {}).get('cart', {}).get('checkout_url', self.config['branding']['website'])
        cal = calorias(data)
        personas = data.get('personas', '1 persona')
        presupuesto = data.get('presupuesto', '65')
        cats = self.config.get('catalog', {}).get('categories', [])
        catalogo = ''
        if cats:
            catalogo = 'PRODUCTOS DE ' + company.upper() + ':\n'
            for cat in cats[:5]:
                catalogo += '\n' + cat['name'] + ':\n'
                for p in cat.get('products', [])[:5]:
                    line = '  - ' + p['name']
                    if p.get('price'): line += ' (' + p['price'] + ')'
                    if p.get('bestseller'): line += ' ESTRELLA'
                    catalogo += line + '\n'
        perfil = ('PERFIL: ' + data.get('nombre','') + ', ' + data.get('genero','') + ', '
                  + data.get('edad','') + ' anos, ' + data.get('peso','') + 'kg, '
                  + data.get('altura','') + 'cm, ' + str(cal) + ' kcal/dia. '
                  'Plan para: ' + personas + '. Objetivo: ' + data.get('objetivo','') + '. '
                  'Cocina: ' + data.get('cocina','') + '. '
                  'Restricciones: ' + data.get('restricciones','Ninguna') + '. '
                  'Presupuesto semanal EXACTO: ' + presupuesto + ' euros.')

        prompt1 = ('Eres ZIA nutricionista de ' + company + '. ' + perfil + '\n\n' + catalogo +
                   '\nGENERA SOLO el menu de *LUNES*, *MARTES* y *MIERCOLES*. '
                   'Cada dia: Desayuno, Comida y Cena con cantidades en gramos. '
                   'SIN tiempos de preparacion. SIN frases motivadoras al final. '
                   'Termina en la ultima cena del miercoles. Usa emojis. Maximo 220 palabras.')

        prompt2 = ('Eres ZIA nutricionista de ' + company + '. ' + perfil + '\n\n' + catalogo +
                   '\nGENERA SOLO el menu de *JUEVES*, *VIERNES*, *SABADO* y *DOMINGO*. '
                   'Empieza DIRECTAMENTE con *Jueves:* sin ningun saludo ni introduccion. '
                   'Cada dia: Desayuno, Comida y Cena con cantidades en gramos. '
                   'SIN tiempos de preparacion. SIN frases motivadoras al final. '
                   'Termina en la ultima cena del domingo. Usa emojis. Maximo 220 palabras.')

        prompt3 = ('Eres ZIA nutricionista de ' + company + '. ' + perfil + '\n\n' + catalogo +
                   '\nGENERA la LISTA DE LA COMPRA COMPLETA con TODOS los ingredientes del menu semanal (Lunes a Domingo). '
                   'OBLIGATORIO: incluye CADA ingrediente mencionado en el menu. '
                   'OBLIGATORIO: el total debe ser entre ' + str(int(presupuesto)-15) + ' y ' + presupuesto + ' euros. '
                   'Organiza por categorias con cantidad y precio por producto. '
                   'Muestra el TOTAL al final. '
                   'Luego pon 2 productos ESTRELLA de ' + company + ' recomendados. '
                   'NO incluyas ningun link ni texto de carrito. '
                   'Usa emojis. Maximo 250 palabras.')

        model = self.config.get('ai',{}).get('model','gpt-4o-mini')
        system = 'Eres ZIA nutricionista de ' + company + '. Responde en espanol con emojis.'
        partes = []
        for prompt in [prompt1, prompt2, prompt3]:
            try:
                r = self.openai.chat.completions.create(
                    model=model,
                    messages=[{'role':'system','content':system},{'role':'user','content':prompt}],
                    max_tokens=500, temperature=0.7, timeout=25)
                partes.append(r.choices[0].message.content)
            except Exception as e:
                partes.append('Error generando esta parte: ' + str(e)[:60])

        nombre = data.get('nombre','')
        nombre_str = ', ' + nombre if nombre else ''
        partes[-1] += ('\n\n---\n¿Que quieres hacer' + nombre_str + '?\n\n'
                       '  1️⃣ Anadir al carrito\n'
                       '  2️⃣ Cambiar algo del menu\n'
                       '  3️⃣ Guardar lista\n\n'
                       '_O escribeme cualquier duda_ 💬')
        return partes

    def _gpt_libre(self, message, u):
        company = self.config['branding']['company_name']
        checkout = self.config.get('integrations',{}).get('cart',{}).get('checkout_url', self.config['branding']['website'])
        data = u['data']
        plan = u.get('plan','')[:600] if u.get('plan') else ''
        system = ('Eres ZIA de ' + company + '. '
                  'Perfil: ' + data.get('nombre','') + ', objetivo: ' + data.get('objetivo','')
                  + ', restricciones: ' + data.get('restricciones','Ninguna') + '. '
                  'Plan actual: ' + plan + '. '
                  'Carrito: ' + checkout + '. '
                  'Ayuda con modificaciones y preguntas sobre el menu o la lista. '
                  'Si el usuario pide cambiar algo, genera la alternativa directamente. '
                  'Al terminar siempre pregunta: ¿Algo mas en lo que pueda ayudarte? '
                  'Usa emojis. Maximo 200 palabras. Espanol.')
        history = u.get('history', [])
        history.append({'role':'user','content':message})
        if len(history) > 10: history = history[-10:]
        try:
            r = self.openai.chat.completions.create(
                model=self.config.get('ai',{}).get('model','gpt-4o-mini'),
                messages=[{'role':'system','content':system}]+history,
                max_tokens=400, temperature=0.7, timeout=25)
            reply = r.choices[0].message.content
            history.append({'role':'assistant','content':reply})
            u['history'] = history
            return reply
        except Exception as e:
            return 'Error: ' + str(e)[:50]
