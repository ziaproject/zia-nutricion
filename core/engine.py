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
            return ('En que supermercado sueles comprar? 🏪\n\n  1️⃣ Mercadona\n  2️⃣ Lidl\n  3️⃣ Aldi\n  4️⃣ Carrefour\n  5️⃣ Dia\n  6️⃣ Consum\n  7️⃣ Supercor\n  8️⃣ El Corte Ingles\n\n_O escribe el nombre directamente_')
        elif s == 'supermercado':
            u['data']['supermercado'] = m.strip() if m.strip() else 'Mercadona'
            u['state'] = 'plan_listo'
            msgs = self._generar_plan_partes(u['data'])
            u['plan'] = '\n\n'.join(msgs[1:])
            u['plan_count'] = u.get('plan_count', 0) + 1
            return msgs
        elif s == 'plan_listo':
            ml = m.lower()
            if ml in ['1', 'si', 'sí', 'confirmar', 'confirmo', 'dale', 'ok', 'vale', 'yes', 'claro']:
                u['state'] = 'confirmando_compra'
                super_nombre = u['data'].get('supermercado', 'Mercadona')
                return (
                    '¿Confirmas la compra en '
                    + super_nombre
                    + '? Responde si para obtener el link directo 🛒'
                )
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
            super_key_pl = m.strip().lower()
            if super_key_pl in SUPER_URLS:
                url = SUPER_URLS.get(super_key_pl, 'https://tienda.mercadona.es')
                return (
                    'Perfecto! Aqui tienes tu link directo para hacer la compra en '
                    + m.strip()
                    + ':\n\n'
                    + url
                    + '\n\nQue disfrutes de tu semana saludable! '
                )
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
            if m.strip() == '6' or any(k in ml for k in ('deporte', 'gym', 'musculo')):
                u['state'] = 'modo_deporte'
                return (
                    'Que deporte practicas y cuantos dias a la semana? Indica tambien tu objetivo: '
                    'ganar musculo, perder grasa o mejorar rendimiento 💪'
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
                sk = str(super_nombre).strip().lower().replace('í', 'i').replace('é', 'e')
                url = SUPER_URLS.get(sk, 'https://tienda.mercadona.es')
                msg1 = (
                    'Perfecto, '
                    + nombre
                    + '! Aqui tienes tu link para '
                    + super_nombre
                    + ':\n\n'
                    + url
                    + '\n\nQue disfrutes de tu semana saludable! 💪🥗'
                )
                msg2 = (
                    'Que quieres hacer ahora, ' + nombre + '?\n\n'
                    '1️⃣ 🍽️ Comer mejor hoy (recetas rapidas)\n'
                    '2️⃣ 🛒 Hacer la compra inteligente\n'
                    '3️⃣ 📸 Cocinar con lo que tengo (foto nevera)\n'
                    '4️⃣ 🧠 Mejorar mi alimentacion (habitos)\n'
                    '5️⃣ 🥗 Dieta especifica (keto, vegana...)\n'
                    '6️⃣ 🏋️ Nutricion deportiva'
                )
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
                                'content': 'Eres ZIA nutricionista. Responde en español con emojis.',
                            },
                            {'role': 'user', 'content': prompt_h},
                        ],
                        max_tokens=400,
                        temperature=0.7,
                        timeout=25,
                    )
                    u['state'] = 'menu_principal'
                    nombre = data.get('nombre', '')
                    menu = (
                        '\n\n---\nQue quieres hacer ahora, ' + nombre + '?\n\n'
                        '1️⃣ 🍽️ Comer mejor hoy (recetas rapidas)\n'
                        '2️⃣ 🛒 Hacer la compra inteligente\n'
                        '3️⃣ 📸 Cocinar con lo que tengo (foto nevera)\n'
                        '4️⃣ 🧠 Mejorar mi alimentacion (habitos)\n'
                        '5️⃣ 🥗 Dieta especifica (keto, vegana...)\n'
                        '6️⃣ 🏋️ Nutricion deportiva'
                    )
                    return r.choices[0].message.content + menu
                except Exception:
                    return 'No pude generar los consejos. Intenta de nuevo.'
            if m.strip() == '5' or 'dieta' in ml or 'keto' in ml or 'vegana' in ml:
                u['state'] = 'eligiendo_dieta'
                return (
                    'Que tipo de dieta quieres?\n\n1️⃣ Keto\n2️⃣ Vegana\n3️⃣ Mediterranea\n'
                    '4️⃣ Ayuno 16:8\n5️⃣ Vegetariana'
                )
            if m.strip() == '6' or 'deporte' in ml or 'gym' in ml:
                u['state'] = 'modo_deporte'
                return (
                    'Que deporte practicas y cuantos dias a la semana? Indica tambien tu objetivo: '
                    'ganar musculo, perder grasa o mejorar rendimiento 💪'
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
            super_key = m.strip().lower()
            url = SUPER_URLS.get(super_key, None)
            if url:
                u['data']['supermercado'] = m.strip()
                u['state'] = 'menu_principal'
                msg1 = (
                    'Perfecto, '
                    + nombre
                    + '! Aqui tienes tu link para '
                    + m.strip()
                    + ':\n\n'
                    + url
                    + '\n\nQue disfrutes de tu semana saludable! 💪🥗'
                )
                msg2 = (
                    'Que quieres hacer ahora, ' + nombre + '?\n\n'
                    '1️⃣ 🍽️ Comer mejor hoy (recetas rapidas)\n'
                    '2️⃣ 🛒 Hacer la compra inteligente\n'
                    '3️⃣ 📸 Cocinar con lo que tengo (foto nevera)\n'
                    '4️⃣ 🧠 Mejorar mi alimentacion (habitos)\n'
                    '5️⃣ 🥗 Dieta especifica (keto, vegana...)\n'
                    '6️⃣ 🏋️ Nutricion deportiva'
                )
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
                    nombre = data.get('nombre', '')
                    menu = (
                        '\n\n---\nQue quieres hacer ahora, ' + nombre + '?\n\n'
                        '1️⃣ 🍽️ Comer mejor hoy (recetas rapidas)\n'
                        '2️⃣ 🛒 Hacer la compra inteligente\n'
                        '3️⃣ 📸 Cocinar con lo que tengo (foto nevera)\n'
                        '4️⃣ 🧠 Mejorar mi alimentacion (habitos)\n'
                        '5️⃣ 🥗 Dieta especifica (keto, vegana...)\n'
                        '6️⃣ 🏋️ Nutricion deportiva'
                    )
                    return r.choices[0].message.content + menu
                except Exception as e:
                    return 'No pude analizar la foto: ' + str(e)[:80]
            else:
                return 'No he recibido la foto. Enviamela directamente como imagen 📸'
        elif s == 'recetas_rapidas':
            data = u['data']
            nombre = data.get('nombre', '')
            company = self.config['branding']['company_name']
            prompt = (
                'Eres ZIA nutricionista de '
                + company
                + '. '
                'El usuario dice: '
                + m.strip()
                + '. '
                'Propón 3 recetas rapidas en menos de 20 minutos adaptadas a su perfil. '
                'Perfil: '
                + nombre
                + ', objetivo: '
                + data.get('objetivo', '')
                + ', '
                'restricciones: '
                + data.get('restricciones', 'Ninguna')
                + '. '
                'Usa emojis. Maximo 300 palabras. Responde en espanol.'
            )
            menu = (
                '\n\n---\nQue quieres hacer ahora, ' + nombre + '?\n\n'
                '1️⃣ 🍽️ Comer mejor hoy (recetas rapidas)\n'
                '2️⃣ 🛒 Hacer la compra inteligente\n'
                '3️⃣ 📸 Cocinar con lo que tengo (foto nevera)\n'
                '4️⃣ 🧠 Mejorar mi alimentacion (habitos)\n'
                '5️⃣ 🥗 Dieta especifica (keto, vegana...)\n'
                '6️⃣ 🏋️ Nutricion deportiva'
            )
            u['state'] = 'menu_principal'
            try:
                r = self.openai.chat.completions.create(
                    model=self.config.get('ai', {}).get('model', 'gpt-4o-mini'),
                    messages=[
                        {
                            'role': 'system',
                            'content': 'Eres ZIA nutricionista. Responde en espanol con emojis.',
                        },
                        {'role': 'user', 'content': prompt},
                    ],
                    max_tokens=600,
                    temperature=0.7,
                    timeout=25,
                )
                return r.choices[0].message.content + menu
            except Exception as e:
                return 'Error: ' + str(e)[:50]
        elif s == 'eligiendo_dieta':
            dietas = {'1': 'keto', '2': 'vegana', '3': 'mediterranea', '4': 'ayuno 16:8', '5': 'vegetariana'}
            dieta = dietas.get(m.strip(), m.strip().lower())
            u['data']['dieta_especial'] = dieta
            data = u['data']
            company = self.config['branding']['company_name']
            prompt = (
                'Eres ZIA nutricionista de '
                + company
                + '. Genera un plan semanal de dieta '
                + dieta
                + ' completo de Lunes a Domingo con Desayuno, Comida y Cena. Perfil: '
                + data.get('nombre', '')
                + ', objetivo: '
                + data.get('objetivo', '')
                + ', restricciones: '
                + data.get('restricciones', 'Ninguna')
                + '. Usa emojis. Maximo 400 palabras.'
            )
            try:
                r = self.openai.chat.completions.create(
                    model=self.config.get('ai', {}).get('model', 'gpt-4o-mini'),
                    messages=[
                        {
                            'role': 'system',
                            'content': 'Eres ZIA nutricionista. Responde en español con emojis.',
                        },
                        {'role': 'user', 'content': prompt},
                    ],
                    max_tokens=800,
                    temperature=0.7,
                    timeout=30,
                )
                u['state'] = 'menu_principal'
                nombre = data.get('nombre', '')
                menu = (
                    '\n\n---\nQue quieres hacer ahora, ' + nombre + '?\n\n'
                    '1️⃣ 🍽️ Comer mejor hoy (recetas rapidas)\n'
                    '2️⃣ 🛒 Hacer la compra inteligente\n'
                    '3️⃣ 📸 Cocinar con lo que tengo (foto nevera)\n'
                    '4️⃣ 🧠 Mejorar mi alimentacion (habitos)\n'
                    '5️⃣ 🥗 Dieta especifica (keto, vegana...)\n'
                    '6️⃣ 🏋️ Nutricion deportiva'
                )
                return r.choices[0].message.content + menu
            except Exception as e:
                return 'Error generando plan de dieta: ' + str(e)[:50]
        elif s == 'modo_deporte':
            u['data']['info_deporte'] = m.strip()
            data = u['data']
            company = self.config['branding']['company_name']
            prompt = (
                'Eres ZIA nutricionista deportiva de '
                + company
                + '. El usuario practica: '
                + m.strip()
                + '. Genera un plan de nutricion deportiva semanal con comidas pre y post entreno, '
                'macros diarios (proteinas, carbohidratos, grasas, calorias). Perfil: '
                + data.get('nombre', '')
                + ', peso: '
                + data.get('peso', '70')
                + 'kg, objetivo: '
                + data.get('objetivo', '')
                + ', restricciones: '
                + data.get('restricciones', 'Ninguna')
                + '. Usa emojis. Maximo 400 palabras.'
            )
            try:
                r = self.openai.chat.completions.create(
                    model=self.config.get('ai', {}).get('model', 'gpt-4o-mini'),
                    messages=[
                        {
                            'role': 'system',
                            'content': 'Eres ZIA nutricionista deportiva. Responde en español con emojis.',
                        },
                        {'role': 'user', 'content': prompt},
                    ],
                    max_tokens=800,
                    temperature=0.7,
                    timeout=30,
                )
                u['state'] = 'menu_principal'
                nombre = data.get('nombre', '')
                menu = (
                    '\n\n---\nQue quieres hacer ahora, ' + nombre + '?\n\n'
                    '1️⃣ 🍽️ Comer mejor hoy (recetas rapidas)\n'
                    '2️⃣ 🛒 Hacer la compra inteligente\n'
                    '3️⃣ 📸 Cocinar con lo que tengo (foto nevera)\n'
                    '4️⃣ 🧠 Mejorar mi alimentacion (habitos)\n'
                    '5️⃣ 🥗 Dieta especifica (keto, vegana...)\n'
                    '6️⃣ 🏋️ Nutricion deportiva'
                )
                return r.choices[0].message.content + menu
            except Exception as e:
                return 'Error generando plan deportivo: ' + str(e)[:50]
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
        company = self.config['branding']['company_name']
        cal = calorias(data)
        personas = data.get('personas', '1 persona')
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

        perfil = (
            'PERFIL: ' + data.get('nombre', '') + ', ' + data.get('genero', '') + ', '
            + data.get('edad', '') + ' anos, ' + data.get('peso', '') + 'kg, '
            + data.get('altura', '') + 'cm, ' + str(cal) + ' kcal/dia\n'
            'Plan para: ' + personas + '\n'
            'Objetivo: ' + data.get('objetivo', '') + '\n'
            'Restricciones: ' + data.get('restricciones', 'Ninguna') + '\n'
            'Presupuesto: ' + data.get('presupuesto', '') + ' euros/semana\n\n'
            + catalogo
        )

        model = self.config.get('ai', {}).get('model', 'gpt-4o-mini')
        system = 'Eres ZIA nutricionista de ' + company + '. Responde en espanol con emojis.'

        prompt1 = (
            'Eres ZIA nutricionista. INSTRUCCION ESTRICTA: Genera UNICAMENTE Lunes, Martes y Miercoles. '
            'PROHIBIDO incluir Jueves, Viernes, Sabado o Domingo. '
            'Empieza con *Lunes:* Cada dia: Desayuno, Comida y Cena. '
            'Termina exactamente en la Cena del Miercoles. Sin texto despues. '
            + perfil
        )
        prompt2 = (
            'Eres ZIA nutricionista. INSTRUCCION ESTRICTA: Genera UNICAMENTE Jueves, Viernes y Sabado. '
            'PROHIBIDO incluir Lunes, Martes, Miercoles o Domingo. '
            'Empieza directamente con *Jueves:* Cada dia: Desayuno, Comida y Cena. '
            'Termina exactamente en la Cena del Sabado. Sin texto antes ni despues. '
            + perfil
        )
        prompt3 = (
            'Eres ZIA nutricionista. INSTRUCCION ESTRICTA: Genera UNICAMENTE el Domingo. '
            'PROHIBIDO incluir cualquier otro dia de la semana. '
            'PROHIBIDO incluir lista de la compra o precios. '
            'Empieza directamente con *Domingo:* con Desayuno, Comida y Cena. '
            'Termina exactamente en la Cena del Domingo. Sin texto antes ni despues. '
            + perfil
        )
        prompt4 = (
            'Eres ZIA nutricionista. INSTRUCCION ESTRICTA: Genera UNICAMENTE la LISTA DE LA COMPRA '
            'para los 7 dias (Lunes a Domingo). PROHIBIDO incluir menus o dias de la semana. '
            'Organiza por categorias con cantidades y precios para ' + super_nombre + '. '
            'Termina con TOTAL ESTIMADO. Sin texto antes ni despues. '
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

        partes = []
        for prompt, max_tok, suffix in [
            (prompt1, 650, ''),
            (prompt2, 650, ''),
            (prompt3, 450, suffix3),
            (prompt4, 1000, suffix4),
        ]:
            cuerpo = _call(prompt, max_tok).rstrip()
            partes.append(cuerpo + suffix)
        intro = 'Aqui tienes tu plan semanal de Lunes a Domingo'
        return [intro] + partes

    def _gpt_libre(self, message, u):
        company = self.config['branding']['company_name']
        checkout = self.config.get('integrations',{}).get('cart',{}).get('checkout_url', self.config['branding']['website'])
        data = u['data']
        plan = u.get('plan','')[:200] if u.get('plan') else ''
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
                'Que quieres hacer ahora?\n\n'
                '1️⃣ 🍽️ Comer mejor hoy (recetas rapidas)\n'
                '2️⃣ 🛒 Hacer la compra inteligente\n'
                '3️⃣ 📸 Cocinar con lo que tengo (foto nevera)\n'
                '4️⃣ 🧠 Mejorar mi alimentacion (habitos)\n'
                '5️⃣ 🥗 Dieta especifica (keto, vegana...)\n'
                '6️⃣ 🏋️ Nutricion deportiva'
            )
        nevera_foto = (image_url is not None) or any(
            w in tl for w in ('nevera', 'frigo', 'tengo en casa', 'foto')
        )
        if nevera_foto:
            system_nevera = (
                'Eres ZIA nutricionista. Analiza esta nevera/despensa y propón 3 recetas rápidas en menos de 20 minutos con lo que ves. '
                'Responde en español con emojis. Incluye ingredientes que faltan con precio orientativo en euros.'
            )
            system_nevera += (
                ' Perfil: ' + data.get('nombre', '')
                + ', objetivo ' + data.get('objetivo', '')
                + ', restricciones ' + data.get('restricciones', 'Ninguna') + '.'
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
                return 'Error: ' + str(e)[:50]
        system = ('Eres ZIA de ' + company + '. Perfil: ' + data.get('nombre','')
                  + ', objetivo: ' + data.get('objetivo','')
                  + ', restricciones: ' + data.get('restricciones','Ninguna')
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
            return 'Error: ' + str(e)[:50]
