
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

# ── Detectores de intención ──────────────────────────────────────────────────

def detectar_foto_nevera(text, image_url):
    """True si el mensaje incluye una imagen o menciona foto de nevera."""
    if image_url:
        return True
    t = text.lower()
    return ('foto' in t or 'analiza' in t) and any(k in t for k in ['nevera','frigo','refrigerador'])

def detectar_comparar_supers(text):
    t = text.lower()
    return any(x in t for x in ['comparar','compara','mas barato','más barato','otras tiendas','otros supermercados','precio'])

def detectar_dieta_especial(text):
    t = text.lower()
    if 'keto' in t or 'cetogen' in t: return 'keto'
    if 'mediterr' in t: return 'mediterranea'
    if 'ayuno' in t: return 'ayuno_16_8'
    if 'vegana' in t or 'vegano' in t: return 'vegana'
    if 'vegetarian' in t: return 'vegetariana'
    if 'definicion' in t or 'cutting' in t: return 'definicion'
    if 'volumen' in t or 'bulking' in t: return 'volumen'
    return None

DIETAS = {
    'keto':        'cetogénica: muy baja en carbohidratos (<50g/día), alta en grasas, proteína moderada.',
    'mediterranea':'mediterránea: aceite de oliva, verduras, legumbres, pescado, cereales integrales.',
    'ayuno_16_8':  'ayuno intermitente 16:8: ventana de 8h para comer, ayuno de 16h.',
    'vegana':      'vegana: sin ningún producto animal. Proteínas de legumbres, tofu, tempeh, frutos secos.',
    'vegetariana': 'vegetariana: sin carne ni pescado, sí huevos y lácteos.',
    'definicion':  'de definición: déficit calórico controlado, proteína alta para preservar músculo.',
    'volumen':     'de volumen: superávit calórico moderado, proteína alta, carbohidratos suficientes.',
}

SUPERMERCADOS = [
    ('Mercadona', 1.00),
    ('Lidl',      0.88),
    ('Aldi',      0.85),
    ('Carrefour', 1.05),
    ('Dia',       1.00),
    ('Alcampo',   0.98),
    ('Eroski',    1.02),
]


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

    def _system_prompt_or(self, internal_prompt):
        if 'system_prompt' in self.config:
            return self.config['system_prompt']
        return internal_prompt

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
                u = str(v['url']).strip()
                if u:
                    url = u
                    break
        if not url:
            im = message.get('image')
            if isinstance(im, str) and (
                im.startswith('http://') or im.startswith('https://') or im.startswith('data:')
            ):
                url = im.strip()
            elif isinstance(im, dict) and im.get('url'):
                u = str(im['url']).strip()
                if u:
                    url = u
        return text, url

    # ── Nuevas funcionalidades ───────────────────────────────────────────────

    def _analizar_foto_nevera(self, image_url, text, data):
        """Analiza foto de nevera con GPT-4o vision y propone 3 recetas rápidas."""
        company = self.config['branding']['company_name']
        nombre = data.get('nombre', '')
        restricciones = data.get('restricciones', 'Ninguna')
        personas = data.get('personas', '1 persona')

        system = (
            'Eres ZIA, nutricionista de ' + company + '. '
            'Analizas imágenes de neveras y propones recetas rápidas con lo que hay. '
            'Responde siempre en español con emojis. Nunca digas que no puedes ver la imagen.'
        )
        prompt = (
            'Analiza esta nevera/despensa.\n\n'
            'Perfil: ' + (nombre or 'usuario') + ', ' + personas + ', restricciones: ' + restricciones + '.\n\n'
            'Responde en este orden:\n'
            '1) PRODUCTOS VISIBLES: lista lo que reconoces (viñetas con •)\n'
            '2) 3 RECETAS RÁPIDAS (<20 min) usando esos ingredientes, con cantidades\n'
            '3) CESTA COMPLEMENTARIA: 2-3 productos baratos que faltan, con precio orientativo en € y TOTAL ESTIMADO\n\n'
            'Sin suplementos. Máximo 300 palabras. Usa emojis.'
        )
        try:
            r = self.openai.chat.completions.create(
                model='gpt-4o',  # vision requiere gpt-4o
                messages=[
                    {'role': 'system', 'content': system},
                    {'role': 'user', 'content': [
                        {'type': 'text', 'text': prompt},
                        {'type': 'image_url', 'image_url': {'url': image_url}},
                    ]},
                ],
                max_tokens=700,
                temperature=0.65,
                timeout=45,
            )
            return r.choices[0].message.content
        except Exception as e:
            return 'No pude analizar la foto ahora mismo 😔 Inténtalo de nuevo en un momento.\n\nError: ' + str(e)[:60]

    def _comparar_supermercados(self, plan, presupuesto):
        """Genera comparativa de totales estimados por supermercado."""
        try:
            base = float(presupuesto or 65)
        except Exception:
            base = 65.0

        lineas = ['🛒 *COMPARATIVA DE SUPERMERCADOS*\n']
        min_total = None
        min_nombre = ''
        resultados = []

        for nombre, factor in SUPERMERCADOS:
            total = round(base * factor, 2)
            resultados.append((nombre, total))
            if min_total is None or total < min_total:
                min_total = total
                min_nombre = nombre

        for nombre, total in resultados:
            estrella = ' ⭐ MÁS BARATO' if nombre == min_nombre else ''
            lineas.append(f'🏪 {nombre} → {total:.2f}€{estrella}')

        lineas.append('\n_Precios orientativos basados en tu presupuesto semanal._')
        lineas.append('\n¿En qué supermercado quieres hacer la compra? Dime el nombre y te preparo la lista. 🛍️')
        return '\n'.join(lineas)

    def _plan_dieta_especial(self, modo, data):
        """Genera plan semanal para dieta especial (keto, mediterránea, etc.)."""
        company = self.config['branding']['company_name']
        nombre = data.get('nombre', '')
        nombre_str = ', ' + nombre if nombre else ''
        descripcion = DIETAS.get(modo, modo)
        cal = calorias(data)
        personas = data.get('personas', '1 persona')
        presupuesto = data.get('presupuesto', '65')
        restricciones = data.get('restricciones', 'Ninguna')

        system = self._system_prompt_or(
            'Eres ZIA nutricionista de ' + company + '. Responde en español con emojis.'
        )
        prompt = (
            'Genera un plan semanal completo de dieta ' + descripcion + '\n\n'
            'Perfil: ' + (nombre or 'usuario') + ', ' + data.get('genero','') + ', '
            + data.get('edad','') + ' años, ' + data.get('peso','') + 'kg, '
            + data.get('altura','') + 'cm, ' + str(cal) + ' kcal/día. '
            'Para: ' + personas + '. Objetivo: ' + data.get('objetivo','comer sano') + '. '
            'Restricciones: ' + restricciones + '. Presupuesto: ' + presupuesto + '€.\n\n'
            'Incluye:\n'
            '1) Breve explicación de esta dieta (3-4 frases)\n'
            '2) Plan Lunes a Domingo: Desayuno, Comida y Cena con cantidades en gramos\n'
            '3) Lista de la compra organizada por categorías con precios y TOTAL ESTIMADO\n\n'
            'Máximo 500 palabras. Usa emojis. Sin suplementos.'
        )
        try:
            r = self.openai.chat.completions.create(
                model=self.config.get('ai', {}).get('model', 'gpt-4o-mini'),
                messages=[
                    {'role': 'system', 'content': system},
                    {'role': 'user', 'content': prompt},
                ],
                max_tokens=900,
                temperature=0.65,
                timeout=45,
            )
            reply = r.choices[0].message.content
            reply += (
                '\n\n---\n¿Qué quieres hacer' + nombre_str + '?\n\n'
                '  1️⃣ Comparar precios entre supermercados\n'
                '  2️⃣ Cambiar algo del menú\n'
                '  3️⃣ Guardar lista\n\n'
                '_O escríbeme cualquier duda_ 💬'
            )
            return reply
        except Exception as e:
            return 'No pude generar el plan ahora mismo 😔\n\nError: ' + str(e)[:60]

    # ── Flujo principal ──────────────────────────────────────────────────────

    def process_message(self, user_id, message, plan_type='pro'):
        meta = self.config.get('_meta')
        if isinstance(meta, dict) and meta.get('type') == 'retail-asesor':
            return self._process_retail_asesor(user_id, message)

        u = self._get_user(user_id)
        text, image_url = self._retail_text_and_image_url(message)
        m = text.strip() if text else ''
        company = self.config['branding']['company_name']
        checkout = self.config.get('integrations', {}).get('cart', {}).get('checkout_url', self.config['branding']['website'])
        nombre = u['data'].get('nombre', '')
        nombre_str = ', ' + nombre if nombre else ''

        if is_reset(m):
            self.reset_user(user_id)
            u = self._get_user(user_id)

        s = u['state']

        # ── Foto nevera: funciona en cualquier estado tras onboarding ────────
        if image_url and s not in ('welcome', 'datos', 'personas', 'objetivo', 'cocina', 'restricciones', 'presupuesto'):
            respuesta = self._analizar_foto_nevera(image_url, m, u['data'])
            respuesta += (
                '\n\n---\n¿Qué quieres hacer' + nombre_str + '?\n\n'
                '  1️⃣ Comparar precios entre supermercados\n'
                '  2️⃣ Cambiar algo del menú\n'
                '  3️⃣ Guardar lista\n\n'
                '_O escríbeme cualquier duda_ 💬'
            )
            return respuesta

        # ── Foto enviada durante onboarding: pedir que termine primero ───────
        if image_url and s in ('welcome', 'datos', 'personas', 'objetivo', 'cocina', 'restricciones', 'presupuesto'):
            return '📸 ¡Foto recibida! Termina tu perfil primero y luego analizamos tu nevera juntos 🥗'

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

            # ── Comparar supermercados ────────────────────────────────────────
            if ml in ['1','carrito','anadir al carrito','añadir al carrito']:
                return ('🛒 Aqui tienes tu carrito' + nombre_str + '!\n\n'
                        + checkout + '\n\n'
                        '_Pulsa el link para anadir todo directamente_ ✅')

            elif detectar_comparar_supers(ml) or ml == '5':
                presupuesto = u['data'].get('presupuesto', '65')
                return self._comparar_supermercados(u.get('plan', ''), presupuesto)

            elif ml in ['2','cambiar','cambiar algo']:
                u['state'] = 'cambiar'
                return '✏️ Que quieres cambiar' + nombre_str + '? Dime el dia o el plato y te propongo una alternativa directamente 🍽️'

            elif ml in ['3','guardar','guardar lista']:
                partes = u.get('plan','').split('\n\n')
                lista_limpia = partes[-1] if partes else u.get('plan','')
                return '💾 Aqui tienes tu lista de la compra' + nombre_str + ':\n\n' + lista_limpia

            # ── Dieta especial ────────────────────────────────────────────────
            else:
                modo_dieta = detectar_dieta_especial(ml)
                if modo_dieta:
                    return self._plan_dieta_especial(modo_dieta, u['data'])
                return self._gpt_libre(m, u)

        elif s == 'cambiar':
            u['state'] = 'plan_listo'
            return self._cambiar_plato(m, u)

        else:
            u['state'] = 'welcome'
            return 'Escribe *Hola* para empezar 👋'

    def _process_retail_asesor(self, user_id, message):
        u = self._get_user(user_id)
        history = u.get('history', [])

        if isinstance(message, str):
            reset = is_reset(message)
        elif isinstance(message, dict):
            t0 = (
                message.get('text')
                or message.get('body')
                or message.get('caption')
                or ''
            )
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
            user_msg = {
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': text or 'Analiza esta imagen.'},
                    {'type': 'image_url', 'image_url': {'url': image_url}},
                ],
            }
        else:
            user_msg = {'role': 'user', 'content': text}

        ai = self.config.get('ai', {})
        model = ai.get('model', 'gpt-4o-mini')
        max_tokens = ai.get('max_tokens', 800)
        temperature = ai.get('temperature', 0.7)
        system_prompt = self._system_prompt_or('')

        messages = [{'role': 'system', 'content': system_prompt}] + history + [user_msg]
        try:
            r = self.openai.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=60,
            )
            reply = r.choices[0].message.content
            new_history = history + [user_msg, {'role': 'assistant', 'content': reply}]
            if len(new_history) > 10:
                new_history = new_history[-10:]
            u['history'] = new_history
            return reply
        except Exception as e:
            return 'Error: ' + str(e)[:80]

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
                  'Presupuesto semanal: ' + presupuesto + ' euros.')

        prompt1 = ('Eres ZIA nutricionista de ' + company + '. ' + perfil + '\n\n' + catalogo + '\n'
                   'INSTRUCCION ESTRICTA: Genera UNICAMENTE el menu de LUNES, MARTES y MIERCOLES. '
                   'La primera palabra de tu respuesta debe ser *Lunes:* '
                   'Cada dia: Desayuno, Comida y Cena con cantidades en gramos. '
                   'SIN tiempos de preparacion. SIN saludos. SIN frases al final. '
                   'Termina exactamente en la Cena del Miercoles. Usa emojis. Maximo 220 palabras.')

        prompt2 = ('Eres ZIA nutricionista de ' + company + '. ' + perfil + '\n\n' + catalogo + '\n'
                   'INSTRUCCION ESTRICTA: Genera UNICAMENTE el menu de JUEVES, VIERNES, SABADO y DOMINGO. '
                   'La primera palabra de tu respuesta debe ser *Jueves:* '
                   'Cada dia: Desayuno, Comida y Cena con cantidades en gramos. '
                   'SIN tiempos de preparacion. SIN saludos. SIN frases al final. '
                   'Termina exactamente en la Cena del Domingo. Usa emojis. Maximo 220 palabras.')

        prompt3 = ('Eres ZIA nutricionista de ' + company + '. ' + perfil + '\n\n' + catalogo + '\n'
                   'INSTRUCCION ESTRICTA: Genera la LISTA DE LA COMPRA COMPLETA con TODOS los ingredientes '
                   'necesarios para el menu de Lunes a Domingo. '
                   'NO omitas ningun ingrediente del menu. '
                   'El total DEBE estar entre ' + str(int(presupuesto)-15) + ' y ' + presupuesto + ' euros. '
                   'Organiza por categorias (Verduras, Proteinas, Lacteos, Cereales, Otros). '
                   'Cada producto con cantidad y precio. Total al final. '
                   'Luego 2 productos ESTRELLA de ' + company + ' recomendados con motivo. '
                   'SIN links ni texto de carrito. SIN frases al final. '
                   'Usa emojis. Maximo 260 palabras.')

        model = self.config.get('ai',{}).get('model','gpt-4o-mini')
        system = self._system_prompt_or(
            'Eres ZIA nutricionista de ' + company + '. Responde SOLO en espanol con emojis. Sigue las instrucciones al pie de la letra.')
        partes = []
        for prompt in [prompt1, prompt2, prompt3]:
            try:
                r = self.openai.chat.completions.create(
                    model=model,
                    messages=[{'role':'system','content':system},{'role':'user','content':prompt}],
                    max_tokens=500, temperature=0.5, timeout=25)
                partes.append(r.choices[0].message.content)
            except Exception as e:
                partes.append('Error: ' + str(e)[:60])

        nombre = data.get('nombre','')
        nombre_str = ', ' + nombre if nombre else ''
        partes[-1] += ('\n\n---\n¿Que quieres hacer' + nombre_str + '?\n\n'
                       '  1️⃣ Anadir al carrito\n'
                       '  2️⃣ Cambiar algo del menu\n'
                       '  3️⃣ Guardar lista\n'
                       '  4️⃣ Comparar precios entre supermercados\n'
                       '  5️⃣ Pedir dieta especial (keto, mediterránea, ayuno...)\n\n'
                       '_O manda una foto de tu nevera para recetas con lo que tienes_ 📸')
        return partes

    def _cambiar_plato(self, message, u):
        company = self.config['branding']['company_name']
        data = u['data']
        plan = u.get('plan','')[:800] if u.get('plan') else ''
        nombre = data.get('nombre','')
        nombre_str = ', ' + nombre if nombre else ''
        system = self._system_prompt_or(
            'Eres ZIA nutricionista de ' + company + '. '
            'El usuario quiere cambiar un plato de su menu. '
            'Plan actual: ' + plan + '. '
            'INSTRUCCION: Da DIRECTAMENTE la alternativa para lo que pide. '
            'Formato: "Para la [comida] del [dia] te propongo: [alternativa con gramos]" '
            'Luego pregunta: "¿Te parece bien o prefieres otra opcion?" '
            'Usa emojis. Maximo 100 palabras. Espanol.')
        try:
            r = self.openai.chat.completions.create(
                model=self.config.get('ai',{}).get('model','gpt-4o-mini'),
                messages=[{'role':'system','content':system},{'role':'user','content':message}],
                max_tokens=200, temperature=0.7, timeout=25)
            reply = r.choices[0].message.content
            reply += ('\n\n---\n¿Que quieres hacer' + nombre_str + '?\n\n'
                      '  1️⃣ Anadir al carrito\n'
                      '  2️⃣ Cambiar otro plato\n'
                      '  3️⃣ Guardar lista\n'
                      '  4️⃣ Comparar precios entre supermercados\n\n'
                      '_O manda una foto de tu nevera_ 📸')
            return reply
        except Exception as e:
            return 'Error: ' + str(e)[:50]

    def _gpt_libre(self, message, u):
        company = self.config['branding']['company_name']
        checkout = self.config.get('integrations',{}).get('cart',{}).get('checkout_url', self.config['branding']['website'])
        data = u['data']
        plan = u.get('plan','')[:600] if u.get('plan') else ''
        nombre = data.get('nombre','')
        nombre_str = ', ' + nombre if nombre else ''

        # Detectar dieta especial en chat libre
        modo_dieta = detectar_dieta_especial(message.lower())
        if modo_dieta:
            return self._plan_dieta_especial(modo_dieta, data)

        # Detectar comparar en chat libre
        if detectar_comparar_supers(message.lower()):
            presupuesto = data.get('presupuesto', '65')
            return self._comparar_supermercados(plan, presupuesto)

        system = self._system_prompt_or(
            'Eres ZIA de ' + company + '. '
            'Perfil: ' + data.get('nombre','') + ', objetivo: ' + data.get('objetivo','')
            + ', restricciones: ' + data.get('restricciones','Ninguna') + '. '
            'Plan: ' + plan + '. Carrito: ' + checkout + '. '
            'Responde de forma util y concisa. '
            'Al terminar siempre ofrece las opciones:\n'
            '1️⃣ Anadir al carrito\n2️⃣ Cambiar algo\n3️⃣ Guardar lista\n4️⃣ Comparar precios\n'
            '_O manda una foto de tu nevera_ 📸\n'
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
