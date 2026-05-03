import json
import os
import re
import unicodedata
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

RESET_WORDS = ['inicio','reset','reiniciar','start','menu','nuevo']


def normalize_text(text):
    value = unicodedata.normalize('NFKD', str(text or '').lower())
    return ''.join(c for c in value if not unicodedata.combining(c)).strip()


COACH_TONE = (
    'Eres ZIA, coach nutricional motivadora y cercana. Usa siempre un tono positivo, '
    'empático y motivador. Incluye frases de ánimo. Celebra los logros del usuario. '
    'Nunca respondas con listas frías - usa un tono de coach que inspira.'
)


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
        key = f"{self.client_id}_{uid}"
        if key not in self._users:
            self._users[key] = {'state': 'welcome', 'data': {}, 'plan': None, 'history': [], 'plan_count': 0}
        return self._users[key]

    def reset_user(self, uid):
        key = f"{self.client_id}_{uid}"
        count = self._users.get(key, {}).get('plan_count', 0)
        self._users[key] = {'state': 'welcome', 'data': {}, 'plan': None, 'history': [], 'plan_count': count}

    def get_welcome_message(self):
        return self.config['bot']['welcome_message']

    def _send_immediate_message(self, to, body):
        try:
            from twilio.rest import Client as TwilioClient

            sid = os.environ.get('TWILIO_ACCOUNT_SID')
            token = os.environ.get('TWILIO_AUTH_TOKEN')
            from_number = os.environ.get('TWILIO_WHATSAPP_FROM', 'whatsapp:+14155238886')
            if not sid or not token:
                return
            TwilioClient(sid, token).messages.create(body=body, from_=from_number, to=to)
        except Exception as e:
            print('No pude enviar mensaje inmediato:', str(e)[:80])

    def _profile_for_prompt(self, data):
        descripcion = data.get('descripcion_grupo', '').strip()
        if descripcion and data.get('personas') != '1 persona':
            return descripcion
        partes = [
            data.get('nombre', ''),
            data.get('genero', ''),
            data.get('edad', ''),
            data.get('peso', ''),
            data.get('altura', ''),
            data.get('objetivo', ''),
            data.get('restricciones', 'Ninguna'),
        ]
        return ', '.join([p for p in partes if p])

    def _supermercado_nombre(self, value):
        super_map = {
            '1': 'Mercadona', 'mercadona': 'Mercadona',
            '2': 'Lidl', 'lidl': 'Lidl',
            '3': 'Aldi', 'aldi': 'Aldi',
            '4': 'Carrefour', 'carrefour': 'Carrefour',
            '5': 'Dia', 'dia': 'Dia',
            '6': 'Consum', 'consum': 'Consum',
            '7': 'Supercor', 'supercor': 'Supercor',
            '8': 'El Corte Ingles', 'el corte ingles': 'El Corte Ingles',
        }
        raw = str(value or '').strip()
        if not raw:
            return 'Mercadona'
        return super_map.get(normalize_text(raw), raw)

    def _menu_principal_text(self, data):
        nombre = data.get('nombre', '')
        titulo = ('Que quieres hacer ahora, ' + nombre + '?') if nombre else '¿Que quieres hacer ahora?'
        return (
            titulo + '\n\n'
            '1️⃣ 🍽️ Comer mejor hoy\n'
            '2️⃣ 🛒 Hacer la compra inteligente\n'
            '3️⃣ 📸 Foto nevera\n'
            '4️⃣ 🧠 Mejorar habitos\n'
            '5️⃣ 🥗 Dieta especifica\n'
            '6️⃣ 📊 Ver mi progreso semanal\n'
            '7️⃣ 💊 Qué suplementos necesito'
        )

    def _retail_text_and_image_url(self, message):
        if isinstance(message, str):
            return message.strip(), None
        if not isinstance(message, dict):
            return str(message).strip(), None
        raw_text = (
            message.get('text')
            or message.get('body')
            or message.get('caption')
            or ''
        )
        text = raw_text.strip() if isinstance(raw_text, str) else ''
        url = None
        for key in ('image_url', 'media_url', 'imageUrl', 'mediaUrl', 'MediaUrl0'):
            v = message.get(key)
            if isinstance(v, str) and v.strip():
                url = v.strip()
                break
            if isinstance(v, dict) and v.get('url'):
                uu = str(v['url']).strip()
                if uu:
                    url = uu
                    break
        if not url:
            im = message.get('image')
            if isinstance(im, str) and (
                im.startswith('http://') or im.startswith('https://') or im.startswith('data:')
            ):
                url = im.strip()
            elif isinstance(im, dict) and im.get('url'):
                uu = str(im['url']).strip()
                if uu:
                    url = uu
        return text, url

    def process_message(self, user_id, message, plan_type='pro'):
        meta = self.config.get('_meta')
        if isinstance(meta, dict) and meta.get('type') == 'retail-asesor':
            return self._process_retail_asesor(user_id, message)
        u = self._get_user(user_id)
        if isinstance(message, dict):
            m, _ = self._retail_text_and_image_url(message)
        else:
            m = (message or '').strip()
        company = self.config['branding']['company_name']
        nombre = u['data'].get('nombre', '')
        nombre_str = ', ' + nombre if nombre else ''
        if is_reset(m):
            self.reset_user(user_id)
            u = self._get_user(user_id)
        s = u['state']
        if s == 'welcome':
            u['state'] = 'tipo_plan'
            return 'Hola! Soy ZIA, tu asesora nutricional de ' + company + ' 🌿\n\nEn 2 minutos te preparo tu menu semanal personalizado con productos naturales y ecologicos + lista de la compra lista para el carrito 🛒\n\nPara quien es el plan?\n\n  👤 Plan individual (solo para mi)\n  👫 Plan en pareja (2 personas)\n  👨‍👩‍👧‍👦 Plan familiar (3 o más personas)'
        elif s == 'tipo_plan':
            ml = normalize_text(m)
            if (
                'familia' in ml or 'familiar' in ml or 'mis hijos' in ml
                or 'somos 3' in ml or 'somos 4' in ml or 'somos 5' in ml
                or ml in ['3', '4', '5'] or 'tres' in ml
            ):
                u['data']['personas'] = 'familia (3 o mas personas)'
                u['data']['num_personas'] = 4
                u['state'] = 'datos_familia'
                return 'Perfecto. Describeme a la familia en un mensaje libre: cuantas personas sois, edades aproximadas, objetivos y restricciones si las hay.'
            if '2' in ml or 'dos' in ml or 'pareja' in ml or 'amigo' in ml:
                u['data']['personas'] = '2 personas'
                u['data']['num_personas'] = 2
                u['state'] = 'datos_pareja'
                return 'Perfecto. Describeme a las 2 personas en un mensaje libre: nombres, genero, edad, peso, altura, objetivo y restricciones si las hay.'
            if (
                'individual' in ml or 'yo solo' in ml or 'una persona' in ml
                or 'solo' in ml or ml == '1' or '1 persona' in ml or ml == 'mi'
            ):
                u['data']['personas'] = '1 persona'
                u['data']['num_personas'] = 1
                u['state'] = 'datos'
                return 'Perfecto. Para empezar necesito conocerte:\n\n*Nombre, genero, edad, peso (kg) y altura (cm)*\n\n_Ejemplo: Maria, mujer, 34, 65kg, 165cm_'
            return 'No te he entendido 😊 Elige una opcion:\n\n👤 Solo para mi\n👫 Para 2 personas\n👨‍👩‍👧‍👦 Familiar (3 o mas personas)'
        elif s == 'datos_pareja':
            if len(m.split()) < 5:
                return 'Necesito un poco mas de detalle 😊 Describeme a las 2 personas: edades, objetivos, restricciones y cualquier dato importante.'
            u['data']['descripcion_grupo'] = m
            u['state'] = 'presupuesto'
            return ('Perfecto. Cuanto quieres gastar a la semana en la compra?\n\n_Escribe la cantidad en euros, ej: 60_')
        elif s == 'datos_familia':
            if len(m.split()) < 5:
                return 'Necesito un poco mas de detalle 😊 Cuéntame cuantas personas sois, edades aproximadas, objetivos y restricciones.'
            u['data']['descripcion_grupo'] = m
            u['state'] = 'presupuesto'
            return ('Perfecto. Cuanto quieres gastar a la semana en la compra?\n\n_Escribe la cantidad en euros, ej: 60_')
        elif s == 'datos':
            parsed = parse_datos(m)
            missing = faltan_datos(parsed)
            if missing:
                return 'Solo me falta: *' + ', '.join(missing) + '*\n\n_Ejemplo: Carlos, hombre, 38, 82kg, 178cm_'
            for k, v in parsed.items():
                u['data'][k] = v
            nombre = u['data'].get('nombre', '')
            if u['data'].get('personas'):
                u['state'] = 'objetivo'
                return 'Cual es vuestro objetivo principal? 🎯\n\n  1️⃣ Perder peso\n  2️⃣ Ganar musculo\n  3️⃣ Mas energia y vitalidad\n  4️⃣ Comer mas sano y natural\n  5️⃣ Mejorar la digestion'
            u['state'] = 'personas'
            return 'Perfecto' + (', ' + nombre if nombre else '') + '! 💪\n\nEl plan nutricional es para...\n\n  👤 Solo para mi\n  👫 Para 2 personas (pareja o amigo/a)\n  👨‍👩‍👧‍👦 Familiar (3 o mas personas)'
        elif s == 'personas':
            opts = {'1': '1 persona', '2': '2 personas', '3': 'familia (3 o mas personas)'}
            ml = m.strip().lower()
            elegido = opts.get(m.strip(), None)
            if not elegido:
                if 'solo' in ml or 'mi' in ml or 'una' in ml or '1' in ml: elegido = '1 persona'
                elif '2' in ml or 'dos' in ml or 'pareja' in ml or 'amigo' in ml: elegido = '2 personas'
                elif '3' in ml or 'familia' in ml or 'familiar' in ml or 'mas' in ml or 'tres' in ml: elegido = 'familia (3 o mas personas)'
            if not elegido:
                return 'No te he entendido 😊 Elige una opcion:\n\n👤 Solo para mi\n👫 Para 2 personas\n👨‍👩‍👧‍👦 Familiar (3 o mas personas)'
            u['data']['personas'] = elegido
            if elegido == '1 persona':
                u['data']['num_personas'] = 1
            elif elegido == '2 personas':
                u['data']['num_personas'] = 2
            else:
                u['data']['num_personas'] = 4
            u['state'] = 'objetivo'
            return 'Cual es vuestro objetivo principal? 🎯\n\n  1️⃣ Perder peso\n  2️⃣ Ganar musculo\n  3️⃣ Mas energia y vitalidad\n  4️⃣ Comer mas sano y natural\n  5️⃣ Mejorar la digestion'
        elif s == 'objetivo':
            opts = {'1':'Perder peso','2':'Ganar musculo','3':'Mas energia y vitalidad','4':'Comer mas sano','5':'Mejorar la digestion'}
            ml = normalize_text(m)
            elegido = opts.get(m.strip(), None)
            if not elegido:
                if 'peso' in ml or 'grasa' in ml or 'adelgazar' in ml or 'estar en forma' in ml: elegido = 'Perder peso'
                elif 'musculo' in ml or 'muscu' in ml: elegido = 'Ganar musculo'
                elif 'energia' in ml: elegido = 'Mas energia y vitalidad'
                elif 'sano' in ml or 'salud' in ml: elegido = 'Comer mas sano'
                elif 'digest' in ml: elegido = 'Mejorar la digestion'
            if not elegido:
                return 'No te he entendido 😊 Elige una opcion:\n\n1️⃣ Perder peso\n2️⃣ Ganar musculo\n3️⃣ Mas energia\n4️⃣ Comer mas sano\n5️⃣ Mejorar digestion'
            u['data']['objetivo'] = elegido
            u['state'] = 'cocina'
            return 'Como es vuestra relacion con la cocina? 🍳\n\n  ⚡ Poco tiempo, recetas rapidas\n  👨‍🍳 Me gusta cocinar\n  🥗 Solo platos sencillos\n  📦 Batch cooking (preparar el domingo)'
        elif s == 'cocina':
            opts = {'1':'Poco tiempo, recetas rapidas','2':'Me gusta cocinar','3':'Solo platos sencillos','4':'Batch cooking'}
            ml = normalize_text(m)
            elegido = opts.get(m.strip(), None)
            if not elegido:
                if 'poco' in ml or 'rapido' in ml or 'tiempo' in ml: elegido = 'Poco tiempo, recetas rapidas'
                elif 'gusta' in ml or 'cocin' in ml: elegido = 'Me gusta cocinar'
                elif 'sencill' in ml: elegido = 'Solo platos sencillos'
                elif 'batch' in ml or 'domingo' in ml: elegido = 'Batch cooking'
            if not elegido:
                return 'No te he entendido 😊 Elige una opcion:\n\n⚡ Poco tiempo, recetas rapidas\n👨‍🍳 Me gusta cocinar\n🥗 Solo platos sencillos\n📦 Batch cooking'
            u['data']['cocina'] = elegido
            u['state'] = 'restricciones'
            return 'Teneis alguna restriccion alimentaria? 🚫\n\n  ✅ Ninguna\n  🌱 Vegano/Vegetariano\n  🌾 Sin gluten\n  🥛 Sin lactosa\n  🐟 Sin pescado\n  ✏️ Otra (escribela)'
        elif s == 'restricciones':
            opts = {
                '1': 'Ninguna', '2': 'Vegano/Vegetariano', '3': 'Sin gluten',
                '4': 'Sin lactosa', '5': 'Sin pescado'
            }
            ml = normalize_text(m)
            elegido = opts.get(m.strip(), None)
            if not elegido:
                if 'ninguna' in ml or 'no' == ml: elegido = 'Ninguna'
                elif 'vegan' in ml or 'vegetarian' in ml: elegido = 'Vegano/Vegetariano'
                elif 'gluten' in ml or 'celiac' in ml: elegido = 'Sin gluten'
                elif 'lactosa' in ml or 'lacteo' in ml or 'leche' in ml: elegido = 'Sin lactosa'
                elif 'pescado' in ml: elegido = 'Sin pescado'
                elif m.strip(): elegido = m.strip()
            if not elegido:
                return 'No te he entendido 😊 Elige una opcion:\n\n✅ Ninguna\n🌱 Vegano/Vegetariano\n🌾 Sin gluten\n🥛 Sin lactosa\n🐟 Sin pescado\n✏️ Otra (escribela)'
            u['data']['restricciones'] = elegido
            u['state'] = 'presupuesto'
            return ('Cuanto quieres gastar a la semana en la compra?\n\n_Escribe la cantidad en euros, ej: 60_')
        elif s == 'presupuesto':
            nums = re.findall(r'\d+', m)
            u['data']['presupuesto'] = nums[0] if nums else '65'
            u['state'] = 'supermercado'
            return '🏪 En que supermercado sueles comprar?\n\n  1️⃣ Mercadona\n  2️⃣ Lidl\n  3️⃣ Aldi\n  4️⃣ Carrefour\n  5️⃣ Dia\n  6️⃣ Consum\n  7️⃣ Supercor\n  8️⃣ El Corte Ingles\n\n_O escribe el nombre directamente_'
        elif s == 'supermercado':
            super_nombre = self._supermercado_nombre(m)
            u['data']['supermercado'] = super_nombre
            u['state'] = 'plan_listo'
            mensaje_espera = 'Perfecto! 🌿 Estoy preparando tu plan semanal personalizado y tu lista de la compra para ' + super_nombre + '. Dame un momento... ⏳'
            msgs = self._generar_plan_partes(u['data'])
            u['plan'] = '\n\n'.join(msgs[1:])
            u['plan_count'] = u.get('plan_count', 0) + 1
            return [mensaje_espera] + msgs
        elif s == 'plan_listo':
            ml = m.lower()
            if ml in ['1', 'si', 'sí', 'confirmar', 'confirmo', 'dale', 'ok', 'vale', 'yes', 'claro']:
                super_nombre = u['data'].get('supermercado', 'Mercadona')
                SUPER_URLS = {
                    'mercadona': 'https://tienda.mercadona.es',
                    'lidl': 'https://www.lidl.es',
                    'aldi': 'https://www.aldi.es',
                    'carrefour': 'https://www.carrefour.es',
                    'dia': 'https://www.dia.es',
                    'consum': 'https://www.consum.es',
                    'supercor': 'https://www.supercor.es',
                    'el corte ingles': 'https://www.elcorteingles.es/supermercado',
                    'el corte inglés': 'https://www.elcorteingles.es/supermercado',
                }
                sk = normalize_text(super_nombre)
                url = SUPER_URLS.get(sk, 'https://tienda.mercadona.es')
                nombre = u['data'].get('nombre', '')
                saludo = ('Perfecto, ' + nombre + '!') if nombre else 'Perfecto! 🌿'
                msg1 = saludo + ' Tu link para ' + super_nombre + ':\n\n' + url + '\n\nQue disfrutes de tu semana saludable! 💪🥗'
                msg2 = self._menu_principal_text(u['data'])
                u['state'] = 'menu_principal'
                return [msg1, msg2]
            if m.strip() == '2' or re.search(r'\bcomparar\b', ml):
                u['state'] = 'confirmando_compra'
                try:
                    presupuesto = float(str(u['data'].get('presupuesto', '65')).replace(',', '.'))
                except Exception:
                    presupuesto = 65.0
                FACTORES = [
                    ('🟢', 'Mercadona', 'mercadona', 1.0),
                    ('🔵', 'Lidl', 'lidl', 0.88),
                    ('🟡', 'Aldi', 'aldi', 0.85),
                    ('🔴', 'Carrefour', 'carrefour', 1.05),
                    ('🟠', 'Dia', 'dia', 1.0),
                    ('⚪', 'Consum', 'consum', 1.0),
                    ('🟣', 'Supercor', 'supercor', 1.1),
                    ('🏛️', 'El Corte Inglés', 'el corte ingles', 1.2),
                ]
                totales = [(e, n, k, round(presupuesto * f, 2)) for e, n, k, f in FACTORES]
                min_total = min(t[3] for t in totales)
                lineas = ['🛒 COMPARATIVA DE PRECIOS\n']
                for e, n, k, total in totales:
                    estrella = ' ⭐ MÁS BARATO' if total == min_total else ''
                    lineas.append(e + ' ' + n + ' → ' + str(total) + '€' + estrella)
                lineas.append('\n¿En cuál quieres hacer la compra? Escribe el nombre.')
                u['state'] = 'eligiendo_super'
                return '\n'.join(lineas)
            SUPER_URLS = {
                'mercadona': 'https://tienda.mercadona.es',
                'lidl': 'https://www.lidl.es',
                'aldi': 'https://www.aldi.es',
                'carrefour': 'https://www.carrefour.es',
                'dia': 'https://www.dia.es',
                'consum': 'https://www.consum.es',
                'supercor': 'https://www.supercor.es',
                'el corte ingles': 'https://www.elcorteingles.es/supermercado',
                'el corte inglés': 'https://www.elcorteingles.es/supermercado',
            }
            super_key_pl = normalize_text(m)
            if super_key_pl in SUPER_URLS:
                url = SUPER_URLS.get(super_key_pl, 'https://tienda.mercadona.es')
                nombre = u['data'].get('nombre', '')
                msg1 = (
                    'Perfecto! Aqui tienes tu link directo para hacer la compra en '
                    + m.strip()
                    + ':\n\n'
                    + url
                    + '\n\nQue disfrutes de tu semana saludable! '
                )
                msg2 = self._menu_principal_text(u['data'])
                u['state'] = 'menu_principal'
                return [msg1, msg2]
            if m.strip() == '4' or 'nevera' in ml or 'foto' in ml:
                u['state'] = 'esperando_foto_nevera'
                return (
                    'Perfecto! Enviame una foto de tu nevera o despensa y te propongo 3 recetas rapidas con lo que tienes 📸'
                )
            if m.strip() == '5' or any(
                k in ml for k in ('keto', 'vegana', 'mediterranea', 'ayuno', 'vegetariana')
            ):
                u['state'] = 'eligiendo_dieta'
                return (
                    'Que tipo de dieta quieres?\n\n1️⃣ Keto\n2️⃣ Vegana\n3️⃣ Mediterranea\n'
                    '4️⃣ Ayuno 16:8\n5️⃣ Vegetariana'
                )
            return self._gpt_libre(message if isinstance(message, dict) else m, u)
        elif s == 'confirmando_compra':
            SUPER_URLS = {
                'mercadona': 'https://tienda.mercadona.es',
                'lidl': 'https://www.lidl.es',
                'aldi': 'https://www.aldi.es',
                'carrefour': 'https://www.carrefour.es',
                'dia': 'https://www.dia.es',
                'consum': 'https://www.consum.es',
                'supercor': 'https://www.supercor.es',
                'el corte ingles': 'https://www.elcorteingles.es/supermercado',
                'el corte inglés': 'https://www.elcorteingles.es/supermercado',
            }
            super_nombre = u['data'].get('supermercado', 'Mercadona')
            nombre = u['data'].get('nombre', '')
            ml = m.strip().lower()
            if ml in ['1', 'si', 'sí', 'confirmar', 'confirmo', 'dale', 'ok', 'vale', 'yes', 'claro']:
                sk = normalize_text(super_nombre)
                url = SUPER_URLS.get(sk, 'https://tienda.mercadona.es')
                saludo = ('Perfecto, ' + nombre + '!') if nombre else 'Perfecto! 🌿'
                msg1 = (
                    saludo
                    + ' Tu link para '
                    + super_nombre
                    + ':\n\n'
                    + url
                    + '\n\nQue disfrutes de tu semana saludable! 💪🥗'
                )
                msg2 = self._menu_principal_text(u['data'])
                u['state'] = 'menu_principal'
                return [msg1, msg2]
            elif ml in ['2', 'comparar', 'comparar precios', 'otros']:
                try:
                    presupuesto = float(u['data'].get('presupuesto', '65'))
                except Exception:
                    presupuesto = 65.0
                FACTORES = [
                    ('🟢', 'Mercadona', 'mercadona', 1.0),
                    ('🔵', 'Lidl', 'lidl', 0.88),
                    ('🟡', 'Aldi', 'aldi', 0.85),
                    ('🔴', 'Carrefour', 'carrefour', 1.05),
                    ('🟠', 'Dia', 'dia', 1.0),
                    ('⚪', 'Consum', 'consum', 1.0),
                    ('🟣', 'Supercor', 'supercor', 1.1),
                    ('🏛️', 'El Corte Inglés', 'el corte ingles', 1.2),
                ]
                totales = [(e, n, k, round(presupuesto * f, 2)) for e, n, k, f in FACTORES]
                min_total = min(t[3] for t in totales)
                lineas = ['🛒 COMPARATIVA DE PRECIOS\n']
                for e, n, k, total in totales:
                    estrella = ' ⭐ MÁS BARATO' if total == min_total else ''
                    lineas.append(e + ' ' + n + ' → ' + str(total) + '€' + estrella)
                lineas.append('\n¿En cuál quieres hacer la compra? Escribe el nombre.')
                u['state'] = 'eligiendo_super'
                return '\n'.join(lineas)
            else:
                return self._gpt_libre(message if isinstance(message, dict) else m, u)
        elif s == 'menu_principal':
            ml = m.lower()
            company = self.config['branding']['company_name']
            data = u['data']
            if m.strip() == '1' or 'recetas' in ml or 'comer' in ml:
                u['state'] = 'recetas_rapidas'
                return (
                    'Cuentame que te apetece comer o que tienes a mano y te preparo recetas rapidas 🍽️'
                )
            if m.strip() == '2' or re.search(r'\bcompra\b', ml):
                msgs = self._generar_plan_partes(u['data'])
                u['plan'] = '\n\n'.join(msgs[1:])
                u['plan_count'] = u.get('plan_count', 0) + 1
                u['state'] = 'plan_listo'
                return msgs
            if m.strip() == '3' or 'nevera' in ml or 'foto' in ml:
                u['state'] = 'esperando_foto_nevera'
                return (
                    'Perfecto! Enviame una foto de tu nevera o despensa y te propongo 3 recetas rapidas con lo que tienes 📸'
                )
            if m.strip() == '4' or 'habitos' in ml:
                prompt_h = (
                    'Eres ZIA nutricionista de '
                    + company
                    + '. Ofrece consejos practicos y personalizados para mejorar la alimentacion y habitos '
                    'saludables. Perfil: '
                    + data.get('nombre', '')
                    + ', objetivo: '
                    + data.get('objetivo', '')
                    + ', restricciones: '
                    + data.get('restricciones', 'Ninguna')
                    + '. Responde en español con emojis. Maximo 200 palabras.'
                )
                try:
                    r = self.openai.chat.completions.create(
                        model=self.config.get('ai', {}).get('model', 'gpt-4o-mini'),
                        messages=[
                            {
                                'role': 'system',
                                'content': COACH_TONE + ' Eres ZIA nutricionista. Responde en español con emojis.',
                            },
                            {'role': 'user', 'content': prompt_h},
                        ],
                        max_tokens=400,
                        temperature=0.7,
                        timeout=25,
                    )
                    u['state'] = 'menu_principal'
                    menu = '\n\n---\n' + self._menu_principal_text(data)
                    return r.choices[0].message.content + menu
                except Exception:
                    return 'No pude generar los consejos. Intenta de nuevo.'
            if m.strip() == '5' or 'dieta' in ml or 'keto' in ml or 'vegana' in ml:
                u['state'] = 'eligiendo_dieta'
                return (
                    'Que tipo de dieta quieres?\n\n1️⃣ Keto\n2️⃣ Vegana\n3️⃣ Mediterranea\n'
                    '4️⃣ Ayuno 16:8\n5️⃣ Vegetariana'
                )
            if m.strip() == '6' or 'progreso' in ml or 'semana' in ml:
                u['state'] = 'progreso_semanal'
                return 'Cuéntame cómo te has sentido esta semana 💬 ¿Seguiste el plan? ¿Cómo te encuentras de energía, digestión y estado de ánimo?'
            if m.strip() == '7' or 'suplement' in ml or 'vitamina' in ml:
                u['state'] = 'suplementos'
                return (
                    '¿Cuál es tu principal preocupación ahora mismo? 💊\n'
                    '1️⃣ Me falta energía\n'
                    '2️⃣ Quiero ganar músculo\n'
                    '3️⃣ Mejorar digestión\n'
                    '4️⃣ Dormir mejor\n'
                    '5️⃣ Reforzar defensas\n'
                    '6️⃣ Perder peso'
                )
            return self._gpt_libre(message if isinstance(message, dict) else m, u)
        elif s == 'eligiendo_super':
            SUPER_URLS = {
                'mercadona': 'https://tienda.mercadona.es',
                'lidl': 'https://www.lidl.es',
                'aldi': 'https://www.aldi.es',
                'carrefour': 'https://www.carrefour.es',
                'dia': 'https://www.dia.es',
                'consum': 'https://www.consum.es',
                'supercor': 'https://www.supercor.es',
                'el corte ingles': 'https://www.elcorteingles.es/supermercado',
                'el corte inglés': 'https://www.elcorteingles.es/supermercado',
            }
            nombre = u['data'].get('nombre', '')
            super_key = normalize_text(m)
            url = SUPER_URLS.get(super_key, None)
            if url:
                super_nombre = self._supermercado_nombre(m)
                u['data']['supermercado'] = super_nombre
                u['state'] = 'menu_principal'
                saludo = ('Perfecto, ' + nombre + '!') if nombre else 'Perfecto! 🌿'
                msg1 = (
                    saludo
                    + ' Aqui tienes tu link para '
                    + super_nombre
                    + ':\n\n'
                    + url
                    + '\n\nQue disfrutes de tu semana saludable! 💪🥗'
                )
                msg2 = self._menu_principal_text(u['data'])
                return [msg1, msg2]
            else:
                return 'Escribe: Mercadona, Lidl, Aldi, Carrefour, Dia, Consum, Supercor o El Corte Inglés.'
        elif s == 'esperando_foto_nevera':
            text, image_url = self._retail_text_and_image_url(message)
            if not image_url and isinstance(message, dict):
                for key in ('MediaUrl0', 'media_url', 'imageUrl', 'image_url'):
                    if message.get(key):
                        image_url = message.get(key)
                        break
            if image_url:
                data = u.get('data', {})
                restricciones = data.get('restricciones', 'Ninguna')
                nombre = data.get('nombre', '')
                try:
                    import requests as _req
                    import base64 as _b64
                    twilio_sid = os.environ.get('TWILIO_ACCOUNT_SID', '')
                    twilio_token = os.environ.get('TWILIO_AUTH_TOKEN', '')
                    if str(image_url).startswith('data:'):
                        data_url = image_url
                    else:
                        img_response = _req.get(
                            image_url, auth=(twilio_sid, twilio_token), timeout=15
                        )
                        img_b64 = _b64.b64encode(img_response.content).decode('utf-8')
                        content_type = img_response.headers.get('Content-Type', 'image/jpeg')
                        data_url = f'data:{content_type};base64,{img_b64}'
                    r = self.openai.chat.completions.create(
                        model='gpt-4o',
                        messages=[
                            {
                                'role': 'user',
                                'content': [
                                    {
                                        'type': 'text',
                                        'text': (
                                            'Eres ZIA nutricionista. Analiza esta nevera y propón 3 recetas rapidas '
                                            'en menos de 20 minutos para '
                                            + nombre
                                            + '. Restricciones: '
                                            + restricciones
                                            + '. Responde en español con emojis. Al final indica 2-3 ingredientes '
                                            'que faltan con precio orientativo en euros.'
                                        ),
                                    },
                                    {'type': 'image_url', 'image_url': {'url': data_url}},
                                ],
                            }
                        ],
                        max_tokens=700,
                        timeout=45,
                    )
                    u['state'] = 'menu_principal'
                    menu = '\n\n---\n' + self._menu_principal_text(data)
                    return r.choices[0].message.content + menu
                except Exception as e:
                    return 'No pude analizar la foto: ' + str(e)[:80]
            else:
                return 'No he recibido la foto. Enviamela directamente como imagen 📸'
        elif s == 'recetas_rapidas':
            data = u['data']
            nombre = data.get('nombre', '')
            company = self.config['branding']['company_name']
            perfil_usuario = self._profile_for_prompt(data)
            prompt = (
                'Eres ZIA nutricionista de '
                + company
                + '. '
                'El usuario dice: '
                + m.strip()
                + '. '
                'Propón 3 recetas rapidas en menos de 20 minutos adaptadas a su perfil. '
                'Perfil: '
                + perfil_usuario
                + '. '
                'Usa emojis. Maximo 300 palabras. Responde en espanol.'
            )
            menu = '\n\n---\n' + self._menu_principal_text(data)
            u['state'] = 'menu_principal'
            try:
                r = self.openai.chat.completions.create(
                    model=self.config.get('ai', {}).get('model', 'gpt-4o-mini'),
                    messages=[
                        {
                            'role': 'system',
                            'content': COACH_TONE + ' Eres ZIA nutricionista. Responde en espanol con emojis.',
                        },
                        {'role': 'user', 'content': prompt},
                    ],
                    max_tokens=600,
                    temperature=0.7,
                    timeout=25,
                )
                return r.choices[0].message.content + menu
            except Exception as e:
                return 'No pude generar las recetas por un error o timeout. Intentalo de nuevo en unos minutos. Detalle: ' + str(e)[:80]
        elif s == 'eligiendo_dieta':
            dietas = {'1': 'keto', '2': 'vegana', '3': 'mediterranea', '4': 'ayuno 16:8', '5': 'vegetariana'}
            dieta = dietas.get(m.strip(), m.strip().lower())
            u['data']['dieta_especial'] = dieta
            data = u['data']
            company = self.config['branding']['company_name']
            super_nombre = data.get('supermercado', 'tu supermercado')
            mensaje_espera = 'Perfecto! 🌿 Estoy preparando tu plan semanal personalizado y tu lista de la compra para ' + super_nombre + '. Dame un momento... ⏳'
            perfil_usuario = self._profile_for_prompt(data)
            prompt = (
                'Eres ZIA nutricionista de '
                + company
                + '. Genera un plan semanal de dieta '
                + dieta
                + ' completo de Lunes a Domingo con Desayuno, Comida y Cena. Perfil: '
                + perfil_usuario
                + '. Usa emojis. Maximo 400 palabras.'
            )
            try:
                r = self.openai.chat.completions.create(
                    model=self.config.get('ai', {}).get('model', 'gpt-4o-mini'),
                    messages=[
                        {
                            'role': 'system',
                            'content': COACH_TONE + ' Eres ZIA nutricionista. Responde en español con emojis.',
                        },
                        {'role': 'user', 'content': prompt},
                    ],
                    max_tokens=800,
                    temperature=0.7,
                    timeout=30,
                )
                u['state'] = 'menu_principal'
                menu = '\n\n---\n' + self._menu_principal_text(data)
                return [mensaje_espera, r.choices[0].message.content + menu]
            except Exception as e:
                return 'No pude generar el plan de dieta por un error o timeout. Intentalo de nuevo en unos minutos. Detalle: ' + str(e)[:80]
        elif s == 'progreso_semanal':
            data = u['data']
            perfil_usuario = self._profile_for_prompt(data)
            prompt = (
                'Respuesta del usuario sobre como le ha ido la semana: '
                + m.strip()
                + '. Perfil: '
                + perfil_usuario
            )
            try:
                r = self.openai.chat.completions.create(
                    model=self.config.get('ai', {}).get('model', 'gpt-4o-mini'),
                    messages=[
                        {'role': 'system', 'content': 'Eres ZIA, coach nutricional motivadora. El usuario te cuenta cómo le ha ido la semana siguiendo su plan nutricional. Da un análisis empático y motivador de máximo 150 palabras, celebra sus logros, sugiere 2-3 ajustes concretos para la semana siguiente. Usa emojis y tono positivo.'},
                        {'role': 'user', 'content': prompt},
                    ],
                    max_tokens=350,
                    temperature=0.7,
                    timeout=25,
                )
                u['state'] = 'menu_principal'
                return r.choices[0].message.content + '\n\n---\n' + self._menu_principal_text(data)
            except Exception as e:
                u['state'] = 'menu_principal'
                return (
                    'No pude analizar tu progreso ahora mismo por un error o timeout, pero no pasa nada 💪 '
                    'Cuéntamelo de nuevo en unos minutos y lo revisamos juntas.'
                    '\n\n---\n' + self._menu_principal_text(data)
                )
        elif s == 'suplementos':
            data = u['data']
            perfil_usuario = self._profile_for_prompt(data)
            ml = normalize_text(m)
            opciones = {
                '1': 'Me falta energía',
                '2': 'Quiero ganar músculo',
                '3': 'Mejorar digestión',
                '4': 'Dormir mejor',
                '5': 'Reforzar defensas',
                '6': 'Perder peso',
            }
            opcion = m.strip()
            if opcion not in opciones:
                if 'perder peso' in ml or 'adelgazar' in ml or 'bajar peso' in ml:
                    opcion = '6'
                elif 'energia' in ml or 'cansancio' in ml or 'falta energia' in ml:
                    opcion = '1'
                elif 'musculo' in ml:
                    opcion = '2'
                elif 'digestion' in ml or 'estomago' in ml:
                    opcion = '3'
                elif 'dormir' in ml or 'sueno' in ml or 'insomnio' in ml:
                    opcion = '4'
                elif 'defensas' in ml or 'inmunidad' in ml or 'resfriado' in ml:
                    opcion = '5'
            necesidad = opciones.get(opcion, m.strip() or 'orientacion general sobre suplementos')
            extra = ''
            if opcion == '6':
                extra = ' Para perdida de peso incluye L-carnitina, CLA, proteina, fibra y te verde.'
            prompt = (
                'Eres ZIA nutricionista. Recomienda suplementos especificos para esta necesidad: '
                + necesidad
                + '. Perfil: '
                + perfil_usuario
                + '. Incluye dosis orientativas, consejos practicos y advertencias de seguridad basicas. '
                + extra
                + ' Indica que es orientativo y no sustituye consejo medico. Maximo 350 palabras.'
            )
            try:
                r = self.openai.chat.completions.create(
                    model=self.config.get('ai', {}).get('model', 'gpt-4o-mini'),
                    messages=[
                        {'role': 'system', 'content': COACH_TONE + ' Eres ZIA nutricionista. Responde en espanol con emojis.'},
                        {'role': 'user', 'content': prompt},
                    ],
                    max_tokens=700,
                    temperature=0.7,
                    timeout=25,
                )
                u['state'] = 'menu_principal'
                return r.choices[0].message.content + '\n\n---\n' + self._menu_principal_text(data)
            except Exception as e:
                u['state'] = 'menu_principal'
                return (
                    'No pude generar recomendaciones de suplementos ahora mismo por un error o timeout. '
                    'Aun asi, podemos seguir avanzando juntos 💪 Intentalo de nuevo en unos minutos.'
                    '\n\n---\n' + self._menu_principal_text(data)
                )
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
                    {'role': 'system', 'content': COACH_TONE + ' Eres ZIA nutricionista de ' + company + '. Responde en espanol con emojis. Maximo 180 palabras.'},
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
        company = self.config['branding']['company_name']
        cal = calorias(data)
        personas = data.get('personas', '1 persona')
        presupuesto = data.get('presupuesto', '65')
        num_personas = data.get('num_personas', 1)
        super_nombre = data.get('supermercado', 'Mercadona')
        cats = self.config.get('catalog', {}).get('categories', [])
        catalogo = ''
        if cats:
            catalogo = 'PRODUCTOS DE ' + company.upper() + ':\n'
            for cat in cats[:3]:
                catalogo += '\n' + cat['name'] + ':\n'
                for p in cat.get('products', [])[:3]:
                    line = '  - ' + p['name']
                    if p.get('price'):
                        line += ' (' + p['price'] + ')'
                    if p.get('bestseller'):
                        line += ' ESTRELLA'
                    catalogo += line + '\n'

        descripcion_grupo = data.get('descripcion_grupo', '').strip()
        if descripcion_grupo and personas != '1 persona':
            perfil = (
                'PERFIL GRUPAL: ' + descripcion_grupo + '. '
                'Plan para: ' + personas + ' (' + str(num_personas) + ' personas). '
                'Presupuesto MAXIMO: ' + presupuesto + ' euros/semana. '
                'Adapta TODAS las cantidades para ' + str(num_personas) + ' persona(s). '
                + catalogo
            )
        else:
            perfil = (
                'PERFIL: ' + data.get('nombre', '') + ', ' + data.get('genero', '') + ', '
                + data.get('edad', '') + ' anos, ' + data.get('peso', '') + 'kg, '
                + data.get('altura', '') + 'cm, ' + str(cal) + ' kcal/dia. '
                'Plan para: ' + personas + ' (' + str(num_personas) + ' personas). '
                'Objetivo: ' + data.get('objetivo', '') + '. '
                'Restricciones: ' + data.get('restricciones', 'Ninguna') + '. '
                'Presupuesto MAXIMO: ' + presupuesto + ' euros/semana. '
                'Adapta TODAS las cantidades para ' + str(num_personas) + ' persona(s). '
                + catalogo
            )

        model = self.config.get('ai', {}).get('model', 'gpt-4o-mini')
        system = COACH_TONE + ' Eres ZIA nutricionista de ' + company + '. Responde en espanol con emojis.'

        prompt1 = (
            'INSTRUCCIÓN ABSOLUTA: Tu respuesta debe empezar EXACTAMENTE con la palabra *Lunes:* como primera palabra. '
            'Nada antes. Genera SOLO Lunes, Martes y Miércoles con Desayuno, Comida y Cena. '
            'PARA en la Cena del Miércoles. '
            'Eres ZIA nutricionista. INSTRUCCION ESTRICTA: Genera UNICAMENTE Lunes, Martes y Miercoles. '
            'PROHIBIDO incluir Jueves, Viernes, Sabado o Domingo. '
            'Empieza con *Lunes:* Cada dia: Desayuno, Comida y Cena. '
            'Termina exactamente en la Cena del Miercoles. Sin texto despues. '
            + perfil
        )
        prompt2 = (
            'INSTRUCCIÓN ABSOLUTA: Tu respuesta debe empezar EXACTAMENTE con la palabra *Jueves:* como primera palabra. '
            'Nada antes. Genera SOLO Jueves, Viernes y Sábado con Desayuno, Comida y Cena. '
            'PARA en la Cena del Sábado. '
            'OBLIGATORIO incluir Desayuno, Comida Y Cena para Jueves, Viernes Y Sábado. '
            'PROHIBIDO terminar en Comida del Sábado. La Cena del Sábado es OBLIGATORIA. '
            'Eres ZIA nutricionista. INSTRUCCION ESTRICTA: Genera UNICAMENTE Jueves, Viernes y Sabado. '
            'PROHIBIDO incluir Lunes, Martes, Miercoles o Domingo. '
            'Empieza directamente con *Jueves:* Cada dia: Desayuno, Comida y Cena. '
            'Termina exactamente en la Cena del Sabado. Sin texto antes ni despues. '
            + perfil
        )
        prompt3 = (
            'INSTRUCCIÓN ABSOLUTA: Tu respuesta debe empezar EXACTAMENTE con la palabra *Domingo:* como primera palabra. '
            'Nada antes. Genera SOLO el Domingo con Desayuno, Comida y Cena. '
            'Eres ZIA nutricionista. INSTRUCCION ESTRICTA: Genera UNICAMENTE el Domingo. '
            'PROHIBIDO incluir cualquier otro dia de la semana. '
            'PROHIBIDO incluir lista de la compra o precios. '
            'Empieza directamente con *Domingo:* con Desayuno, Comida y Cena. '
            'Termina exactamente en la Cena del Domingo. Sin texto antes ni despues. '
            + perfil
        )
        prompt4 = (
            'Eres ZIA nutricionista. INSTRUCCION ESTRICTA: Genera UNICAMENTE la LISTA DE LA COMPRA '
            'completa para los 7 dias (Lunes a Domingo). PROHIBIDO incluir menus o dias de la semana. '
            'El TOTAL ESTIMADO NO puede superar ' + presupuesto + ' euros. '
            'Si los productos superan el presupuesto reduce cantidades o elige alternativas mas baratas. '
            'Organiza por categorias con cantidades y precios para ' + super_nombre + '. '
            'Termina con TOTAL ESTIMADO (debe ser menor o igual a ' + presupuesto + ' euros). '
            'Sin texto antes ni despues. '
            + perfil
        )

        suffix3 = (
            '\n---\nAhora preparo tu lista de la compra para '
            + super_nombre
            + '. Dame un momento... 🛒'
        )
        suffix4 = (
            '\n---\n'
            '1️⃣ Confirmar compra en ' + super_nombre + '\n'
            '2️⃣ Comparar precios con otros supermercados'
        )

        def _call(prompt, max_tok):
            try:
                r = self.openai.chat.completions.create(
                    model=model,
                    messages=[
                        {'role': 'system', 'content': system},
                        {'role': 'user', 'content': prompt},
                    ],
                    max_tokens=max_tok,
                    temperature=0.7,
                    timeout=25,
                )
                return r.choices[0].message.content
            except Exception as e:
                return 'Error generando parte del plan: ' + str(e)[:60]

        lunes_mie = _call(prompt1, 650).rstrip()
        jue_sab = _call(prompt2, 650).rstrip()
        domingo = _call(prompt3, 450).rstrip() + suffix3
        lista = _call(prompt4, 1000).rstrip() + suffix4
        partes = [lunes_mie, jue_sab, domingo, lista]
        print('ZIA plan partes generadas:', len(partes))
        intro = 'Aqui tienes tu plan semanal de Lunes a Domingo'
        return [intro] + [lunes_mie, jue_sab, domingo, lista]

    def _gpt_libre(self, message, u):
        company = self.config['branding']['company_name']
        checkout = self.config.get('integrations',{}).get('cart',{}).get('checkout_url', self.config['branding']['website'])
        data = u['data']
        plan = u.get('plan','')[:200] if u.get('plan') else ''
        perfil_usuario = self._profile_for_prompt(data)
        if isinstance(message, dict):
            text = (message.get('text') or '').strip()
            image_url = message.get('image_url')
        else:
            text = (message or '').strip() if isinstance(message, str) else str(message).strip()
            image_url = None
        tl = text.lower()
        ml = tl
        if any(w in ml for w in ['gracias', 'thank', 'perfecto', 'genial', 'ok', 'vale', 'listo']):
            nombre = data.get('nombre', '')
            u['state'] = 'menu_principal'
            return (
                'De nada, ' + nombre + '! 😊\n\n'
                + self._menu_principal_text(data)
            )
        nevera_foto = (image_url is not None) or any(
            w in tl for w in ('nevera', 'frigo', 'tengo en casa', 'foto')
        )
        if nevera_foto:
            system_nevera = (
                COACH_TONE + ' Eres ZIA nutricionista. Analiza esta nevera/despensa y propón 3 recetas rápidas en menos de 20 minutos con lo que ves. '
                'Responde en español con emojis. Incluye ingredientes que faltan con precio orientativo en euros.'
            )
            system_nevera += (
                ' Perfil: ' + perfil_usuario + '.'
            )
            user_parts = [{'type': 'text', 'text': text or 'Analiza esta imagen de mi nevera o despensa.'}]
            if image_url is not None:
                user_parts.append({'type': 'image_url', 'image_url': {'url': image_url}})
            history = u.get('history', [])
            history.append({'role': 'user', 'content': text or ('[foto nevera]' if image_url else '[nevera/despensa]')})
            if len(history) > 6:
                history = history[-6:]
            try:
                r = self.openai.chat.completions.create(
                    model='gpt-4o',
                    messages=[
                        {'role': 'system', 'content': system_nevera},
                        {'role': 'user', 'content': user_parts},
                    ],
                    max_tokens=600,
                    temperature=0.7,
                    timeout=45,
                )
                reply = r.choices[0].message.content
                history.append({'role': 'assistant', 'content': reply})
                u['history'] = history
                return reply
            except Exception as e:
                return 'No pude responder por un error o timeout. Intentalo de nuevo en unos minutos. Detalle: ' + str(e)[:80]
        system = (COACH_TONE + ' Eres ZIA de ' + company + '. Perfil: ' + perfil_usuario
                  + '. Plan actual: ' + plan
                  + '. Carrito: ' + checkout
                  + '. Ayuda con modificaciones y preguntas. Usa emojis. MAXIMO 100 palabras. Espanol.')
        history = u.get('history', [])
        history.append({'role': 'user', 'content': text})
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
            return 'No pude responder por un error o timeout. Intentalo de nuevo en unos minutos. Detalle: ' + str(e)[:80]
    def _process_retail_asesor(self, user_id, message):
        u = self._get_user(user_id)
        history = u.get('history', [])
        if isinstance(message, str):
            reset = is_reset(message)
        elif isinstance(message, dict):
            t0 = message.get('text') or message.get('body') or message.get('caption') or ''
            reset = is_reset(t0.strip()) if isinstance(t0, str) else False
        else:
            reset = False
        if reset:
            had_history = len(u.get('history', [])) > 0
            u['history'] = []
            history = []
            if not had_history:
                return self.get_welcome_message()
        text, image_url = self._retail_text_and_image_url(message)
        if image_url:
            user_msg = {'role': 'user', 'content': [{'type': 'text', 'text': text or 'Analiza esta imagen.'}, {'type': 'image_url', 'image_url': {'url': image_url}}]}
        else:
            user_msg = {'role': 'user', 'content': text}
        ai = self.config.get('ai', {})
        model = ai.get('model', 'gpt-4o-mini')
        max_tokens = ai.get('max_tokens', 800)
        temperature = ai.get('temperature', 0.7)
        system_prompt = self.config.get('system_prompt', '')
        messages = [{'role': 'system', 'content': system_prompt}] + history + [user_msg]
        try:
            r = self.openai.chat.completions.create(model=model, messages=messages, max_tokens=max_tokens, temperature=temperature, timeout=60)
            reply = r.choices[0].message.content
            new_history = history + [user_msg, {'role': 'assistant', 'content': reply}]
            if len(new_history) > 10:
                new_history = new_history[-10:]
            u['history'] = new_history
            return reply
        except Exception as e:
            return 'Error: ' + str(e)[:80]
