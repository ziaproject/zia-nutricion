"""
Motor Naturvitia: onboarding completo, Harris-Benedict, plan semanal con macros,
crudo vs cocinado, pregunta por entreno antes de sugerir comida, vision GPT-4o.
"""

import base64
import json
import os
import re

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
    ml = (text or '').lower()
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


class ZiaNaturvitiaEngine:
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
            }
        return self._users[uid]

    def reset_user(self, uid):
        self._users[uid] = {'state': 'nv_onb_nombre', 'data': {}, 'history': []}

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
            + '\n- Comidas al día: '
            + str(comidas)
            + ' (organiza desayuno/comida/cena y colaciones si aplica).\n'
            '- GET diario orientativo: '
            + str(energy['kcal_objetivo'])
            + ' kcal; macros diarios orientativos: P '
            + str(energy['proteinas_g'])
            + ' g, C '
            + str(energy['carbos_g'])
            + ' g, G '
            + str(energy['grasas_g'])
            + ' g.\n'
        )

    def _plan_system_content(self):
        return (
            (self.config['bot'].get('personality') or '')
            + ' Responde en español con emojis discretos. '
            'En cada alimento con cantidad indica si es CRUDO o COCINADO (ej. pollo 150 g crudo ≈ 110 g cocido). '
            'Alinea el día con el GET y macros del contexto. No repitas otros días de la semana fuera del bloque pedido.'
        )

    def _gpt_plan_chunk(self, user_content):
        r = self.openai.chat.completions.create(
            model=self._model_chat(),
            messages=[
                {'role': 'system', 'content': self._plan_system_content()},
                {'role': 'user', 'content': user_content},
            ],
            max_tokens=500,
            temperature=float(self.config.get('ai', {}).get('temperature', 0.7)),
            timeout=20,
        )
        return r.choices[0].message.content

    def _weekly_plan_four_messages(self, d, energy, intro_first_line):
        ctx = self._plan_profile_context(d, energy)
        msg1 = (
            intro_first_line
            + '\n\n📊 *Tu cálculo (Harris-Benedict revisada + actividad + objetivo)*\n'
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
            'Te envío el plan en varios mensajes; en cada comida verás *crudo vs cocinado* cuando aplique.'
        )
        p2 = (
            ctx
            + 'TAREA: Plan detallado solo para *Lunes, Martes y Miércoles*. '
            'Para cada día: comidas según sus tomas diarias, con porciones y macros aproximados del día. '
            'No incluyas jueves en adelante ni lista de la compra.'
        )
        p3 = (
            ctx
            + 'TAREA: Plan detallado solo para *Jueves, Viernes y Sábado*. '
            'Misma estructura que habrías usado para esos días; no repitas lun-mié. '
            'No incluyas domingo ni lista de la compra.'
        )
        p4 = (
            ctx
            + 'TAREA: Plan detallado solo para *Domingo* (todas las comidas del día). '
            'Después, *lista de la compra semanal* agrupada por categorías '
            '(proteínas, lácteos/vegetales, fruta, cereales, grasas, otros) '
            'con cantidades orientativas en crudo/cocinado cuando aplique. '
            'No repitas lunes a sábado.'
        )
        msg2 = self._gpt_plan_chunk(p2)
        msg3 = self._gpt_plan_chunk(p3)
        msg4 = self._gpt_plan_chunk(p4)
        return [msg1, msg2, msg3, msg4]

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
                    'content': 'Eres ZIA de Naturvitia. Nutrición práctica, español, crudo vs cocinado obligatorio.',
                },
                {'role': 'user', 'content': prompt},
            ],
            max_tokens=500,
            temperature=0.65,
            timeout=35,
        )
        return r.choices[0].message.content

    def _gpt_photo_analysis(self, data_url, caption, d):
        nombre = d.get('nombre', '')
        user_txt = (
            'Analiza la imagen (comida, nevera o etiqueta). '
            'Perfil: '
            + nombre
            + ', objetivo: '
            + d.get('objetivo', '')
            + ', restricciones: '
            + d.get('restricciones', 'ninguna')
            + ', patologías declaradas: '
            + d.get('patologias', 'ninguna')
            + '. '
        )
        if caption:
            user_txt += 'Mensaje del usuario: ' + caption + '. '
        user_txt += (
            'Resume lo que ves, estima macros aproximados si es plato/comida (indicando crudo vs cocinado), '
            'y da 2 recomendaciones concretas. Máximo 280 palabras, español.'
        )
        r = self.openai.chat.completions.create(
            model='gpt-4o',
            messages=[
                {
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': user_txt},
                        {'type': 'image_url', 'image_url': {'url': data_url}},
                    ],
                }
            ],
            max_tokens=700,
            timeout=50,
        )
        return r.choices[0].message.content

    def _gpt_libre(self, m, d):
        company = self.config['branding']['company_name']
        r = self.openai.chat.completions.create(
            model=self._model_chat(),
            messages=[
                {'role': 'system', 'content': self.config['bot'].get('system_prompt', '')[:12000]},
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
                '¿Nivel de actividad?\n'
                '*sedentario* / *ligero* / *moderado* / *activo* / *muy activo*\n'
                '(sirve para el factor sobre tu metabolismo basal)'
            )

        if s == 'nv_onb_actividad':
            if len(m) < 3:
                return 'Indica uno: sedentario, ligero, moderado, activo o muy activo.'
            d['actividad'] = m.strip()[:200]
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
            u['state'] = 'nv_onb_comidas'
            return '¿Cuántas comidas al día haces habitualmente? (número entre 2 y 6, ej: 5)'

        if s == 'nv_onb_comidas':
            if ml in ('reintentar', 'retry', 'otra vez') and d.get('comidas_dia'):
                energy = self._calc_energy(d)
                u['last_energy'] = energy
                u['state'] = 'nv_activo'
                try:
                    msgs = self._weekly_plan_four_messages(
                        d, energy, 'Listo, reintento del plan ✅'
                    )
                    msgs[-1] += (
                        '\n\n---\nPuedes enviar *foto* o preguntar *qué comer*. '
                        'Si preguntas qué comer, antes te preguntaré si has entrenado 💪'
                    )
                    return msgs
                except Exception as e:
                    u['state'] = 'nv_onb_comidas'
                    return 'Sigue sin funcionar: ' + str(e)[:100]
            nums = re.findall(r'\d+', m)
            if not nums:
                return 'Indica un número del 2 al 6, por ejemplo: 4'
            n = int(nums[0])
            if not (2 <= n <= 6):
                return 'Por favor un número entre 2 y 6 comidas al día.'
            d['comidas_dia'] = str(n)
            energy = self._calc_energy(d)
            u['last_energy'] = energy
            u['state'] = 'nv_activo'
            try:
                msgs = self._weekly_plan_four_messages(
                    d,
                    energy,
                    'Perfecto, '
                    + d.get('nombre', '')
                    + '. Ya tengo tu perfil. Generando tu plan semanal con macros… ✅',
                )
                msgs[-1] += (
                    '\n\n---\nPuedes enviarme *foto de comida o nevera* para analizarla, o preguntarme *qué comer*. '
                    'Si preguntas qué comer, antes te preguntaré si has entrenado 💪'
                )
                return msgs
            except Exception as e:
                u['state'] = 'nv_onb_comidas'
                return 'No pude generar el plan ahora: ' + str(e)[:120] + '\nRepite el número de comidas o escribe *reintentar*.'

        # --- Activo: foto, comida con pregunta entreno, chat ---
        if s == 'nv_esperando_entreno':
            yn = parse_yes_no(ml)
            if yn is None:
                return 'Responde con *sí* o *no*: ¿has entrenado hoy? Así ajusto carbohidratos y timing.'
            u['state'] = 'nv_activo'
            try:
                return self._gpt_meal_after_training(u, yn)
            except Exception as e:
                return 'Error al sugerir comida: ' + str(e)[:80]

        if s == 'nv_activo':
            if image_url:
                try:
                    data_url = self._media_to_data_url(image_url)
                    return self._gpt_photo_analysis(data_url, m, d)
                except Exception as e:
                    return 'No pude leer la imagen: ' + str(e)[:100]

            if wants_food_advice(ml):
                u['state'] = 'nv_esperando_entreno'
                return 'Antes de recomendarte qué comer: ¿has entrenado hoy? (sí / no)'

            if m:
                try:
                    return self._gpt_libre(m, d)
                except Exception as e:
                    return 'Error: ' + str(e)[:80]
            return (
                'Envía texto o una foto. Si quieres ideas de comida, pregunta por ejemplo *¿qué como ahora?* '
                'y te preguntaré si has entrenado.'
            )

        u['state'] = 'nv_onb_nombre'
        return self.get_welcome_message()
