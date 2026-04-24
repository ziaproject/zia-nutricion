"""
Motor Naturvitia: onboarding completo, Harris-Benedict, plan semanal con macros,
crudo vs cocinado, pregunta por entreno antes de sugerir comida, vision GPT-4o.
"""

import base64
import json
import os
import re
import time

import requests
from openai import OpenAI

_NATURVITIA_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..',
    'clients',
    'naturvitia',
    'config.json',
)

_cache_nv = {}


def load_naturvitia_config():
    with open(_NATURVITIA_CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_naturvitia_engine():
    if 'singleton' not in _cache_nv:
        _cache_nv['singleton'] = ZiaNaturvitiaEngine()
    return _cache_nv['singleton']


def pause_between_plan_whatsapp_parts():
    """Pausa entre partes del plan (generación y envío vía Twilio) para orden en WhatsApp."""
    time.sleep(1)


RESET_WORDS = [
    'hola',
    'inicio',
    'reset',
    'empezar',
    'reiniciar',
    'start',
    'menu',
    'nuevo',
]


def is_reset(m):
    return m.strip().lower() in RESET_WORDS


def _activity_factor(text):
    raw = (text or '').strip()
    if len(raw) == 1 and raw in '12345':
        return {'1': 1.2, '2': 1.375, '3': 1.55, '4': 1.725, '5': 1.9}[raw]
    ml = raw.lower()
    if any(w in ml for w in ('muy activo', 'atleta', 'doble', '2 sesiones')):
        return 1.9
    if any(w in ml for w in ('activo', 'intenso', '6 días', '6 dias')):
        return 1.725
    if any(w in ml for w in ('moderado', '3-5', '3 a 5', 'gimnasio')):
        return 1.55
    if any(w in ml for w in ('ligero', 'paseo', '1-3', 'suave')):
        return 1.375
    if any(w in ml for w in ('sedentario', 'oficina', 'poco')):
        return 1.2
    return 1.375


def harris_benedict_revised_bmr(sexo, peso_kg, altura_cm, edad):
    """Harris-Benedict revisada (1984), peso kg, altura cm, edad años."""
    w = float(peso_kg)
    h = float(altura_cm)
    a = float(edad)
    s = (sexo or '').strip().lower()
    if s.startswith('h'):
        return 88.362 + (13.397 * w) + (4.799 * h) - (5.677 * a)
    return 447.593 + (9.247 * w) + (3.098 * h) - (4.330 * a)


def apply_goal_to_tdee(tdee, objetivo):
    o = (objetivo or '').lower()
    if any(x in o for x in ('perder', 'grasa', 'adelgazar', 'deficit', 'bajar')):
        return int(round(tdee * 0.85))
    if any(x in o for x in ('ganar', 'músculo', 'musculo', 'hipertrofia', 'volumen', 'subir')):
        return int(round(tdee * 1.1))
    return int(round(tdee))


def macros_grams(kcal, objetivo):
    o = (objetivo or '').lower()
    if any(x in o for x in ('perder', 'grasa', 'adelgazar', 'deficit')):
        rp, rc, rf = 0.32, 0.38, 0.30
    elif any(x in o for x in ('ganar', 'músculo', 'musculo', 'hipertrofia')):
        rp, rc, rf = 0.28, 0.42, 0.30
    else:
        rp, rc, rf = 0.30, 0.40, 0.30
    p = round(kcal * rp / 4)
    c = round(kcal * rc / 4)
    f = round(kcal * rf / 9)
    return p, c, f


def wants_food_advice(ml):
    if not ml or len(ml) < 4:
        return False
    patterns = (
        r'\bqu[eé]\s+com',
        r'\bqu[eé]\s+cojo',
        r'\bqu[eé]\s+puedo\s+com',
        r'\bqu[eé]\s+debo\s+com',
        r'\bqu[eé]\s+ceno',
        r'\bqu[eé]\s+desayun',
        r'\bqu[eé]\s+merend',
        r'\bideas?\s+para\s+com',
        r'\bqu[eé]\s+hago\s+de\s+com',
        r'\btengo\s+hambre',
        r'\brecomi[eé]nda',
        r'\bsugerencia',
        r'\bmen[uú]\s+de',
        r'\bcomida\s+para',
    )
    return any(re.search(p, ml) for p in patterns)


def parse_yes_no(ml):
    if re.search(r'\b(si|sí|yes|yep|entren[eé]|he\s+ido|gym|sí\s+he)\b', ml):
        return True
    if re.search(r'\b(no|nop|nada|descanso|hoy\s+no|no\s+he)\b', ml):
        return False
    return None


def parse_comidas_dia_value(m):
    """Normaliza la respuesta de comidas/día a '2'..'5' (texto o número); por defecto '5'. Máximo 5 comidas."""
    ms = (m or '').strip().lower()
    if not ms:
        return '5'
    for w, val in (
        ('cinco', '5'),
        ('cuatro', '4'),
        ('tres', '3'),
        ('dos', '2'),
    ):
        if re.search(r'\b' + w + r'\b', ms):
            return val
    if re.search(r'\bseis\b', ms):
        return '5'
    nums = re.findall(r'\d+', ms)
    if nums:
        v = int(nums[0])
        if 2 <= v <= 5:
            return str(v)
        if v > 5:
            return '5'
    return '5'


def parse_plan_menu_digit(m, max_n):
    """Dígito 1–max_n (menú post-plan dinámico); evita que '3' o '3️⃣' caigan en consulta libre."""
    if max_n < 1:
        return None
    raw = (m or '').strip()
    raw = re.sub(r'[\ufe0f\u200d]', '', raw).replace('️', '')
    if max_n <= 9 and re.fullmatch(rf'[1-{max_n}]', raw):
        return int(raw)
    solo = ''.join(c for c in raw if c.isdigit())
    if len(solo) == 1 and len(raw) <= 4:
        d = int(solo)
        if 1 <= d <= max_n:
            return d
    return None


def quiere_plan_semanal_o_vale(ml):
    t = (ml or '').strip().lower()
    if t in (
        'vale',
        'ok',
        'okay',
        'si',
        'sí',
        'dale',
        'genial',
        'perfecto',
        'adelante',
        'va',
        'vamos',
        'claro',
    ):
        return True
    return bool(
        re.search(
            r'\b(plan\s+semanal|mi\s+plan|el\s+plan|genera(?:r)?\s+el\s+plan|vuelve(?:r)?\s+a\s+generar|'
            r'regenera(?:r)?|mu[eé]strame\s+el\s+plan|mu[eé]strame\s+mi\s+plan|horario\s+semanal|quiero\s+el\s+plan)\b',
            t,
        )
    )


class ZiaNaturvitiaEngine:
    @staticmethod
    def _whatsapp_system_suffix():
        return (
            ' NUNCA uses markdown como ###, **, ##. Usa solo texto plano, '
            'mayúsculas para títulos y emojis.'
        )

    def __init__(self, config=None):
        self.config = config if config is not None else load_naturvitia_config()
        self.client_id = (self.config.get('_meta') or {}).get('client_id', 'naturvitia')
        self.openai = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
        self._users = {}

    def _get_user(self, uid):
        if uid not in self._users:
            self._users[uid] = {
                'state': 'nv_onb_nombre',
                'data': {},
                'history': [],
                'usado': set(),
            }
        return self._users[uid]

    def reset_user(self, uid):
        self._users[uid] = {'state': 'nv_onb_nombre', 'data': {}, 'history': [], 'usado': set()}

    def get_welcome_message(self):
        return self.config['bot']['welcome_message']

    def _text_and_image_url(self, message):
        if isinstance(message, str):
            return message.strip(), None
        if not isinstance(message, dict):
            return str(message).strip(), None
        raw = (
            message.get('text')
            or message.get('body')
            or message.get('caption')
            or ''
        )
        text = raw.strip() if isinstance(raw, str) else ''
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

    def _media_to_data_url(self, image_url):
        if image_url.startswith('data:'):
            return image_url
        if 'twilio.com' in image_url or 'api.twilio.com' in image_url:
            sid = os.environ.get('TWILIO_ACCOUNT_SID', '')
            token = os.environ.get('TWILIO_AUTH_TOKEN', '')
            r = requests.get(image_url, auth=(sid, token), timeout=20)
            r.raise_for_status()
            ct = r.headers.get('Content-Type', 'image/jpeg')
            if not ct.startswith('image/'):
                ct = 'image/jpeg'
            b64 = base64.b64encode(r.content).decode('utf-8')
            return f'data:{ct};base64,{b64}'
        return image_url

    def _model_chat(self):
        return self.config.get('ai', {}).get('model', 'gpt-4o-mini')

    _POST_PLAN_MENU_STATES = frozenset({'plan_listo', 'esperando_foto_dieta', 'nv_esperando_entreno'})

    _MENU_OPCIONES_ORDER = (
        ('lista_compra', 'Lista de la compra'),
        ('suplementos', 'Suplementos'),
        ('foto_dieta', 'Foto / análisis de tu dieta'),
        ('plan_semanal', 'Nuevo plan semanal'),
        ('que_como', 'Ideas de comida (¿qué como?)'),
    )

    @staticmethod
    def _ensure_usado(u):
        us = u.get('usado')
        if isinstance(us, set):
            return
        if isinstance(us, (list, tuple)):
            u['usado'] = set(us)
        else:
            u['usado'] = set()

    def _menu_items_disponibles(self, u):
        self._ensure_usado(u)
        used = u['usado']
        return [(k, lab) for k, lab in self._MENU_OPCIONES_ORDER if k not in used]

    def _menu_opciones_tras_domingo(self, u):
        items = self._menu_items_disponibles(u)
        if not items:
            return '\n\n¿Tienes alguna duda más sobre tu nutrición? 🤓'
        lines = ['\n\n---\n¿Qué quieres hacer ahora?']
        emojis = ('1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣')
        for i, (_, lab) in enumerate(items):
            lines.append(emojis[i] + ' ' + lab)
        lines.append('\n(Escribe el número, una palabra clave o lo que necesites.)')
        return '\n'.join(lines)

    def _with_post_plan_menu(self, u, text):
        if u.get('state') in self._POST_PLAN_MENU_STATES:
            return text + self._menu_opciones_tras_domingo(u)
        return text

    def _match_menu_keyword(self, ml, keys_only):
        if not ml or not keys_only:
            return None
        if 'lista_compra' in keys_only and re.search(
            r'\b(lista\s*(de\s*la)?\s*compra|supermercado|carrito)\b', ml
        ):
            return 'lista_compra'
        if 'suplementos' in keys_only and (
            'suplement' in ml or 'vitamina' in ml or 'omega' in ml or 'creatina' in ml
        ):
            return 'suplementos'
        if 'foto_dieta' in keys_only and (
            re.search(r'\b(foto|imagen|plato)\b', ml) or 'analisis' in ml or 'análisis' in ml
        ):
            return 'foto_dieta'
        if 'plan_semanal' in keys_only and re.search(
            r'\b(plan\s+semanal|regenera|nuevo\s+plan|horario\s+semanal)\b', ml
        ):
            return 'plan_semanal'
        if 'que_como' in keys_only and wants_food_advice(ml):
            return 'que_como'
        return None

    def _calc_energy(self, d):
        bmr = harris_benedict_revised_bmr(
            d.get('sexo', ''),
            d.get('peso', 70),
            d.get('altura', 170),
            d.get('edad', 30),
        )
        factor = _activity_factor(d.get('actividad', ''))
        tdee = bmr * factor
        kcal = apply_goal_to_tdee(tdee, d.get('objetivo', ''))
        p_g, c_g, f_g = macros_grams(kcal, d.get('objetivo', ''))
        return {
            'bmr': int(round(bmr)),
            'tdee_sin_ajuste': int(round(tdee)),
            'kcal_objetivo': kcal,
            'proteinas_g': p_g,
            'carbos_g': c_g,
            'grasas_g': f_g,
            'factor_actividad': factor,
        }

    def _plan_profile_context(self, d, energy):
        comidas = d.get('comidas_dia', '5')
        return (
            'Contexto del paciente:\n'
            '- Nombre: '
            + d.get('nombre', '')
            + '\n- Objetivo: '
            + d.get('objetivo', '')
            + '\n- Actividad: '
            + d.get('actividad', '')
            + '\n- Restricciones: '
            + d.get('restricciones', 'ninguna')
            + '\n- Patologías declaradas: '
            + d.get('patologias', 'ninguna')
            + '\n- Comidas al día (referencia onboarding): '
            + str(comidas)
            + '.\n'
            '- GET diario orientativo: '
            + str(energy['kcal_objetivo'])
            + ' kcal; macros diarios orientativos: P '
            + str(energy['proteinas_g'])
            + ' g, C '
            + str(energy['carbos_g'])
            + ' g, G '
            + str(energy['grasas_g'])
            + ' g.\n'
            '- Presupuesto compra semanal (euros): '
            + str(d.get('presupuesto', 'no indicado'))
            + '.\n'
        )

    def _plan_system_content(self):
        return (
            (self.config['bot'].get('personality') or '')
            + ' Responde en español. '
            'Pon un emoji de comida acorde junto a cada plato o preparación (ej. 🥗 🍳 🐟). '
            'En cada alimento con cantidad indica si es CRUDO o COCINADO (ej. pollo 150 g crudo ≈ 110 g cocido). '
            'Alinea el día con el GET y macros del contexto. No repitas otros días de la semana fuera del bloque pedido. '
            'Máximo 5 comidas al día según el contexto del usuario. '
            'Nunca uses la palabra colacion; para tomas intermedias usa solo MEDIA MAÑANA o MERIENDA.'
            + self._whatsapp_system_suffix()
        )

    def _plan_comidas_diarias_rules(self, d):
        try:
            n = int(str(d.get('comidas_dia', '3')).strip())
        except (TypeError, ValueError):
            n = 3
        n = max(2, min(5, n))
        if n == 5:
            return (
                'ESTRUCTURA DE CADA DIA (el usuario indicó 5 comidas; exactamente 5 tomas, en este orden): '
                'DESAYUNO, MEDIA MAÑANA, COMIDA, MERIENDA, CENA. '
                'Nunca uses la palabra colacion; no llames colacion a ninguna toma.\n'
            )
        if n == 4:
            return (
                'ESTRUCTURA DE CADA DIA (el usuario indicó 4 comidas; exactamente 4 tomas, en este orden): '
                'DESAYUNO, COMIDA, MERIENDA (entre comida y cena), CENA. Sin media mañana. '
                'Nunca uses la palabra colacion.\n'
            )
        if n == 3:
            return (
                'ESTRUCTURA DE CADA DIA (el usuario indicó 3 comidas; exactamente 3 tomas): '
                'DESAYUNO, COMIDA y CENA solamente. Sin media mañana ni merienda. '
                'Nunca uses la palabra colacion.\n'
            )
        return (
            'ESTRUCTURA DE CADA DIA (el usuario indicó 2 comidas; exactamente 2 tomas principales al dia): '
            'elige la combinacion mas adecuada al perfil entre DESAYUNO+COMIDA, COMIDA+CENA o DESAYUNO+CENA; '
            'solo esos dos bloques nombrados. Sin media mañana ni merienda. Nunca uses la palabra colacion.\n'
        )

    _DIAS_SEMANA_PLAN = (
        ('LUNES', '📆'),
        ('MARTES', '🗓️'),
        ('MIERCOLES', '⛅'),
        ('JUEVES', '🌤️'),
        ('VIERNES', '✨'),
        ('SABADO', '🎯'),
        ('DOMINGO', '🌴'),
    )

    def _gpt_plan_single_day(self, user_content):
        r = self.openai.chat.completions.create(
            model=self._model_chat(),
            messages=[
                {'role': 'system', 'content': self._plan_system_content()},
                {'role': 'user', 'content': user_content},
            ],
            max_tokens=600,
            temperature=float(self.config.get('ai', {}).get('temperature', 0.7)),
            timeout=20,
        )
        return r.choices[0].message.content

    def _weekly_plan_eight_messages(self, d, energy, intro_first_line, u):
        import time

        ctx = self._plan_profile_context(d, energy)
        comidas_rules = self._plan_comidas_diarias_rules(d)
        bloque_macros = (
            '\n\n📊 *Tu cálculo (Harris-Benedict revisada + actividad + objetivo)*\n'
            '• GET diario orientativo: *'
            + str(energy['kcal_objetivo'])
            + ' kcal/día*\n'
            '• BMR ~'
            + str(energy['bmr'])
            + ' kcal | TDEE base ~'
            + str(energy['tdee_sin_ajuste'])
            + ' kcal\n'
            '• Macros orientativos: P '
            + str(energy['proteinas_g'])
            + ' g | C '
            + str(energy['carbos_g'])
            + ' g | G '
            + str(energy['grasas_g'])
            + ' g\n\n'
            'Te envío 7 mensajes seguidos, uno por día, en orden: LUNES, MARTES, MIERCOLES, JUEVES, VIERNES, SABADO, DOMINGO. '
            'Cada día empieza con el nombre en MAYÚSCULAS y un emoji; crudo/cocinado cuando aplique.'
        )
        msgs = [intro_first_line + bloque_macros]
        for dia, emoji in self._DIAS_SEMANA_PLAN:
            time.sleep(2)
            user_prompt = (
                ctx
                + comidas_rules
                + 'TAREA: Plan nutricional detallado para UN SOLO DIA: '
                + dia
                + '. '
                'La PRIMERA linea debe ser exactamente la palabra '
                + dia
                + ' en mayusculas, un espacio, y UN emoji de comida o agenda (puedes usar '
                + emoji
                + ' u otro coherente). Sin saludo ni lineas previas. '
                'Incluye todas las tomas del dia segun la estructura indicada, con porciones CRUDO/COCINADO y macros del dia. '
                'No incluyas otros dias, ni lista de la compra, ni menu de opciones al final.\n'
                'OBLIGATORIO: el nombre del dia SIEMPRE debe ir seguido de un emoji de comida. '
                'Ejemplo: LUNES 🥗, MARTES 🍳, MIERCOLES 🥙, JUEVES 🍗, VIERNES 🐟, SABADO 🥩, DOMINGO 🍲. '
                'Nunca omitas el emoji del dia.'
            )
            msgs.append(self._gpt_plan_single_day(user_prompt).strip())
        return msgs

    def _generar_plan_completo(self, u, intro_first_line=None, marcar_plan_semanal_usado=True):
        d = u['data']
        if not str(d.get('presupuesto', '')).strip():
            d['presupuesto'] = '60'
        energy = self._calc_energy(d)
        u['last_energy'] = energy
        if intro_first_line is not None:
            intro = intro_first_line
        else:
            intro = (
                'Perfecto, '
                + d.get('nombre', '')
                + '. Ya tengo tu perfil. Generando tu plan semanal con macros… ✅'
            )
        msgs = self._weekly_plan_eight_messages(d, energy, intro, u)
        self._ensure_usado(u)
        if marcar_plan_semanal_usado:
            u['usado'].add('plan_semanal')
        msgs[-1] += self._menu_opciones_tras_domingo(u)
        return msgs

    def _generar_lista_compra(self, u):
        d = u['data']
        le = u.get('last_energy') or {}
        pres = str(d.get('presupuesto', '60')).replace(',', '.')
        prompt = (
            'Genera una LISTA DE LA COMPRA SEMANAL en español, texto plano, con emojis por categoría. '
            'Presupuesto TOTAL semanal del usuario: '
            + pres
            + ' euros (prioridad: adaptar cantidades, priorizar ingredientes economicos razonables y evitar productos caros innecesarios; '
            'el total orientativo debe respetar ese techo salvo que sea imposible, en cuyo caso indicalo en una linea al final).\n'
            'Basate en el perfil y en un plan equilibrado tipo el que seguiria alguien con estos datos:\n'
            + json.dumps(d, ensure_ascii=False)
            + '\nGET orientativo '
            + str(le.get('kcal_objetivo', ''))
            + ' kcal/dia; macros P '
            + str(le.get('proteinas_g', ''))
            + ' g, C '
            + str(le.get('carbos_g', ''))
            + ' g, G '
            + str(le.get('grasas_g', ''))
            + ' g.\n'
            'Agrupa: proteinas, lacteos/verduras, fruta, cereales, grasas, otros. Cantidades orientativas crudo/cocinado. '
            'Maximo 450 palabras.'
        )
        r = self.openai.chat.completions.create(
            model=self._model_chat(),
            messages=[
                {
                    'role': 'system',
                    'content': 'Eres ZIA de Naturvitia. Listas practicas para la compra.' + self._whatsapp_system_suffix(),
                },
                {'role': 'user', 'content': prompt},
            ],
            max_tokens=900,
            temperature=0.65,
            timeout=35,
        )
        return r.choices[0].message.content

    def _gpt_suplementos_plan(self, u):
        d = u['data']
        prompt = (
            'Sugiere suplementos SOLO si encajan con el objetivo y perfil; si no hacen falta, dilo claro. '
            'Justifica cada uno en una linea. Perfil: '
            + json.dumps(d, ensure_ascii=False)
            + '\nMaximo 220 palabras, español, sin vendas agresivas.'
        )
        r = self.openai.chat.completions.create(
            model=self._model_chat(),
            messages=[
                {
                    'role': 'system',
                    'content': 'Eres ZIA de Naturvitia, criterio prudente.' + self._whatsapp_system_suffix(),
                },
                {'role': 'user', 'content': prompt},
            ],
            max_tokens=500,
            temperature=0.6,
            timeout=30,
        )
        return r.choices[0].message.content

    def _gpt_meal_after_training(self, u, entreno_si):
        d = u['data']
        ml = 'sí ha entrenado hoy' if entreno_si else 'no ha entrenado hoy'
        prompt = (
            'El usuario pregunta qué comer. Contexto: '
            + ml
            + '.\nPerfil: '
            + json.dumps(d, ensure_ascii=False)
            + '\nObjetivo calórico diario aproximado: '
            + str(u.get('last_energy', {}).get('kcal_objetivo', ''))
            + ' kcal; macros: P '
            + str(u.get('last_energy', {}).get('proteinas_g', ''))
            + ' g, C '
            + str(u.get('last_energy', {}).get('carbos_g', ''))
            + ' g, G '
            + str(u.get('last_energy', {}).get('grasas_g', ''))
            + ' g.\n'
            'Propón la siguiente comida (o comida del momento) con 2-3 opciones breves. '
            'Siempre indica cantidades en CRUDO y equivalente COCINADO cuando aplique. '
            'Máximo 220 palabras, español, emojis discretos.'
        )
        r = self.openai.chat.completions.create(
            model=self._model_chat(),
            messages=[
                {
                    'role': 'system',
                    'content': (
                        'Eres ZIA de Naturvitia. Nutrición práctica, español, crudo vs cocinado obligatorio.'
                        + self._whatsapp_system_suffix()
                    ),
                },
                {'role': 'user', 'content': prompt},
            ],
            max_tokens=500,
            temperature=0.65,
            timeout=35,
        )
        return r.choices[0].message.content

    def _analizar_dieta_foto(self, data_url, caption, d):
        nombre = d.get('nombre', '')
        user_txt = (
            'INSTRUCCION ESTRICTA. Usa la foto (nevera, despensa o comida) solo para identificar ingredientes. '
            'NO describas la imagen ni la nevera. NO escribas introduccion ni conclusion ni menu al final.\n\n'
            'Perfil para adaptar recetas: '
            + nombre
            + '; objetivo: '
            + d.get('objetivo', '')
            + '; restricciones: '
            + d.get('restricciones', 'ninguna')
            + '; patologias: '
            + d.get('patologias', 'ninguna')
            + '.\n\n'
            'Devuelve EXACTAMENTE 3 recetas rapidas (menos de 20 minutos cada una) usando prioritariamente '
            'ingredientes visibles en la foto; si falta algo imprescindible, indicalo en una sola linea entre parentesis '
            'dentro de la receta, sin apartado extra.\n\n'
            'Para CADA receta usa este formato (sin titulos de seccion genericos antes del bloque):\n'
            '1) **Nombre de la receta**\n'
            'Ingredientes: lista con gramos y si es CRUDO o COCINADO (ej: pollo 120 g crudo / arroz 60 g crudo).\n'
            'Preparacion: paso 1 · paso 2 · paso 3 (exactamente tres pasos).\n'
            'Macros totales de la receta: kcal, P g, C g, G g (una linea).\n\n'
            'Repite el mismo formato para la receta 2 y 3. Espanol, emojis discretos solo en el nombre si quieres.'
        )
        if caption:
            user_txt += '\nNota del usuario (opcional): ' + caption.strip() + '.'
        r = self.openai.chat.completions.create(
            model='gpt-4o',
            messages=[
                {
                    'role': 'system',
                    'content': (
                        'Cumples al pie de la letra el formato pedido. Sin parrafos introductorios, sin describir '
                        'lo que hay en la foto, sin resumen final ni menu. Solo las 3 recetas en el formato indicado.'
                        + self._whatsapp_system_suffix()
                    ),
                },
                {
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': user_txt},
                        {'type': 'image_url', 'image_url': {'url': data_url}},
                    ],
                },
            ],
            max_tokens=900,
            timeout=50,
        )
        return r.choices[0].message.content

    def _gpt_libre(self, m, d):
        company = self.config['branding']['company_name']
        r = self.openai.chat.completions.create(
            model=self._model_chat(),
            messages=[
                {
                    'role': 'system',
                    'content': (self.config['bot'].get('system_prompt', '')[:12000] + self._whatsapp_system_suffix()),
                },
                {
                    'role': 'user',
                    'content': 'Consulta del usuario: '
                    + m
                    + '\nDatos conocidos: '
                    + json.dumps(d, ensure_ascii=False),
                },
            ],
            max_tokens=700,
            temperature=0.7,
            timeout=35,
        )
        return r.choices[0].message.content

    def process_message(self, user_id, message, plan_type='pro'):
        u = self._get_user(user_id)
        m, image_url = self._text_and_image_url(message if isinstance(message, dict) else {'text': message})
        m = (m or '').strip()
        ml = m.lower()
        d = u['data']
        s = u['state']
        # Sesiones antiguas (antes del rename de estados)
        if s == 'nv_onb_comidas':
            u['state'] = 'comidas_dia'
            s = 'comidas_dia'
        elif s == 'nv_activo':
            u['state'] = 'plan_listo'
            s = 'plan_listo'

        if image_url is not None and image_url:
            try:
                data_url = self._media_to_data_url(image_url)
                estado_prev = u['state']
                out = self._analizar_dieta_foto(data_url, m, d)
                if estado_prev == 'esperando_foto_dieta':
                    u['state'] = 'plan_listo'
                    self._ensure_usado(u)
                    u['usado'].add('foto_dieta')
                return self._with_post_plan_menu(u, out)
            except Exception as e:
                err = 'No pude leer la imagen: ' + str(e)[:100]
                return self._with_post_plan_menu(u, err)

        if is_reset(m):
            self.reset_user(user_id)
            u = self._get_user(user_id)
            d = u['data']
            s = u['state']
            return self.get_welcome_message()

        # --- Onboarding secuencial ---
        if s == 'nv_onb_nombre':
            if not m:
                return '¿Cómo te llamas?'
            d['nombre'] = m.strip()[:80]
            u['state'] = 'nv_onb_edad'
            return 'Encantada, ' + d['nombre'] + '. ¿Cuántos años tienes?'

        if s == 'nv_onb_edad':
            nums = re.findall(r'\d+', m)
            if not nums or not (5 <= int(nums[0]) <= 120):
                return 'Indica tu edad en años (número), por ejemplo: 34'
            d['edad'] = int(nums[0])
            u['state'] = 'nv_onb_sexo'
            return '¿Sexo biológico para el cálculo energético? Responde *hombre* o *mujer*.'

        if s == 'nv_onb_sexo':
            if 'hom' in ml or 'mascul' in ml or ml == 'h':
                d['sexo'] = 'Hombre'
            elif 'muj' in ml or 'femen' in ml or ml == 'm':
                d['sexo'] = 'Mujer'
            else:
                return 'Por favor indica *hombre* o *mujer* (sirve para la fórmula de Harris-Benedict).'
            u['state'] = 'nv_onb_peso'
            return '¿Peso actual en kg? (ej: 72.5)'

        if s == 'nv_onb_peso':
            m2 = m.replace(',', '.')
            nums = re.findall(r'\d+\.?\d*', m2)
            if not nums:
                return 'Escribe tu peso en kilos, por ejemplo: 68'
            w = float(nums[0])
            if not (25 <= w <= 250):
                return 'Peso fuera de rango habitual. Indica kg con un número razonable (ej: 70).'
            d['peso'] = w
            u['state'] = 'nv_onb_altura'
            return '¿Altura en cm? (ej: 172)'

        if s == 'nv_onb_altura':
            nums = [int(n) for n in re.findall(r'\d+', m)]
            ok = [n for n in nums if 120 <= n <= 220]
            if not ok:
                return 'Indica altura en centímetros (ej: 168).'
            d['altura'] = ok[0]
            u['state'] = 'nv_onb_objetivo'
            return (
                '¿Objetivo principal? (puedes elegir o escribir)\n'
                '• Perder grasa\n• Ganar músculo\n• Mejorar hábitos\n• Mantenimiento'
            )

        if s == 'nv_onb_objetivo':
            if len(m) < 3:
                return 'Cuéntame tu objetivo en una frase o elige una de las opciones.'
            d['objetivo'] = m.strip()[:500]
            u['state'] = 'nv_onb_actividad'
            return (
                '¿Cuál es tu nivel de actividad? Elige el que mejor te encaje (responde con el nombre o el número):\n\n'
                '1️⃣ *Sedentario*: 0 días de entreno, menos de 5.000 pasos/día\n'
                '2️⃣ *Ligero*: 1–2 días de entreno, 5.000–7.500 pasos/día\n'
                '3️⃣ *Moderado*: 3–4 días de entreno, 7.500–10.000 pasos/día\n'
                '4️⃣ *Activo*: 5–6 días de entreno, 10.000–12.500 pasos/día\n'
                '5️⃣ *Muy activo*: entreno diario o dobles sesiones, más de 12.500 pasos/día\n\n'
                '_Sirve para el factor sobre tu metabolismo basal (Harris-Benedict)._'
            )

        if s == 'nv_onb_actividad':
            ms = m.strip()
            if len(ms) < 3 and ms not in ('1', '2', '3', '4', '5'):
                return (
                    'Indica uno de los niveles (ej: *moderado*) o el número del 1 al 5, según los ejemplos de días '
                    'de entreno y pasos diarios que te envié arriba.'
                )
            d['actividad'] = ms[:200]
            u['state'] = 'nv_onb_restricciones'
            return '¿Alergias o restricciones alimentarias? (si no hay, escribe *ninguna*)'

        if s == 'nv_onb_restricciones':
            d['restricciones'] = m.strip()[:500] or 'Ninguna'
            u['state'] = 'nv_onb_patologias'
            return (
                '¿Patologías o condiciones médicas relevantes para la dieta? '
                '(si no quieres detallar, escribe *ninguna*; recuerda que no sustituyen el criterio de tu médico)'
            )

        if s == 'nv_onb_patologias':
            d['patologias'] = m.strip()[:500] or 'Ninguna'
            u['state'] = 'comidas_dia'
            return '¿Cuántas comidas al día haces habitualmente? (número entre 2 y 5, ej: 4)'

        if s == 'comidas_dia':
            if not m.strip():
                return '¿Cuántas comidas al día haces habitualmente? (número entre 2 y 5, ej: 4)'
            if ml in ('reintentar', 'retry', 'otra vez'):
                if not d.get('comidas_dia'):
                    return 'Indica primero cuántas comidas al día haces (número del 2 al 5 o en texto).'
                if d.get('presupuesto'):
                    try:
                        msgs = self._generar_plan_completo(u, intro_first_line='Listo, reintento del plan ✅')
                        u['state'] = 'plan_listo'
                        return msgs
                    except Exception as e:
                        return 'Sigue sin funcionar: ' + str(e)[:100]
                u['state'] = 'presupuesto'
                return (
                    '¿Cuánto quieres gastar en la compra esta semana? 💰 '
                    '(escribe la cantidad en euros, ej: 60)'
                )
            d['comidas_dia'] = parse_comidas_dia_value(m)
            u['state'] = 'presupuesto'
            return (
                '¿Cuánto quieres gastar en la compra esta semana? 💰 '
                '(escribe la cantidad en euros, ej: 60)'
            )

        if s == 'presupuesto':
            if not m.strip():
                return (
                    '¿Cuánto quieres gastar en la compra esta semana? 💰 '
                    '(escribe la cantidad en euros, ej: 60)'
                )
            m2 = m.replace(',', '.')
            nums = re.findall(r'\d+\.?\d*', m2)
            if not nums:
                return 'No he entendido la cantidad. Escribe el importe en euros, ej: 60 o 72.50'
            try:
                p = float(nums[0])
            except ValueError:
                return 'Escribe un número válido en euros, ej: 65'
            if not (10 <= p <= 600):
                return 'Indica un importe razonable entre 10 y 600 euros.'
            d['presupuesto'] = str(int(p)) if p == int(p) else str(round(p, 2))
            try:
                msgs = self._generar_plan_completo(u, marcar_plan_semanal_usado=False)
                u['state'] = 'plan_listo'
                return msgs
            except Exception as e:
                return (
                    'No pude generar el plan ahora: '
                    + str(e)[:120]
                    + '\nRepite la cantidad en euros o escribe *reintentar* desde comidas.'
                )

        # --- Tras el plan: consultas, regenerar plan, comida con pregunta entreno ---
        if s == 'nv_esperando_entreno':
            yn = parse_yes_no(ml)
            if yn is None:
                return self._with_post_plan_menu(
                    u,
                    'Responde con *sí* o *no*: ¿has entrenado hoy? Así ajusto carbohidratos y timing.',
                )
            u['state'] = 'plan_listo'
            try:
                out = self._gpt_meal_after_training(u, yn)
                self._ensure_usado(u)
                u['usado'].add('que_como')
                return self._with_post_plan_menu(u, out)
            except Exception as e:
                return self._with_post_plan_menu(u, 'Error al sugerir comida: ' + str(e)[:80])

        if s == 'esperando_foto_dieta':
            if ml in ('cancelar', 'salir', 'volver', 'menu', 'menú', '0'):
                u['state'] = 'plan_listo'
                return self._with_post_plan_menu(u, 'De acuerdo.')
            return self._with_post_plan_menu(
                u,
                'Envía una foto de tu plato o de tu día de comidas para analizarla (como imagen). '
                'Cuando la tengas, mándala aquí. Para volver al menú escribe *cancelar* o *menu*.',
            )

        if s == 'plan_listo':
            self._ensure_usado(u)
            items = self._menu_items_disponibles(u)
            keys_only = [k for k, _ in items]
            n = len(items)

            if ml in ('reintentar', 'retry', 'otra vez') and d.get('comidas_dia'):
                try:
                    msgs = self._generar_plan_completo(u, intro_first_line='Listo, reintento del plan ✅')
                    u['state'] = 'plan_listo'
                    return msgs
                except Exception as e:
                    return self._with_post_plan_menu(u, 'No pude regenerar el plan: ' + str(e)[:120])

            if quiere_plan_semanal_o_vale(ml):
                try:
                    msgs = self._generar_plan_completo(
                        u,
                        intro_first_line='Aquí tienes de nuevo tu plan semanal con macros ✅',
                    )
                    u['state'] = 'plan_listo'
                    return msgs
                except Exception as e:
                    return self._with_post_plan_menu(u, 'No pude regenerar el plan: ' + str(e)[:120])

            digit = parse_plan_menu_digit(m, n) if n else None
            chosen_key = keys_only[digit - 1] if digit is not None else None
            if chosen_key is None and m.strip():
                chosen_key = self._match_menu_keyword(ml, keys_only)

            if chosen_key == 'lista_compra':
                try:
                    out = self._generar_lista_compra(u)
                    u['usado'].add('lista_compra')
                    return self._with_post_plan_menu(u, out)
                except Exception as e:
                    return self._with_post_plan_menu(u, 'No pude generar la lista: ' + str(e)[:80])
            if chosen_key == 'suplementos':
                try:
                    out = self._gpt_suplementos_plan(u)
                    u['usado'].add('suplementos')
                    return self._with_post_plan_menu(u, out)
                except Exception as e:
                    return self._with_post_plan_menu(u, 'No pude preparar suplementos: ' + str(e)[:80])
            if chosen_key == 'foto_dieta':
                u['state'] = 'esperando_foto_dieta'
                return self._with_post_plan_menu(
                    u,
                    'Perfecto. Envía una foto de tu comida o de tu día y te doy feedback sobre tu dieta '
                    '(macros aproximados, crudo/cocinado y 2 ideas de mejora). Para volver al menú: *cancelar*.',
                )
            if chosen_key == 'plan_semanal':
                try:
                    msgs = self._generar_plan_completo(
                        u,
                        intro_first_line='Aquí tienes tu nuevo plan semanal con macros ✅',
                    )
                    u['state'] = 'plan_listo'
                    return msgs
                except Exception as e:
                    return self._with_post_plan_menu(u, 'No pude generar el plan: ' + str(e)[:120])
            if chosen_key == 'que_como':
                u['state'] = 'nv_esperando_entreno'
                return self._with_post_plan_menu(
                    u,
                    'Antes de recomendarte qué comer: ¿has entrenado hoy? (sí / no)',
                )

            if wants_food_advice(ml) and 'que_como' not in u['usado']:
                u['state'] = 'nv_esperando_entreno'
                return self._with_post_plan_menu(
                    u,
                    'Antes de recomendarte qué comer: ¿has entrenado hoy? (sí / no)',
                )

            if m:
                try:
                    return self._with_post_plan_menu(u, self._gpt_libre(m, d))
                except Exception as e:
                    return self._with_post_plan_menu(u, 'Error: ' + str(e)[:80])
            if n == 0:
                return '¿Tienes alguna duda más sobre tu nutrición? 🤓'
            return self._with_post_plan_menu(
                u,
                'Envía texto o una foto. Si quieres ideas de comida, pregunta por ejemplo *¿qué como ahora?* '
                'y te preguntaré si has entrenado.',
            )

        u['state'] = 'nv_onb_nombre'
        return self.get_welcome_message()
