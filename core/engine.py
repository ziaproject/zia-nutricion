import json
import os
import re
import unicodedata
import urllib.parse
from core.supabase_client import upsert_user, get_user
import urllib.request
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


def sanitize_whatsapp_text(text):
    if not isinstance(text, str) or not text:
        return text
    text = re.sub(r'```[\s\S]*?```', '', text)
    lines = text.split('\n')
    out_lines = []
    for line in lines:
        s = re.sub(r'^[#]{1,6}\s*', '', line)
        m = re.match(r'^(\s*)-\s+(.*)$', s)
        if m:
            s = m.group(1) + '▪️ ' + m.group(2)
        else:
            m = re.match(r'^(\s*)\*\s+(\S.*)$', s)
            if m:
                s = m.group(1) + '▪️ ' + m.group(2)
        out_lines.append(s)
    text = '\n'.join(out_lines)
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'__([^_]+)__', r'\1', text)
    for _ in range(15):
        nxt = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'\1', text)
        if nxt == text:
            break
        text = nxt
    text = text.replace('**', '')
    text = re.sub(r'(?<!\w)_([^_\n]+)_(?!\w)', r'\1', text)
    return text


COACH_TONE = (
    'Eres ZIA, coach nutricional motivadora y cercana. Usa siempre un tono positivo, '
    'empático y motivador. Incluye frases de ánimo. Celebra los logros del usuario. '
    'Nunca respondas con listas frías - usa un tono de coach que inspira. '
    'Responde solo en texto plano para WhatsApp: nunca uses markdown (ni #, ni **, ni * '
    'para énfasis, ni guiones al inicio de línea como viñetas). Organiza con saltos de '
    'línea y emojis en lugar de títulos o negritas.'
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

    def _user_key(self, uid):
        return f"{self.client_id}_{uid}"

    def _default_user(self):
        return {'state': 'welcome', 'data': {}, 'plan': None, 'history': [], 'plan_count': 0}

    def _supabase_config(self):
        return self.config.get('integrations', {}).get('supabase', {})

    def _supabase_users_table(self):
        table = self._supabase_config().get('table_users') or 'usuarios'
        return 'usuarios' if table == 'users' else table

    def _supabase_enabled(self):
        cfg = self._supabase_config()
        return bool(
            cfg.get('enabled')
            and os.environ.get('SUPABASE_URL')
            and os.environ.get('SUPABASE_KEY')
        )

    def _supabase_headers(self, prefer=None):
        key = os.environ.get('SUPABASE_KEY', '')
        headers = {
            'apikey': key,
            'Authorization': 'Bearer ' + key,
            'Content-Type': 'application/json',
        }
        if prefer:
            headers['Prefer'] = prefer
        return headers

    def _supabase_url(self, table, suffix=''):
        base = os.environ.get('SUPABASE_URL', '').rstrip('/')
        return base + '/rest/v1/' + table + suffix

    def _supabase_request(self, method, table, suffix='', payload=None, prefer=None):
        body = None if payload is None else json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            self._supabase_url(table, suffix),
            data=body,
            method=method,
            headers=self._supabase_headers(prefer),
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            raw = response.read().decode('utf-8')
            return json.loads(raw) if raw else None

    def _get_user(self, uid):
        key = self._user_key(uid)
        if key not in self._users:
            self._users[key] = self._load_user(uid)
        return self._users[key]

    def _load_user(self, uid):
        fallback = self._default_user()
        if not self._supabase_enabled():
            return fallback
        try:
            table = self._supabase_users_table()
            user_id = str(uid)
            suffix = '?id=eq.' + urllib.parse.quote(user_id, safe='') + '&select=*'
            rows = self._supabase_request('GET', table, suffix) or []
            if not rows:
                return fallback
            row = rows[0]
            profile = row.get('profile') if isinstance(row.get('profile'), dict) else row
            user = self._default_user()
            user['state'] = profile.get('state') or row.get('state') or 'welcome'
            user['data'] = profile.get('data') or row.get('data') or {}
            user['plan'] = profile.get('plan') if 'plan' in profile else row.get('plan')
            user['history'] = profile.get('history') or row.get('history') or []
            user['plan_count'] = profile.get('plan_count') or row.get('plan_count') or 0
            return user
        except Exception as e:
            print('Supabase load fallback:', str(e)[:120])
            return fallback

    def _save_user(self, uid, user=None):
        key = self._user_key(uid)
        user = user or self._users.get(key) or self._default_user()
        self._users[key] = user
        if not self._supabase_enabled():
            return
        try:
            table = self._supabase_users_table()
            user_id = str(uid)
            full_payload = {
                'id': user_id,
                'whatsapp': user_id,
                'state': user.get('state', 'welcome'),
                'data': user.get('data', {}),
                'plan': user.get('plan'),
                'history': user.get('history', []),
                'plan_count': user.get('plan_count', 0),
                'profile': user,
            }
            compact_payload = {
                'id': user_id,
                'state': user.get('state', 'welcome'),
                'data': user.get('data', {}),
                'plan': user.get('plan'),
                'history': user.get('history', []),
                'plan_count': user.get('plan_count', 0),
            }
            profile_payload = {
                'id': user_id,
                'profile': user,
            }
            last_error = None
            for payload in [full_payload, compact_payload, profile_payload]:
                try:
                    self._supabase_request('POST', table, payload=[payload], prefer='resolution=merge-duplicates')
                    return
                except Exception as e:
                    last_error = e
            if last_error:
                raise last_error
        except Exception as e:
            print('Supabase save fallback:', str(e)[:120])

    def reset_user(self, uid):
        key = self._user_key(uid)
        count = self._users.get(key, {}).get('plan_count', 0)
        self._users[key] = self._default_user()
        self._users[key]['plan_count'] = count
        self._save_user(uid, self._users[key])

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
            partes = [descripcion]
            obj_g = (data.get('objetivo') or '').strip()
            if obj_g:
                partes.append('Objetivo(s): ' + obj_g + '.')
            rn = (data.get('restricciones_ninos') or '').strip()
            if rn and normalize_text(rn) not in ('ninguna', 'no', 'nada'):
                partes.append('Restricciones/alergias de menores: ' + rn + '.')
            rtxt = (data.get('restricciones') or '').strip()
            if rtxt and normalize_text(rtxt) not in ('ninguna', 'no'):
                partes.append('Restricciones/alergias del grupo: ' + rtxt + '.')
            return ' '.join(partes)
        partes = [
            data.get('nombre', ''),
            data.get('genero', ''),
            data.get('edad', ''),
            data.get('peso', ''),
            data.get('altura', ''),
            data.get('objetivo', ''),
            data.get('actividad', ''),
            'Restricciones/alergias: ' + data.get('restricciones', 'Ninguna'),
        ]
        return ', '.join([p for p in partes if p])

    def _supermercado_nombre(self, value):
        super_map = {
            '1': 'Mercadona', 'mercadona': 'Mercadona', 'merca': 'Mercadona',
            '2': 'Lidl', 'lidl': 'Lidl', 'lid': 'Lidl',
            '3': 'Aldi', 'aldi': 'Aldi',
            '4': 'Carrefour', 'carrefour': 'Carrefour', 'carrefur': 'Carrefour', 'carre': 'Carrefour',
            '5': 'Dia', 'dia': 'Dia', 'día': 'Dia',
            '6': 'Consum', 'consum': 'Consum',
            '7': 'Supercor', 'supercor': 'Supercor',
            '8': 'El Corte Ingles', 'el corte ingles': 'El Corte Ingles', 'el corte inglés': 'El Corte Ingles', 'corte ingles': 'El Corte Ingles', 'corte inglés': 'El Corte Ingles',
        }
        raw = str(value or '').strip()
        if not raw:
            return 'Mercadona'
        norm = normalize_text(raw)
        if any(w in norm for w in ['cualquiera', 'me da igual', 'no se', 'normal', 'super']):
            return 'Mercadona'
        dmen = self._menu_opcion_numero(raw, max_digit=8)
        if dmen and dmen in super_map:
            return super_map[dmen]
        return super_map.get(norm, raw)

    def _texto_pregunta_supermercado_onboarding(self):
        return (
            '🏪 En que supermercado sueles comprar?\n\n'
            '1️⃣ Mercadona\n'
            '2️⃣ Lidl\n'
            '3️⃣ Aldi\n'
            '4️⃣ Carrefour\n'
            '5️⃣ Dia\n'
            '6️⃣ Consum\n'
            '7️⃣ Supercor\n'
            '8️⃣ El Corte Ingles\n\n'
            'O escribe el nombre directamente'
        )

    def _aviso_supermercado_debe_ser_uno(self, m):
        if self._mensaje_indica_varios_supermercados(m):
            return (
                'Para darte el mejor plan necesito que elijas solo uno 🛒 ¿Cuál es tu supermercado principal?\n\n'
                + self._texto_pregunta_supermercado_onboarding()
            )
        return None

    def _supermercado_ids_mencionados(self, m):
        ml = normalize_text(m or '')
        ids = set()
        if 'el corte ingles' in ml or 'corte ingles' in ml:
            ids.add('corte')
        for sub, sid in (
            ('mercadona', 'mercadona'),
            ('lidl', 'lidl'),
            ('aldi', 'aldi'),
            ('carrefour', 'carrefour'),
            ('carrefur', 'carrefour'),
            ('supercor', 'supercor'),
        ):
            if sub in ml:
                ids.add(sid)
        if re.search(r'(^|[^a-z0-9])consum([^a-z0-9]|$)', ml):
            ids.add('consum')
        if re.search(r'\bdia\b', ml) or re.search(r'\bdiper\b', ml):
            ids.add('dia')
        if re.search(r'\bmerca\b', ml):
            ids.add('mercadona')
        if re.search(r'\bcarre\b', ml):
            ids.add('carrefour')
        return ids

    def _mensaje_indica_varios_supermercados(self, m):
        raw = (m or '').strip()
        if not raw:
            return False
        ml = normalize_text(raw)
        ids = self._supermercado_ids_mencionados(m)
        digs = set(re.findall(r'\b([1-8])\b', raw))
        if len(ids) >= 2 or len(digs) >= 2:
            return True
        if len(ids) >= 1 and len(digs) >= 1:
            return True
        chunks = [
            p.strip()
            for p in re.split(
                r'(?:\s*,\s*|\s+y\s+|\s+e\s+|\s*/\s*|\s*\+\s*|\s+o\s+|\s+ó\s+)',
                ml,
                0,
                re.I,
            )
            if p.strip()
        ]
        nchunks = 0
        for ch in chunks:
            if self._supermercado_ids_mencionados(ch):
                nchunks += 1
        if nchunks >= 2:
            return True
        return False

    def _marca_blanca_instruccion_ahorro(self, super_nombre):
        """Texto para el prompt de lista económica: marca blanca por cadena o productos baratos de temporada."""
        s = (super_nombre or '').strip()
        canon = {
            'Mercadona': (
                'Supermercado: Mercadona. Prioriza SIEMPRE marca Hacendado (despensa, lácteos, congelados, bebidas…) '
                'y Deliplus en frescos (carne, pescado, charcutería) cuando aplique.'
            ),
            'Lidl': (
                'Supermercado: Lidl. Prioriza SIEMPRE la marca blanca Lidl: Milbona (lácteos), Dulano (embutidos), '
                'Chef Select, Freshona, Crownfield, etc., según el tipo de producto.'
            ),
            'Aldi': (
                'Supermercado: Aldi. Prioriza SIEMPRE Aldi Cuisine y el resto de marcas propias Aldi (lácteos estilo Milbona propios de Aldi, etc.).'
            ),
            'Carrefour': (
                'Supermercado: Carrefour. Prioriza SIEMPRE marca blanca Carrefour: Simpl (económica), Carrefour Classic, '
                'Carrefour Bio o línea del distribuidor equivalente según el producto.'
            ),
            'Dia': (
                'Supermercado: Dia. Prioriza SIEMPRE marca DIA, Díper y gama económica de la cadena.'
            ),
            'Consum': (
                'Supermercado: Consum. Prioriza SIEMPRE marca blanca Consum.'
            ),
            'Supercor': (
                'Supermercado: Supercor. Prioriza marcas propias del grupo / gama económica de la tienda y productos de temporada a buen precio.'
            ),
            'El Corte Ingles': (
                'Supermercado: El Corte Inglés. Prioriza marca propia El Corte Inglés (incl. líneas económicas de la cadena).'
            ),
        }
        if s in canon:
            return canon[s]
        return (
            'Supermercado indicado por el usuario: '
            + s
            + '. No hay una marca blanca genérica fija para esta cadena: prioriza productos económicos de temporada, '
            'segunda marca o marca del distribuidor más barata que suelas encontrar ahí, con precios realistas para esa tienda.'
        )

    def _menu_principal_body_text(self):
        return (
            '¿Qué necesitas hoy? 👇\n\n'
            '1️⃣ 🛒 Hazme la lista de la compra\n'
            '2️⃣ 💰 Quiero ahorrar esta semana\n'
            '3️⃣ 🍽️ ¿Qué como con lo que tengo?\n'
            '4️⃣ 📅 Planifícame la semana\n'
            '5️⃣ 💊 Suplementación'
        )

    def _menu_principal_text(self, data):
        return self._menu_principal_body_text()

    def _append_menu_footer(self, data, ahorro=False):
        if ahorro:
            return '\n\n¿Algo más? 👇\n\n' + self._menu_principal_text(data)
        return '\n\n' + self._menu_principal_text(data)

    def _menu_opcion_numero(self, m, max_digit=9):
        raw = (m or '').strip()
        if not raw:
            return None
        emoji_idx = [
            '1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣',
        ]
        for i in range(min(max_digit, len(emoji_idx))):
            if emoji_idx[i] in raw:
                return str(i + 1)
        mo = re.match(r'^(\d)\s*[\.\):;)]?\s*$', raw)
        if mo:
            v = int(mo.group(1))
            if 1 <= v <= max_digit:
                return str(v)
        norm = normalize_text(raw)
        if norm.isdigit() and len(norm) == 1:
            v = int(norm)
            if 1 <= v <= max_digit:
                return norm
        return None

    def _texto_opciones_objetivo(self):
        return (
            '1️⃣ Perder peso\n'
            '2️⃣ Ganar músculo\n'
            '3️⃣ Más energía y vitalidad\n'
            '4️⃣ Comer más sano y natural\n'
            '5️⃣ Mejorar la digestión'
        )

    def _parse_objetivo_opcion(self, m):
        opts = {
            '1': 'Perder peso',
            '2': 'Ganar musculo',
            '3': 'Mas energia y vitalidad',
            '4': 'Comer mas sano',
            '5': 'Mejorar la digestion',
        }
        ml = normalize_text(m)
        elegido = opts.get((m or '').strip(), None)
        if not elegido:
            mn = self._menu_opcion_numero(m, max_digit=5)
            if mn:
                elegido = opts.get(mn)
        if not elegido:
            if any(w in ml for w in ['peso', 'grasa', 'adelgazar', 'bajar', 'definir', 'perder', 'forma']):
                elegido = 'Perder peso'
            elif any(w in ml for w in ['musculo', 'muscu', 'fuerza', 'ganar', 'volumen', 'gym']):
                elegido = 'Ganar musculo'
            elif any(w in ml for w in ['energia', 'vitalidad', 'cansancio', 'fatiga', 'rendimiento']):
                elegido = 'Mas energia y vitalidad'
            elif any(w in ml for w in ['sano', 'salud', 'mejor', 'bien', 'cuidarme', 'habitos', 'natural']):
                elegido = 'Comer mas sano'
            elif any(w in ml for w in ['digest', 'hinch', 'estomago', 'intestinal', 'gases']):
                elegido = 'Mejorar la digestion'
        return elegido

    def _texto_pregunta_objetivo_onboarding(self):
        return '¿Cuál es tu objetivo principal? 🎯\n\n' + self._texto_opciones_objetivo()

    def _texto_pregunta_objetivo_familia(self):
        return (
            '¿Cuál es vuestro objetivo principal? 🎯 Podéis elegir varios separados por comas o por «y».\n\n'
            + self._texto_opciones_objetivo()
        )

    def _es_plan_familia(self, data):
        p = (data.get('personas') or '')
        return p.startswith('familia') or 'plan familiar' in normalize_text(p)

    def _labels_objetivo_display(self):
        return {
            'Perder peso': 'Perder peso',
            'Ganar musculo': 'Ganar músculo',
            'Mas energia y vitalidad': 'Más energía y vitalidad',
            'Comer mas sano': 'Comer más sano y natural',
            'Mejorar la digestion': 'Mejorar la digestión',
        }

    def _parse_objetivos_familia_multiples(self, m):
        raw = (m or '').strip()
        if not raw:
            return None
        pretty_map = self._labels_objetivo_display()
        inner_opts = {
            '1': 'Perder peso',
            '2': 'Ganar musculo',
            '3': 'Mas energia y vitalidad',
            '4': 'Comer mas sano',
            '5': 'Mejorar la digestion',
        }
        segs = []
        for chunk in re.split(r'\s*,\s*|\s*/\s*', raw):
            for sub in re.split(r'\s+y\s+', chunk, flags=re.I):
                t = sub.strip()
                if t:
                    segs.append(t)
        picked = []
        for seg in segs:
            sraw = seg.strip()
            one = self._parse_objetivo_opcion(sraw)
            if one:
                disp = pretty_map.get(one, one)
                if disp not in picked:
                    picked.append(disp)
                continue
            for d in sorted(set(re.findall(r'[1-5]', sraw)), key=int):
                one_i = inner_opts.get(d)
                if one_i:
                    disp = pretty_map.get(one_i, one_i)
                    if disp not in picked:
                        picked.append(disp)
        if picked:
            return ' y '.join(picked)
        one = self._parse_objetivo_opcion(raw)
        if one:
            return pretty_map.get(one, one)
        if len(raw) >= 2:
            return raw
        return None

    def _texto_familia_num_ninos(self):
        return (
            '¿Cuántos niños?\n\n'
            '1️⃣ Uno\n'
            '2️⃣ Dos\n'
            '3️⃣ Tres o más'
        )

    def _parse_familia_num_ninos(self, m):
        raw = (m or '').strip()
        if not raw:
            return None
        ml = normalize_text(m)
        mn = self._menu_opcion_numero(m, max_digit=3)
        if mn == '1' or ml in ('uno', 'una', '1 nino', '1 niño', 'un nino', 'un niño', 'solo uno'):
            return '1'
        if mn == '2' or ml == 'dos' or '2 ninos' in ml or '2 niños' in ml:
            return '2'
        if (
            mn == '3'
            or ml in ('tres', 'mas', 'más')
            or 'tres o mas' in ml
            or '3 o mas' in ml
            or 'cuatro' in ml
            or 'cinco' in ml
        ):
            return '3+'
        mo = re.search(r'\b(\d+)\b', raw)
        if mo:
            v = int(mo.group(1))
            if v == 1:
                return '1'
            if v == 2:
                return '2'
            if v >= 3:
                return '3+'
        return None

    def _texto_familia_restricciones_ninos(self):
        return (
            '¿Algún niño tiene restricción alimentaria?\n\n'
            '✅ No, ninguna\n'
            '🌾 Sin gluten\n'
            '🥛 Sin lactosa\n'
            '🥜 Alergia a frutos secos\n'
            '✏️ Otra'
        )

    def _parse_restricciones_ninos(self, m):
        raw = (m or '').strip()
        if not raw:
            return None
        ml = normalize_text(m)
        mn = self._menu_opcion_numero(m, max_digit=5)
        tags = []
        if mn == '2' or 'gluten' in ml or 'celia' in ml or 'celiac' in ml:
            tags.append('Sin gluten')
        if mn == '3' or 'lactosa' in ml or 'lacteo' in ml or 'sin leche' in ml:
            tags.append('Sin lactosa')
        if mn == '4' or 'frutos secos' in ml or 'cacahuet' in ml or 'nueces' in ml or 'almendra' in ml:
            tags.append('Alergia a frutos secos')
        if mn == '5':
            rest = raw.split('\n')[0].strip()
            if len(rest) > 2:
                return 'Otra: ' + rest
        if any(w in ml for w in ('huevo', 'marisco', 'pescado', 'vegano', 'vegetarian')):
            if len(raw) > 2:
                return 'Otra: ' + raw[:200]
        if tags:
            return ', '.join(dict.fromkeys(tags))
        if mn == '1' or any(
            w in ml
            for w in (
                'ninguna',
                'no ninguna',
                'no hay',
                'ninguno',
                'sin problema',
                'todo bien',
                'nada',
                'sin restriccion',
                'sin restricción',
            )
        ) or ml in ('no', 'nop'):
            return 'Ninguna'
        if len(raw) > 2:
            return 'Otra: ' + raw[:200]
        return 'Ninguna'

    def _texto_familia_actividad(self):
        return (
            '¿Soléis hacer actividad física en familia? 🏃\n\n'
            '1️⃣ Poco o nada\n'
            '2️⃣ Alguna vez\n'
            '3️⃣ Sí, regularmente'
        )

    def _parse_familia_actividad(self, m):
        raw = (m or '').strip()
        if not raw:
            return None, None
        ml = normalize_text(m)
        mn = self._menu_opcion_numero(m, max_digit=3)
        if mn == '1' or any(
            w in ml for w in ('nada', 'casi nada', 'poco', 'sedentario', 'no hacemos', 'nos movemos poco')
        ):
            return 'Poco o nada en familia', 'sedentario'
        if mn == '2' or any(
            w in ml
            for w in (
                'alguna',
                'aveces',
                'a veces',
                'de vez',
                'poco a poco',
                'ocasional',
                'fines de semana',
                'fin de semana',
            )
        ):
            return 'Actividad familiar ocasional', 'moderado'
        if mn == '3' or any(
            w in ml for w in ('regular', 'mucho', 'siempre', 'casi todos', 'varios dias', 'a diario', 'entrena')
        ):
            return 'Actividad familiar regular', 'activo'
        if len(raw) > 2:
            return raw[:120], 'moderado'
        return None, None

    def _actualizar_descripcion_grupo_familia(self, data):
        np = data.get('num_personas', '')
        kids = data.get('familia_ninos_menores', 'no')
        lineas = ['Familia de ' + str(np) + ' personas.']
        if kids == 'si':
            lineas.append('Con niños menores de 12 años.')
            nn = data.get('num_ninos', '')
            if nn:
                lineas.append('Número de niños: ' + str(nn) + '.')
            rn = (data.get('restricciones_ninos') or '').strip()
            if rn and normalize_text(rn) not in ('ninguna', 'no', 'nada'):
                lineas.append('Restricciones de menores: ' + rn + '.')
        else:
            lineas.append('Sin niños menores de 12 años en el hogar.')
        data['descripcion_grupo'] = ' '.join(lineas)

    def _pregunta_familia_cuantos_text(self):
        return (
            '¿Cuántas personas sois?\n\n'
            '2️⃣ Dos\n'
            '3️⃣ Tres\n'
            '4️⃣ Cuatro\n'
            '➕ Más de 4'
        )

    def _ensure_grupo_nutricion_defaults(self, data):
        if data.get('personas') == '1 persona':
            return
        if not str(data.get('descripcion_grupo', '')).strip():
            return
        if not data.get('objetivo'):
            data['objetivo'] = 'Comer mas sano'
        if not data.get('actividad'):
            data['actividad'] = '3-4 días por semana'
            data['actividad_tag'] = 'activo'
        if not data.get('cocina'):
            data['cocina'] = 'Poco tiempo, recetas rápidas'
        if not data.get('num_comidas'):
            data['num_comidas'] = '3 veces al día'

    def _descripcion_pareja_actualizada(self, data):
        h = data.get('pareja_horario', '')
        o1 = data.get('objetivo', '')
        o2 = data.get('objetivo_pareja') or o1
        comen = {
            'juntos': 'Comen juntos casi siempre',
            'finde': 'Coinciden sobre todo en cenas o fines de semana',
            'separado': 'Horarios separados pero comparten la compra',
        }.get(h, 'Convivencia: ' + str(h))
        if (data.get('pareja_mismos_objetivos') == 'si') or (o1 == o2):
            return 'Pareja. ' + comen + '. Objetivo compartido: ' + o1 + '.'
        return 'Pareja. ' + comen + '. Persona 1: ' + o1 + '. Persona 2: ' + o2 + '.'

    def _normalizar_perfil_menu(self, data):
        if not str(data.get('restricciones') or '').strip():
            data['restricciones'] = 'Ninguna'

    def _perfil_es_grupo(self, data):
        return bool(data.get('descripcion_grupo', '').strip()) and data.get('personas') != '1 persona'

    def _primer_campo_perfil_faltante(self, data, requiere_bio=False):
        if self._perfil_es_grupo(data):
            for k in ('presupuesto', 'supermercado'):
                if not str(data.get(k) or '').strip():
                    return k
            return None
        for k in ('objetivo', 'cocina', 'presupuesto', 'supermercado'):
            if not str(data.get(k) or '').strip():
                return k
        if requiere_bio:
            for k in ('genero', 'edad', 'peso', 'altura'):
                if not str(data.get(k) or '').strip():
                    return k
        return None

    def _pregunta_campo_perfil(self, campo):
        preguntas = {
            'objetivo': (
                '¿Cuál es tu objetivo nutricional ahora? 🎯\n'
                'Ej.: perder peso, mantener, ganar músculo, más energía…'
            ),
            'cocina': (
                '¿Cómo es tu relación con la cocina? 🍳\n\n'
                '⚡ Poco tiempo, recetas rápidas\n'
                '🛋️ Cocina para vagos\n'
                '👨‍🍳 Me gusta cocinar\n'
                '📦 Batch cooking'
            ),
            'presupuesto': (
                '¿Cuánto quieres gastar a la semana en la compra? 💶\n'
                'Escribe un número en euros, ej: 60'
            ),
            'supermercado': (
                '🏪 ¿En qué supermercado sueles comprar?\n\n'
                '1️⃣ Mercadona  2️⃣ Lidl  3️⃣ Aldi  4️⃣ Carrefour\n'
                '5️⃣ Dia  6️⃣ Consum  7️⃣ Supercor  8️⃣ El Corte Inglés\n\n'
                'O escribe el nombre'
            ),
            'genero': 'Para ajustar calorías: ¿eres hombre o mujer?',
            'edad': '¿Cuántos años tienes?',
            'peso': '¿Cuánto pesas aprox.? (kg)',
            'altura': '¿Cuánto mides? (cm)',
        }
        return preguntas.get(campo, 'Cuéntame ese dato y seguimos 😊')

    def _guardar_campo_perfil_menu(self, u, m, campo):
        data = u['data']
        ml = normalize_text(m)
        raw = (m or '').strip()
        if not raw:
            return False
        if campo == 'presupuesto':
            nums = re.findall(r'\d+', m)
            if nums:
                data['presupuesto'] = nums[0]
                return True
            return False
        if campo == 'supermercado':
            data['supermercado'] = self._supermercado_nombre(m)
            return True
        if campo == 'objetivo':
            data['objetivo'] = raw
            return True
        if campo == 'cocina':
            if raw == '1' or any(w in ml for w in ['poco', 'poca', 'poco tiempo', 'no tengo tiempo', 'rapido', '15 min', '15min']):
                data['cocina'] = 'Poco tiempo, recetas rápidas'
            elif raw == '2' or any(w in ml for w in ['vago', 'vagos', 'precocinado', 'listo', 'facil', 'no me gusta cocinar', 'odio cocinar']):
                data['cocina'] = 'Cocina para vagos'
            elif raw == '3' or any(w in ml for w in ['me gusta', 'gusta', 'cocinar', 'cocino', 'disfruto']):
                data['cocina'] = 'Me gusta cocinar'
            elif raw == '4' or any(w in ml for w in ['batch', 'domingo', 'preparo', 'semana', 'tuppers', 'taper']):
                data['cocina'] = 'Batch cooking'
            else:
                if len(raw) > 3:
                    data['cocina'] = raw
                    return True
                return False
            return True
        if campo == 'genero':
            if any(w in ml for w in ('hombre', 'masculino', 'chico', 'varon')):
                data['genero'] = 'Hombre'
                return True
            if any(w in ml for w in ('mujer', 'femenino', 'chica')):
                data['genero'] = 'Mujer'
                return True
            return False
        if campo == 'edad':
            for n in re.findall(r'\d+', m):
                v = int(n)
                if 14 <= v <= 100:
                    data['edad'] = str(v)
                    return True
            return False
        if campo == 'peso':
            for n in re.findall(r'\d+', m):
                v = int(n)
                if 35 <= v <= 250:
                    data['peso'] = str(v)
                    return True
            return False
        if campo == 'altura':
            for n in re.findall(r'\d+', m):
                v = int(n)
                if 120 <= v <= 230:
                    data['altura'] = str(v)
                    return True
            return False
        return False

    def _arrancar_accion_menu(self, user_id, u, message, accion):
        self._normalizar_perfil_menu(u['data'])
        req_bio = accion == 'plan_semana'
        campo = self._primer_campo_perfil_faltante(u['data'], req_bio)
        if campo:
            u['state'] = 'menu_esperando_perfil'
            u['data']['_menu_accion_pendiente'] = accion
            u['data']['_menu_requiere_bio'] = req_bio
            u['data']['_menu_campo_perfil'] = campo
            return self._pregunta_campo_perfil(campo)
        u['data'].pop('_menu_accion_pendiente', None)
        u['data'].pop('_menu_requiere_bio', None)
        u['data'].pop('_menu_campo_perfil', None)
        return self._ejecutar_accion_menu_principal(user_id, u, message, accion)

    def _ejecutar_accion_menu_principal(self, user_id, u, message, accion):
        data = u['data']
        if accion == 'lista_compra':
            return self._respuesta_lista_compra_openai(u)
        if accion == 'ahorrar':
            u['state'] = 'menu_principal'
            return self._respuesta_ahorrar_openai(u)
        if accion == 'que_tengo':
            u['state'] = 'menu_que_tengo'
            return (
                'Dime qué tienes en la nevera o despensa (o mándame una foto) '
                'y te digo qué puedes comer 🥕📸'
            )
        if accion == 'plan_semana':
            u['state'] = 'plan_listo'
            super_nombre = data.get('supermercado', 'Mercadona')
            mensaje_espera = (
                'Perfecto! 🌿 Estoy preparando tu plan semanal para '
                + super_nombre
                + '. Dame un momento... ⏳'
            )
            msgs = self._generar_plan_partes(data)
            u['plan'] = '\n\n'.join(msgs[1:])
            u['plan_count'] = u.get('plan_count', 0) + 1
            return [mensaje_espera] + msgs
        if accion == 'suplementos':
            u['state'] = 'menu_principal'
            return self._respuesta_suplementos_desde_perfil(u)
        u['state'] = 'menu_principal'
        return self._menu_principal_text(data)

    def _respuesta_lista_compra_openai(self, u):
        data = u['data']
        self._normalizar_perfil_menu(data)
        perfil_usuario = self._profile_for_prompt(data)
        sup = data.get('supermercado', 'Mercadona')
        pres = data.get('presupuesto', '65')
        cocina = data.get('cocina', '')
        prompt = (
            'Eres ZIA nutricionista. Haz la LISTA DE LA COMPRA para TODA UNA SEMANA '
            'alineada con este perfil: '
            + perfil_usuario
            + '. Estilo de cocina: '
            + cocina
            + '. Supermercado habitual: '
            + sup
            + '. Presupuesto orientativo máximo: '
            + str(pres)
            + ' €/semana (respeta precios razonables). '
            'Organiza por zonas del super (fruta/verdura, frescos, despensa). '
            'Cantidades orientativas. Sin menú día a día; solo la lista. '
            'Incluye especias/condimentos básicos si faltan. '
            'Español, emojis, máximo 280 palabras.'
        )
        menu = self._append_menu_footer(data)
        u['state'] = 'menu_principal'
        try:
            r = self.openai.chat.completions.create(
                model=self.config.get('ai', {}).get('model', 'gpt-4o-mini'),
                messages=[
                    {'role': 'system', 'content': COACH_TONE + ' Eres ZIA nutricionista. Responde en español con emojis.'},
                    {'role': 'user', 'content': prompt},
                ],
                max_tokens=700,
                temperature=0.7,
                timeout=28,
            )
            return r.choices[0].message.content + menu
        except Exception:
            return (
                'No pude generar la lista ahora por un error o timeout. Inténtalo en unos minutos.'
                + menu
            )

    def _respuesta_ahorrar_openai(self, u):
        data = u['data']
        self._normalizar_perfil_menu(data)
        perfil_usuario = self._profile_for_prompt(data)
        pres_raw = data.get('presupuesto', '65')
        try:
            pres_max = float(str(pres_raw).replace(',', '.'))
        except (TypeError, ValueError):
            pres_max = 65.0
        if abs(pres_max - round(pres_max)) < 1e-9:
            pres_max_str = str(int(round(pres_max)))
        else:
            pres_max_str = ('%.2f' % pres_max).replace('.', ',').rstrip('0').rstrip(',')
        sup = data.get('supermercado', 'Mercadona')
        cocina = data.get('cocina', '')
        marca_bloque = self._marca_blanca_instruccion_ahorro(sup)
        prompt = (
            'Eres ZIA nutricionista. Genera SOLO la lista de la compra económica pedida, sin introducción ni párrafos extra.\n\n'
            + marca_bloque
            + '\n\n'
            'Perfil nutricional y restricciones: '
            + perfil_usuario
            + '\nEstilo de cocina: '
            + cocina
            + '\n\n'
            'REQUISITOS:\n'
            '- Como máximo 12 productos ESENCIALES (12 líneas como máximo), suficientes para encajar con el objetivo nutricional del perfil.\n'
            '- Cada línea debe nombrar explícitamente la marca blanca indicada arriba (ej. Hacendado arroz integral 1 kg) o, si no aplica, un producto económico de temporada en esa cadena.\n'
            '- Precio aproximado por línea realista en ese supermercado en España (~2025-2026), en € (ej. ~1,20 €).\n'
            '- La suma de los precios aproximados NO puede superar '
            + pres_max_str
            + ' € (presupuesto semanal del usuario). Ajusta cantidades o formatos si hace falta.\n'
            '- Formato por línea: emoji + nombre del producto con marca + cantidad/unidad + precio aprox.\n'
            '- Tras las líneas de producto, añade UNA última línea y solo esta, exactamente en este estilo (sustituye X,X por la suma, comma decimal en español):\n'
            '  💰 Total estimado: X,X€ de tu presupuesto de '
            + pres_max_str
            + '€\n'
            '  La suma debe ser menor o igual a '
            + pres_max_str
            + ' €.\n'
            'PROHIBIDO: recetas, consejos, trucos, comparar con otros supers, markdown, guiones - o * o #, viñetas con cuadrado. '
            'Sin texto antes del primer producto. Respeta alergias y restricciones del perfil.'
        )
        system_ahorro = (
            COACH_TONE + ' Devuelve EXCLUSIVAMENTE la lista: máximo 12 líneas de producto, cada una con emoji + nombre con marca blanca '
            '+ cantidad + precio aprox (~X,XX €), y al final UNA línea: "💰 Total estimado: SUM€ de tu presupuesto de '
            + pres_max_str
            + '€" donde SUM es la suma y no supera ese presupuesto. Sin markdown ni texto extra.'
        )
        menu = self._append_menu_footer(data, ahorro=True)
        try:
            r = self.openai.chat.completions.create(
                model=self.config.get('ai', {}).get('model', 'gpt-4o-mini'),
                messages=[
                    {'role': 'system', 'content': system_ahorro},
                    {'role': 'user', 'content': prompt},
                ],
                max_tokens=750,
                temperature=0.4,
                timeout=28,
            )
            return r.choices[0].message.content + menu
        except Exception:
            return (
                'No pude preparar la lista ahora. Inténtalo en unos minutos.'
                + menu
            )

    def _respuesta_suplementos_desde_perfil(self, u):
        data = u['data']
        self._normalizar_perfil_menu(data)
        perfil_usuario = self._profile_for_prompt(data)
        prompt = (
            'Eres ZIA, experta en suplementación. Recomienda suplementos alineados con este perfil: '
            + perfil_usuario
            + '. Objetivo declarado: '
            + data.get('objetivo', '')
            + '. Restricciones: '
            + data.get('restricciones', 'Ninguna')
            + '.\n'
            'Entrega 4-5 suplementos concretos con: nombre, para qué sirve respecto a su objetivo, '
            'dosis orientativa, mejor momento del día y precio aproximado €/mes en España. '
            'Termina con un aviso breve de consultar a médico o farmacéutico si toma medicación o tiene patologías. '
            'Español, emojis, máximo 260 palabras.'
        )
        menu = self._append_menu_footer(data)
        try:
            r = self.openai.chat.completions.create(
                model=self.config.get('ai', {}).get('model', 'gpt-4o-mini'),
                messages=[
                    {'role': 'system', 'content': COACH_TONE + ' Eres ZIA nutricionista. Responde en español con emojis.'},
                    {'role': 'user', 'content': prompt},
                ],
                max_tokens=750,
                temperature=0.7,
                timeout=28,
            )
            return r.choices[0].message.content + menu
        except Exception:
            return (
                'No pude generar recomendaciones de suplementos ahora. Inténtalo en unos minutos.'
                + menu
            )

    def _restricciones_combinadas_pregunta_text(self):
        return (
            '¿Tienes alguna restricción o intolerancia alimentaria? 🚨\n\n'
            '✅ Ninguna\n'
            '🌱 Vegano/Vegetariano\n'
            '🌾 Sin gluten / Celiaquía\n'
            '🥛 Sin lactosa\n'
            '🥚 Alergia al huevo\n'
            '🥜 Alergia a frutos secos\n'
            '🦐 Alergia al marisco\n'
            '🐟 Sin pescado\n'
            '✏️ Otra opción'
        )

    def _parse_restricciones_combinadas(self, m):
        raw = (m or '').strip()
        if not raw:
            return None
        expanded = (
            raw.replace(' y ', ',')
            .replace(';', ',')
        )
        parts = [p.strip() for p in expanded.split(',') if p.strip()]
        if not parts:
            return None
        tags = []
        explicit_ninguna = False

        def add_tag(label):
            if label not in tags:
                tags.append(label)

        for part in parts:
            p = part.strip()
            ml = normalize_text(p)
            if not p:
                continue
            low = p.lower()
            if low.startswith('otra') and ':' in p:
                rest = p.split(':', 1)[1].strip()
                if rest:
                    add_tag(rest)
                continue
            n = p if re.fullmatch(r'[1-9]', p) else None
            if n == '1' or p == '✅' or ml in ('no', 'nada', 'ninguna', 'ninguno', 'cero'):
                explicit_ninguna = True
                continue
            if n == '2' or '🌱' in p or any(
                w in ml for w in ('vegan', 'vegano', 'vegana', 'vegetarian', 'vegetariano', 'plant based', 'sin carne')
            ):
                add_tag('Vegano/Vegetariano')
                continue
            if n == '3' or '🌾' in p or any(
                w in ml for w in ('gluten', 'celiaco', 'celiaca', 'celiaquia', 'trigo', 'sin gluten')
            ):
                add_tag('Sin gluten / Celiaquía')
                continue
            if n == '4' or '🥛' in p or any(
                k in ml for k in ('lactosa', 'lacteo', 'lacteos', 'sin lactosa', 'leche')
            ):
                add_tag('Sin lactosa')
                continue
            if n == '5' or '🥚' in p or 'huevo' in ml or 'huevos' in ml:
                add_tag('Alergia al huevo')
                continue
            if n == '6' or '🥜' in p or any(
                k in ml
                for k in (
                    'frutos secos',
                    'fruto seco',
                    'nueces',
                    'cacahuete',
                    'cacahuetes',
                    'almendra',
                    'almendras',
                )
            ):
                add_tag('Alergia a frutos secos')
                continue
            if n == '7' or '🦐' in p or any(
                k in ml
                for k in (
                    'marisco',
                    'mariscos',
                    'crustaceo',
                    'crustaceos',
                    'molusco',
                    'moluscos',
                    'gamba',
                    'gambas',
                    'langostino',
                    'langostinos',
                )
            ):
                add_tag('Alergia al marisco')
                continue
            if n == '8' or '🐟' in p or (
                any(k in ml for k in ('sin pescado', 'no pescado', 'no como pescado'))
                or ('pescado' in ml and 'marisco' not in ml and 'alergia' not in ml)
            ):
                add_tag('Sin pescado')
                continue
            if n == '9' or ml in ('otra', 'otro', 'otras', 'otros'):
                continue
            add_tag(p)

        if tags:
            return ', '.join(tags)
        if explicit_ninguna:
            return 'Ninguna'
        ml_all = normalize_text(raw)
        if ml_all in ('no', 'nada', 'ninguna', 'ninguno') or any(
            w in ml_all for w in ('como de todo', 'ninguna alergia', 'sin alergias')
        ):
            return 'Ninguna'
        return None

    def _returning_user_menu_text(self, data):
        return self._menu_principal_body_text()

    def _welcome_plan_text(self, company):
        return (
            'Hola! Soy ZIA, tu asesora nutricional de ' + company + ' 🌿\n\n'
            'En 2 minutos te preparo tu menu semanal personalizado con productos naturales y ecologicos + lista de la compra lista para el carrito 🛒\n\n'
            'Para quien es el plan?\n\n'
            '👤 Plan individual\n'
            '👫 Plan pareja\n'
            '👨‍👩‍👧 Plan familiar'
        )

    def _gpt_libre_same_state(self, message, u, state):
        respuesta = self._gpt_libre(message, u)
        u['state'] = state
        return respuesta

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

    def _sanitize_reply_for_whatsapp(self, out):
        if isinstance(out, list):
            return [self._sanitize_reply_for_whatsapp(x) for x in out]
        if isinstance(out, str):
            return sanitize_whatsapp_text(out)
        return out

    def process_message(self, user_id, message, plan_type='pro'):
        try:
            out = self._process_message_impl(user_id, message, plan_type)
            return self._sanitize_reply_for_whatsapp(out)
        finally:
            key = self._user_key(user_id)
            if key in self._users:
                self._save_user(user_id, self._users[key])

    def _process_message_impl(self, user_id, message, plan_type='pro'):
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
        s = str(u.get('state') or 'welcome').strip().lower()
        u['state'] = s
        if s in [
            'menu_principal',
            'plan_listo',
            'menu_que_tengo',
            'menu_esperando_perfil',
        ] and normalize_text(m) in [
            'hola', 'buenas', 'buenos dias', 'buenas tardes', 'buenas noches', 'inicio', 'menu',
        ]:
            u['state'] = 'menu_principal'
            u['data'].pop('_menu_accion_pendiente', None)
            u['data'].pop('_menu_requiere_bio', None)
            u['data'].pop('_menu_campo_perfil', None)
            return self._returning_user_menu_text(u.get('data', {}))
        if s == 'welcome':
            u['state'] = 'tipo_plan'
            return self._welcome_plan_text(company)
        elif s == 'tipo_plan':
            ml = normalize_text(m)
            mn = self._menu_opcion_numero(m, max_digit=3)
            if (
                mn == '3'
                or 'familia' in ml
                or 'familiar' in ml
                or 'mis hijos' in ml
                or 'somos 3' in ml
                or 'somos 4' in ml
                or 'somos 5' in ml
                or 'somos 6' in ml
                or 'plan familiar' in ml
                or '👨‍👩‍👧' in (m or '')
            ):
                u['data']['personas'] = 'plan familiar'
                u['data'].pop('num_personas', None)
                u['data']['_familia_detalle'] = True
                u['state'] = 'familia_cuantos'
                return self._pregunta_familia_cuantos_text()
            if (
                mn == '2'
                or 'somos 2' in ml
                or 'pareja' in ml
                or '👫' in (m or '')
                or ('amigo' in ml and 'familiar' not in ml)
                or ('dos' in ml and 'personas' in ml and 'familiar' not in ml)
            ):
                u['data']['personas'] = '2 personas'
                u['data']['num_personas'] = 2
                u['state'] = 'datos_pareja'
                return 'Perfecto 👫 Dos preguntas rápidas:\n\n¿Coméis juntos normalmente o tenéis horarios distintos?\n\n1️⃣ Comemos juntos casi siempre\n2️⃣ Solo coincidimos en cenas o fines de semana\n3️⃣ Cada uno come por su lado pero compartimos compra'
            if (
                mn == '1'
                or 'individual' in ml
                or 'yo solo' in ml
                or 'una persona' in ml
                or '1 persona' in ml
                or ml == 'mi'
                or '👤' in (m or '')
                or ('solo' in ml and 'pareja' not in ml and 'familiar' not in ml and 'somos' not in ml)
            ):
                u['data']['personas'] = '1 persona'
                u['data']['num_personas'] = 1
                u['state'] = 'datos'
                return (
                    'Perfecto. Para empezar necesito conocerte:\n\n'
                    '👉 Nombre, género, edad, peso (kg) y altura (cm)\n\n'
                    'Ejemplo: Maria, mujer, 34, 65kg, 165cm'
                )
            return '¡Casi! ¿Cuál de estas se parece más a ti? 😊\n\n👤 Solo para mi\n👫 Para 2 personas\n👨‍👩‍👧‍👦 Familiar (3 o mas personas)'
        elif s == 'datos_pareja':
            ml = normalize_text(m)
            mn = self._menu_opcion_numero(m, max_digit=3)
            if mn == '1' or 'juntos' in ml or ('siempre' in ml and 'no siempre' not in ml and 'no todos' not in ml):
                u['data']['pareja_horario'] = 'juntos'
            elif mn == '2' or 'cenas' in ml or 'finde' in ml or 'fin de semana' in ml:
                u['data']['pareja_horario'] = 'finde'
            elif mn == '3' or 'separado' in ml or 'cada uno' in ml or 'por su lado' in ml:
                u['data']['pareja_horario'] = 'separado'
            else:
                return '¡Casi! ¿Cuál de estas se parece más a ti? 😊\n\n1️⃣ Comemos juntos casi siempre\n2️⃣ Solo coincidimos en cenas o fines de semana\n3️⃣ Cada uno come por su lado pero compartimos compra'
            u['state'] = 'pareja_mismos_objetivos'
            return (
                '¿Tenéis los mismos objetivos?\n\n'
                '✅ Sí, los mismos\n'
                '🔀 No, son diferentes'
            )
        elif s == 'pareja_horario':
            u['state'] = 'pareja_mismos_objetivos'
            return (
                'Seguimos con unas preguntas más rápidas 👇\n\n'
                '¿Tenéis los mismos objetivos?\n\n'
                '✅ Sí, los mismos\n'
                '🔀 No, son diferentes'
            )
        elif s == 'pareja_mismos_objetivos':
            ml = normalize_text(m)
            mn = self._menu_opcion_numero(m, max_digit=2)
            if mn == '1' or ml in ('si', 'sí', 'mismos', 'mismo', 'iguales', 'igual', 'los mismos', 'misma', 'claro') or '✅' in (m or ''):
                u['data']['pareja_mismos_objetivos'] = 'si'
                u['state'] = 'pareja_objetivo_shared'
                return self._texto_pregunta_objetivo_onboarding()
            if mn == '2' or ml in ('no', 'distintos', 'distinto', 'diferentes', 'diferente') or '🔀' in (m or ''):
                u['data']['pareja_mismos_objetivos'] = 'no'
                u['state'] = 'pareja_objetivo_a'
                return 'Objetivo de la primera persona 🎯\n\n' + self._texto_opciones_objetivo()
            return (
                '¿Tenéis los mismos objetivos?\n\n'
                '✅ Sí, los mismos\n'
                '🔀 No, son diferentes'
            )
        elif s == 'pareja_objetivo_shared':
            elegido = self._parse_objetivo_opcion(m)
            if not elegido and len((m or '').strip()) >= 3:
                elegido = (m or '').strip()[:200]
            if not elegido:
                return (
                    '¡Casi! Elige una opción 😊\n\n'
                    + self._texto_opciones_objetivo()
                )
            u['data']['objetivo'] = elegido
            u['data']['objetivo_pareja'] = elegido
            u['data']['descripcion_grupo'] = self._descripcion_pareja_actualizada(u['data'])
            u['state'] = 'presupuesto'
            return (
                'Perfecto. ¿Cuánto queréis gastar a la semana en la compra? 💶\n\n'
                'Escribe la cantidad en euros, ej: 60'
            )
        elif s == 'pareja_objetivo_a':
            elegido = self._parse_objetivo_opcion(m)
            if not elegido and len((m or '').strip()) >= 3:
                elegido = (m or '').strip()[:200]
            if not elegido:
                return (
                    '¡Casi! Elige una opción 😊\n\n'
                    + self._texto_opciones_objetivo()
                )
            u['data']['objetivo'] = elegido
            u['state'] = 'pareja_objetivo_b'
            return 'Objetivo de la segunda persona 🎯\n\n' + self._texto_opciones_objetivo()
        elif s == 'pareja_objetivo_b':
            elegido = self._parse_objetivo_opcion(m)
            if not elegido and len((m or '').strip()) >= 3:
                elegido = (m or '').strip()[:200]
            if not elegido:
                return (
                    '¡Casi! Elige una opción 😊\n\n'
                    + self._texto_opciones_objetivo()
                )
            u['data']['objetivo_pareja'] = elegido
            u['data']['descripcion_grupo'] = self._descripcion_pareja_actualizada(u['data'])
            u['state'] = 'presupuesto'
            return (
                'Perfecto. ¿Cuánto queréis gastar a la semana en la compra? 💶\n\n'
                'Escribe la cantidad en euros, ej: 60'
            )
        elif s == 'datos_familia':
            u['data']['_familia_detalle'] = True
            u['state'] = 'familia_cuantos'
            return (
                'Vale, vamos con el formato nuevo 👇\n\n'
                + self._pregunta_familia_cuantos_text()
            )
        elif s == 'familia_cuantos':
            ml = normalize_text(m)
            raw = (m or '').strip()
            n = None
            mas_de_cuatro = (
                '➕' in (m or '')
                or 'mas de 4' in ml
                or 'más de 4' in ml
                or 'mas cuatro' in ml
                or raw in ('+', '5+')
                or ml == '5'
            )
            if mas_de_cuatro:
                if raw.isdigit() and int(raw) >= 5:
                    n = int(raw)
                else:
                    n = 5
            elif '2️⃣' in (m or '') or raw == '2' or (ml == 'dos' and 'tres' not in ml and 'cuatro' not in ml):
                n = 2
            elif '3️⃣' in (m or '') or raw == '3' or ml == 'tres':
                n = 3
            elif '4️⃣' in (m or '') or raw == '4' or ml == 'cuatro':
                n = 4
            elif raw.isdigit():
                v = int(raw)
                if 2 <= v <= 4:
                    n = v
                elif v >= 5:
                    n = v
            if not n:
                return '¡Casi! Indica cuántas personas sois 😊\n\n' + self._pregunta_familia_cuantos_text()
            u['data']['num_personas'] = n
            u['data']['personas'] = 'familia (' + str(n) + ' personas)'
            u['state'] = 'familia_ninos'
            return (
                '¿Hay niños menores de 12 años?\n\n'
                '👶 Sí\n'
                '🙅 No'
            )
        elif s == 'familia_ninos':
            ml = normalize_text(m)
            mn = self._menu_opcion_numero(m, max_digit=2)
            if any(w in ml for w in ('no hay ninos', 'no hay niños', 'sin ninos', 'sin niños', 'sin hijos', 'solo adultos', 'no tenemos hijos')):
                no = True
                si = False
            else:
                si = (
                    mn == '1'
                    or ml in ('si', 'sí', 'sii', 'sip')
                    or '👶' in (m or '')
                    or (len(ml) >= 2 and ml.startswith('si') and not ml.startswith('sin'))
                )
                no = mn == '2' or ml in ('no', 'nop', 'nope') or '🙅' in (m or '')
            if not si and not no:
                if any(
                    w in ml
                    for w in (
                        'nino',
                        'niño',
                        'ninos',
                        'niños',
                        'hijo',
                        'hijos',
                        'menor',
                        'peque',
                        'peques',
                        'bebe',
                        'bebé',
                    )
                ):
                    si = True
                elif any(w in ml for w in ('no hay', 'sin hijos', 'solo adultos', 'no tenemos hijos')):
                    no = True
            if not si and not no:
                return (
                    '¡Casi! ¿Hay niños menores de 12 años?\n\n'
                    '👶 Sí\n'
                    '🙅 No'
                )
            u['data']['familia_ninos_menores'] = 'si' if si else 'no'
            if si:
                u['state'] = 'familia_num_ninos'
                return self._texto_familia_num_ninos()
            u['data']['num_ninos'] = '0'
            u['data']['restricciones_ninos'] = 'Ninguna'
            self._actualizar_descripcion_grupo_familia(u['data'])
            u['state'] = 'objetivo'
            return self._texto_pregunta_objetivo_familia()
        elif s == 'familia_num_ninos':
            nn = self._parse_familia_num_ninos(m)
            if not nn:
                return '¡Casi! Indica cuántos niños hay 😊\n\n' + self._texto_familia_num_ninos()
            u['data']['num_ninos'] = nn
            u['state'] = 'familia_restricciones_ninos'
            return self._texto_familia_restricciones_ninos()
        elif s == 'familia_restricciones_ninos':
            r = self._parse_restricciones_ninos(m)
            if r is None:
                return '¡Casi! ¿Alguna restricción entre los peques?\n\n' + self._texto_familia_restricciones_ninos()
            u['data']['restricciones_ninos'] = r
            self._actualizar_descripcion_grupo_familia(u['data'])
            u['state'] = 'objetivo'
            return self._texto_pregunta_objetivo_familia()
        elif s == 'familia_actividad':
            act, tag = self._parse_familia_actividad(m)
            if not act:
                return '¡Casi! ¿Cuál se acerca más? 😊\n\n' + self._texto_familia_actividad()
            u['data']['actividad'] = act
            u['data']['actividad_tag'] = tag or 'moderado'
            u['state'] = 'cocina'
            return (
                'Perfecto 🙌\n\n'
                + '¿Cómo es tu relación con la cocina? 🍳\n\n'
                '⚡ Poco tiempo, recetas rápidas\n'
                '🛋️ Cocina para vagos\n'
                '👨‍🍳 Me gusta cocinar\n'
                '📦 Batch cooking'
            )
        elif s == 'datos':
            parsed = parse_datos(m)
            missing = faltan_datos(parsed)
            if missing:
                return (
                    'Solo me falta: '
                    + ', '.join(missing)
                    + '\n\nEjemplo: Carlos, hombre, 38, 82kg, 178cm'
                )
            for k, v in parsed.items():
                u['data'][k] = v
            nombre = u['data'].get('nombre', '')
            if u['data'].get('personas'):
                u['state'] = 'objetivo'
                if self._es_plan_familia(u['data']):
                    return self._texto_pregunta_objetivo_familia()
                return self._texto_pregunta_objetivo_onboarding()
            u['state'] = 'personas'
            return 'Perfecto' + (', ' + nombre if nombre else '') + '! 💪\n\nEl plan nutricional es para...\n\n  👤 Solo para mi\n  👫 Para 2 personas (pareja o amigo/a)\n  👨‍👩‍👧‍👦 Familiar (3 o mas personas)'
        elif s == 'personas':
            ml = m.strip().lower()
            mn = self._menu_opcion_numero(m, max_digit=3)
            opts = {'1': '1 persona', '2': '2 personas', '3': 'familia (3 o mas personas)'}
            elegido = opts.get(mn) if mn else None
            if not elegido:
                elegido = opts.get(m.strip(), None)
            if not elegido:
                if any(
                    w in ml
                    for w in (
                        'solo para mi',
                        'solo yo',
                        'yo solo',
                        'una persona',
                    )
                ) or ml in ('solo', 'mi', '1 persona'):
                    elegido = '1 persona'
                elif any(w in ml for w in ('pareja', 'dos personas', 'somos 2', 'amigos', 'amigas')):
                    elegido = '2 personas'
                elif any(w in ml for w in ('familia', 'familiar', 'somos 3', 'somos 4', 'hijos')):
                    elegido = 'familia (3 o mas personas)'
            if not elegido:
                return '¡Casi! ¿Cuál de estas se parece más a ti? 😊\n\n👤 Solo para mi\n👫 Para 2 personas\n👨‍👩‍👧‍👦 Familiar (3 o mas personas)'
            u['data']['personas'] = elegido
            if elegido == '1 persona':
                u['data']['num_personas'] = 1
            elif elegido == '2 personas':
                u['data']['num_personas'] = 2
            else:
                u['data']['num_personas'] = 4
                u['data']['familia_ninos_menores'] = 'no'
                u['data']['num_ninos'] = '0'
                u['data']['restricciones_ninos'] = 'Ninguna'
                u['data']['descripcion_grupo'] = 'Familia de 4 personas indicadas en el perfil.'
            u['state'] = 'objetivo'
            if self._es_plan_familia(u['data']):
                return self._texto_pregunta_objetivo_familia()
            return self._texto_pregunta_objetivo_onboarding()
        elif s == 'objetivo':
            es_fam = self._es_plan_familia(u['data'])
            if es_fam:
                elegido = self._parse_objetivos_familia_multiples(m)
            else:
                elegido = self._parse_objetivo_opcion(m)
                if not elegido and len((m or '').strip()) >= 3:
                    elegido = (m or '').strip()[:200]
            if not elegido:
                return '¡Casi! ¿Cuál de estas se parece más a ti? 😊\n\n' + self._texto_opciones_objetivo()
            u['data']['objetivo'] = elegido
            if es_fam:
                u['state'] = 'familia_actividad'
                return self._texto_familia_actividad()
            u['state'] = 'pasos'
            return '¿Cuánto ejercicio haces? 🏃\n1️⃣ Nada o casi nada\n2️⃣ 1-2 días por semana\n3️⃣ 3-4 días por semana\n4️⃣ Todos los días'
        elif s == 'pasos':
            if self._es_plan_familia(u['data']):
                u['state'] = 'familia_actividad'
                return self._texto_familia_actividad()
            ml = normalize_text(m)
            actividad = None
            tag = None
            respuesta = None
            mn = self._menu_opcion_numero(m, max_digit=4)
            if mn == '1' or m.strip() == '1' or any(w in ml for w in ['nada', 'casi nada', 'poco', 'sedentario', 'no hago']):
                actividad = 'Nada o casi nada'
                tag = 'sedentario'
                respuesta = 'Tranquilo/a, empezamos desde donde estás 🙌 Con pequeños cambios en tu alimentación vas a notar la diferencia enseguida.'
            elif mn == '2' or m.strip() == '2' or any(w in ml for w in ['1-2', '1 2', '1 dia', '2 dias', 'uno', 'dos', 'alguna vez', 'moderado']):
                actividad = '1-2 días por semana'
                tag = 'moderado'
                respuesta = 'Bien 👟 Ya hay movimiento. Vamos a potenciarlo con la alimentación correcta.'
            elif mn == '4' or m.strip() == '4' or any(w in ml for w in ['todos', 'diario', 'cada dia', 'a diario', 'siempre', 'muy activo']):
                actividad = 'Todos los días'
                tag = 'muy_activo'
                respuesta = '💪 Eres una máquina. Vamos a trabajar en rendimiento y recuperación.'
            elif mn == '3' or m.strip() == '3' or any(w in ml for w in ['3-4', '3 4', '3 dias', '4 dias', 'tres', 'cuatro', 'activo']):
                actividad = '3-4 días por semana'
                tag = 'activo'
                respuesta = '🔥 Buen ritmo. Vamos a optimizar tu nutrición para que cada paso cuente más.'
            if not actividad:
                return '¡Casi! ¿Cuál de estas se parece más a ti? 😊\n\n1️⃣ Nada o casi nada\n2️⃣ 1-2 días por semana\n3️⃣ 3-4 días por semana\n4️⃣ Todos los días'
            u['data']['actividad'] = actividad
            u['data']['actividad_tag'] = tag
            u['state'] = 'cocina'
            return respuesta + '\n\n' + '¿Cómo es tu relación con la cocina? 🍳\n\n⚡ Poco tiempo, recetas rápidas\n🛋️ Cocina para vagos\n👨‍🍳 Me gusta cocinar\n📦 Batch cooking'
        elif s == 'cocina':
            ml = normalize_text(m)
            elegido = None
            mn = self._menu_opcion_numero(m, max_digit=4)
            if mn == '1' or m.strip() == '1' or any(w in ml for w in ['poco', 'poca', 'poco tiempo', 'no tengo tiempo', 'rapido', '15 min', '15min']):
                elegido = 'Poco tiempo, recetas rápidas'
            elif mn == '2' or m.strip() == '2' or any(w in ml for w in ['vago', 'vagos', 'precocinado', 'listo', 'facil', 'no me gusta cocinar', 'odio cocinar']):
                elegido = 'Cocina para vagos'
            elif mn == '3' or m.strip() == '3' or any(w in ml for w in ['me gusta', 'gusta', 'cocinar', 'cocino', 'bien', 'disfruto']):
                elegido = 'Me gusta cocinar'
            elif mn == '4' or m.strip() == '4' or any(w in ml for w in ['batch', 'domingo', 'preparo', 'semana', 'tuppers', 'taper']):
                elegido = 'Batch cooking'
            if not elegido:
                return '¡Casi! ¿Cuál de estas se parece más a ti? 😊\n\n⚡ Poco tiempo, recetas rápidas\n🛋️ Cocina para vagos\n👨‍🍳 Me gusta cocinar\n📦 Batch cooking'
            u['data']['cocina'] = elegido
            u['state'] = 'num_comidas'
            return '¿Cuántas veces comes al día? 🍽️\n\n☀️ 2 veces al día\n🌤️ 3 veces al día\n⛅ 4-5 veces con snacks\n🌙 Ayuno intermitente'
        elif s == 'num_comidas':
            ml = normalize_text(m)
            elegido = None
            mn = self._menu_opcion_numero(m, max_digit=4)
            if mn == '1' or m.strip() == '1' or any(w in ml for w in ['2 veces', 'dos', 'poco', 'pocas', 'salto desayuno']) or '☀️' in m:
                elegido = '2 veces al día'
            elif mn == '2' or m.strip() == '2' or any(w in ml for w in ['3 veces', 'tres', 'normal', 'desayuno comida cena']) or '🌤️' in m:
                elegido = '3 veces al día'
            elif mn == '4' or m.strip() == '4' or any(w in ml for w in ['ayuno', 'intermitente', '16/8', '16 8']) or '🌙' in m:
                elegido = 'Ayuno intermitente'
            elif mn == '3' or m.strip() in ['3', '5'] or any(w in ml for w in ['4 veces', '5 veces', 'snack', 'picoteo', 'merienda', 'muchas']) or '⛅' in m:
                elegido = '4-5 veces con snacks'
            if not elegido:
                return '¡Casi! ¿Cuál de estas se parece más a ti? 😊\n\n☀️ 2 veces al día\n🌤️ 3 veces al día\n⛅ 4-5 veces con snacks\n🌙 Ayuno intermitente'
            u['data']['num_comidas'] = elegido
            u['state'] = 'restricciones'
            return self._restricciones_combinadas_pregunta_text()
        elif s == 'restricciones':
            parsed = self._parse_restricciones_combinadas(m)
            if not parsed:
                return (
                    '¡Casi! ¿Cuál de estas te aplica? 😊\n\n'
                    + self._restricciones_combinadas_pregunta_text()
                )
            u['data']['restricciones'] = parsed
            u['state'] = 'presupuesto'
            return (
                'Cuanto quieres gastar a la semana en la compra?\n\n'
                'Escribe la cantidad en euros, ej: 60'
            )
        elif s == 'presupuesto':
            nums = re.findall(r'\d+', m)
            u['data']['presupuesto'] = nums[0] if nums else '65'
            u['state'] = 'supermercado'
            return self._texto_pregunta_supermercado_onboarding()
        elif s == 'supermercado':
            av = self._aviso_supermercado_debe_ser_uno(m)
            if av:
                return av
            super_nombre = self._supermercado_nombre(m)
            u['data']['supermercado'] = super_nombre
            u['data'].pop('_familia_detalle', None)
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
            av = self._aviso_supermercado_debe_ser_uno(m)
            if av:
                return av
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
                av = self._aviso_supermercado_debe_ser_uno(m)
                if av:
                    return av
                return self._gpt_libre(message if isinstance(message, dict) else m, u)
            campo = u['data'].get('_menu_campo_perfil')
            accion = u['data'].get('_menu_accion_pendiente')
            req_bio = u['data'].get('_menu_requiere_bio', False)
            if not campo or not accion:
                u['state'] = 'menu_principal'
                return self._menu_principal_text(u['data'])
            av = self._aviso_supermercado_debe_ser_uno(m)
            if campo == 'supermercado' and av:
                return av
            if not self._guardar_campo_perfil_menu(u, m, campo):
                return (
                    self._pregunta_campo_perfil(campo)
                    + '\n\n_No lo he pillado bien, ¿lo repetimos?_ 😊'
                )
            self._normalizar_perfil_menu(u['data'])
            siguiente = self._primer_campo_perfil_faltante(u['data'], req_bio)
            if siguiente:
                u['data']['_menu_campo_perfil'] = siguiente
                return self._pregunta_campo_perfil(siguiente)
            u['data'].pop('_menu_campo_perfil', None)
            accion_final = u['data'].pop('_menu_accion_pendiente', None)
            u['data'].pop('_menu_requiere_bio', None)
            if not accion_final:
                u['state'] = 'menu_principal'
                return self._menu_principal_text(u['data'])
            return self._ejecutar_accion_menu_principal(user_id, u, message, accion_final)
        elif s == 'menu_que_tengo':
            text, image_url = self._retail_text_and_image_url(message)
            if not image_url and isinstance(message, dict):
                for key in ('MediaUrl0', 'media_url', 'imageUrl', 'image_url'):
                    if message.get(key):
                        image_url = message.get(key)
                        break
            if image_url:
                u['state'] = 'esperando_foto_nevera'
                return self.process_message(user_id, message)
            data = u['data']
            user_txt = (text or m).strip()
            if not user_txt:
                return 'Cuéntame qué ingredientes tienes o mándame una foto 📸'
            self._normalizar_perfil_menu(data)
            perfil_usuario = self._profile_for_prompt(data)
            prompt = (
                'Eres ZIA nutricionista. El usuario solo tiene estos alimentos en casa: '
                + user_txt
                + '. Propón 3 comidas posibles usando sobre todo esos ingredientes '
                '(aceite, sal y especias básicas sí puedes asumirlos). '
                'Perfil: '
                + perfil_usuario
                + '. Respeta las restricciones y alergias del perfil al pie de la letra. '
                'Recetas ≤25 min. Tono cercano. Español con emojis.'
            )
            menu = self._append_menu_footer(data)
            u['state'] = 'menu_principal'
            try:
                r = self.openai.chat.completions.create(
                    model=self.config.get('ai', {}).get('model', 'gpt-4o-mini'),
                    messages=[
                        {'role': 'system', 'content': COACH_TONE + ' Eres ZIA nutricionista. Responde en español con emojis.'},
                        {'role': 'user', 'content': prompt},
                    ],
                    max_tokens=650,
                    temperature=0.7,
                    timeout=28,
                )
                return r.choices[0].message.content + menu
            except Exception:
                return (
                    'No pude proponerte ideas ahora. Inténtalo en unos minutos.'
                    + menu
                )
        elif s == 'menu_principal':
            ml = normalize_text(m)
            data = u['data']
            self._normalizar_perfil_menu(data)
            mn = self._menu_opcion_numero(m)
            if mn == '1' or (
                mn is None and (
                    'lista de la compra' in ml
                    or ('lista' in ml and 'plan' not in ml and 'semanal' not in ml)
                    or ('compra' in ml and 'ahorr' not in ml and 'preparada' not in ml and 'facil' not in ml)
                    or 'carrito' in ml
                )
            ):
                return self._arrancar_accion_menu(user_id, u, message, 'lista_compra')
            if mn == '2' or (
                mn is None and any(
                    x in ml for x in ('ahorr', 'barato', 'econo', 'gastar menos', 'poco dinero')
                )
            ):
                return self._arrancar_accion_menu(user_id, u, message, 'ahorrar')
            if mn == '3' or (
                mn is None and any(
                    x in ml
                    for x in (
                        'con lo que tengo',
                        'lo que tengo',
                        'que tengo',
                        'qué tengo',
                        'que como',
                        'qué como',
                        'despensa',
                        'nevera',
                        'ingredientes',
                        'tengo en casa',
                    )
                )
            ):
                return self._arrancar_accion_menu(user_id, u, message, 'que_tengo')
            if mn == '4' or (
                mn is None and (
                    any(
                        x in ml
                        for x in (
                            'planifica',
                            'planificame',
                            'plan semanal',
                            'menu semanal',
                            'organiza la semana',
                        )
                    )
                    or ('plan' in ml and 'semanal' in ml)
                )
            ):
                return self._arrancar_accion_menu(user_id, u, message, 'plan_semana')
            if mn == '5' or (
                mn is None and any(x in ml for x in ('suplement', 'vitamina', 'proteina', 'proteína'))
            ):
                return self._arrancar_accion_menu(user_id, u, message, 'suplementos')
            if mn is None and any(
                x in ml for x in ('comida preparada', 'precocinado', 'lista para comer', 'compra facil')
            ):
                u['state'] = 'compra_mercadona'
                return self.process_message(user_id, 'comida preparada lista para comer')
            if mn is None and any(
                x in ml for x in ('mejorar aliment', 'reset', 'finde', 'evento', 'boda', 'progreso semanal')
            ):
                u['state'] = 'mejorar'
                return '¿Qué quieres mejorar? 👇\n\n1️⃣ 📅 Plan semanal completo\n2️⃣ 😅 Me he pasado el finde, quiero resetear\n3️⃣ 🎯 Tengo un evento en X días\n4️⃣ 🥗 Dieta específica (keto, vegana, colesterol...)\n5️⃣ 📊 Mi progreso semanal'
            return self._gpt_libre_same_state(message if isinstance(message, dict) else m, u, 'menu_principal')
        if s == 'compra_rapida' or u.get('state') == 'compra_rapida':
            return self._respuesta_lista_compra_openai(u)
        elif s == 'compra_mercadona' or u.get('state') == 'compra_mercadona':
            data = u['data']
            perfil_usuario = self._profile_for_prompt(data)
            prompt = (
                'Eres ZIA nutricionista. El usuario quiere comida preparada lista para comer del supermercado, sin cocinar.\n'
                'Recomienda 12-15 productos concretos listos para comer que encuentras en cualquier supermercado español:\n'
                '- Cremas de verduras tetra brik (calabaza, zanahoria, puerro)\n'
                '- Tortilla de patatas hecha\n'
                '- Arroces y quinoas Brillante (1 min microondas)\n'
                '- Pollo asado o pechuga envasada lista\n'
                '- Ensaladas bolsa con proteína\n'
                '- Hummus y guacamole listos\n'
                '- Gazpacho y salmorejo tetra brik\n'
                '- Legumbres cocidas en bote\n'
                '- Latas de atún, sardinas, mejillones\n'
                '- Yogures proteicos\n'
                '- Fruta lavada lista (fresas, arándanos, uvas)\n'
                '- Frutos secos en bolsita\n'
                'Organiza por secciones con emoji y precio orientativo.\n'
                'Adapta a restricciones del usuario: ' + perfil_usuario + '.\n'
                'Tono cercano y práctico. Emojis. Máximo 200 palabras.\n'
                "Al final: 'Todo listo en menos de 5 minutos 🚀'"
            )
            menu = self._append_menu_footer(data)
            u['state'] = 'menu_principal'
            try:
                r = self.openai.chat.completions.create(
                    model=self.config.get('ai', {}).get('model', 'gpt-4o-mini'),
                    messages=[
                        {'role': 'system', 'content': COACH_TONE + ' Eres ZIA nutricionista. Responde en español con emojis.'},
                        {'role': 'user', 'content': prompt},
                    ],
                    max_tokens=500,
                    temperature=0.7,
                    timeout=25,
                )
                return r.choices[0].message.content + menu
            except Exception as e:
                return 'No pude generar la compra fácil de Mercadona por un error o timeout. Inténtalo de nuevo en unos minutos.' + self._append_menu_footer(data)
        elif s == 'mejorar':
            ml = normalize_text(m)
            if m.strip() == '1' or 'plan' in ml or 'semanal' in ml:
                super_nombre = u['data'].get('supermercado', 'Mercadona')
                u['state'] = 'plan_listo'
                mensaje_espera = 'Perfecto! 🌿 Estoy preparando tu plan semanal completo para ' + super_nombre + '. Dame un momento... ⏳'
                msgs = self._generar_plan_partes(u['data'])
                u['plan'] = '\n\n'.join(msgs[1:])
                u['plan_count'] = u.get('plan_count', 0) + 1
                return [mensaje_espera] + msgs
            if m.strip() == '2' or 'pasado' in ml or 'reset' in ml or 'finde' in ml:
                u['state'] = 'mejorar_reset'
                return '¡Oye, que un día no define tu camino! 😊 Lo importante es que quieres volver y eso ya es mucho.\n\nCuéntame, ¿qué ha pasado? Sin juicios 🙌'
            if m.strip() == '3' or 'evento' in ml or 'boda' in ml or 'viaje' in ml:
                u['state'] = 'mejorar_evento'
                return (
                    '¡Qué emocionante! 🎯 ¿Para cuándo es el evento y qué quieres conseguir?\n'
                    'Ejemplo: boda en 3 semanas, quiero perder 3kg'
                )
            if m.strip() == '4' or 'dieta' in ml or 'keto' in ml or 'vegana' in ml or 'colesterol' in ml:
                u['state'] = 'mejorar_dieta'
                return '¿Qué tipo de dieta quieres? 🥗\n1️⃣ Keto\n2️⃣ Vegana\n3️⃣ Mediterránea\n4️⃣ Ayuno 16:8\n5️⃣ Vegetariana\n6️⃣ Colesterol bajo'
            if m.strip() == '5' or 'progreso' in ml:
                u['state'] = 'mejorar_progreso'
                return 'Cuéntame cómo te has sentido esta semana 💬 ¿Seguiste el plan? ¿Energía, digestión, ánimo?'
            return self._gpt_libre_same_state(message if isinstance(message, dict) else m, u, 'mejorar')
        elif s == 'eligiendo_super':
            av = self._aviso_supermercado_debe_ser_uno(m)
            if av:
                return av
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
        elif s == 'que_como':
            text, image_url = self._retail_text_and_image_url(message)
            if not image_url and isinstance(message, dict):
                for key in ('MediaUrl0', 'media_url', 'imageUrl', 'image_url'):
                    if message.get(key):
                        image_url = message.get(key)
                        break
            if image_url:
                u['state'] = 'esperando_foto_nevera'
                return self.process_message(user_id, message)
            data = u['data']
            perfil_usuario = self._profile_for_prompt(data)
            prompt = (
                'Eres ZIA nutricionista. El usuario quiere decidir qué comer o cenar ahora y dice: '
                + (text or m).strip()
                + '. Propón 3 opciones rápidas adaptadas a su perfil: '
                + perfil_usuario
                + '. Deben ser realistas, de menos de 20 minutos, con ingredientes simples, cantidades orientativas y tono cercano. Responde en español con emojis.'
            )
            menu = self._append_menu_footer(data)
            u['state'] = 'menu_principal'
            try:
                r = self.openai.chat.completions.create(
                    model=self.config.get('ai', {}).get('model', 'gpt-4o-mini'),
                    messages=[
                        {'role': 'system', 'content': COACH_TONE + ' Eres ZIA nutricionista. Responde en español con emojis.'},
                        {'role': 'user', 'content': prompt},
                    ],
                    max_tokens=600,
                    temperature=0.7,
                    timeout=25,
                )
                return r.choices[0].message.content + menu
            except Exception as e:
                return 'No pude proponerte opciones ahora mismo por un error o timeout. Inténtalo de nuevo en unos minutos.' + self._append_menu_footer(data)
        elif s == 'mejorar_reset':
            data = u['data']
            prompt = (
                'Eres ZIA coach nutricional cercana. El usuario dice: '
                + m.strip()
                + '. Responde sin juzgar, con empatía y humor suave. Da un plan reset de 2 días muy concreto con desayuno, comida y cena para volver a la rutina. Máximo 150 palabras. Emojis.'
            )
            menu = self._append_menu_footer(data)
            u['state'] = 'menu_principal'
            try:
                r = self.openai.chat.completions.create(
                    model=self.config.get('ai', {}).get('model', 'gpt-4o-mini'),
                    messages=[
                        {'role': 'system', 'content': COACH_TONE + ' Eres ZIA nutricionista. Responde en español con emojis.'},
                        {'role': 'user', 'content': prompt},
                    ],
                    max_tokens=650,
                    temperature=0.7,
                    timeout=25,
                )
                return r.choices[0].message.content + menu
            except Exception as e:
                return self._gpt_libre_same_state(message if isinstance(message, dict) else m, u, 'mejorar_reset')
        elif s == 'mejorar_evento':
            data = u['data']
            prompt = (
                'Eres ZIA coach nutricional. El usuario tiene este evento: '
                + m.strip()
                + '. Crea un plan detallado y motivador con: objetivo diario de calorías, alimentos que debe priorizar, alimentos que debe evitar, consejo de hidratación, y frase motivacional final. Máximo 200 palabras. Emojis.'
            )
            menu = self._append_menu_footer(data)
            u['state'] = 'menu_principal'
            try:
                r = self.openai.chat.completions.create(
                    model=self.config.get('ai', {}).get('model', 'gpt-4o-mini'),
                    messages=[
                        {'role': 'system', 'content': COACH_TONE + ' Eres ZIA nutricionista. Responde en español con emojis.'},
                        {'role': 'user', 'content': prompt},
                    ],
                    max_tokens=700,
                    temperature=0.7,
                    timeout=25,
                )
                return r.choices[0].message.content + menu
            except Exception as e:
                return self._gpt_libre_same_state(message if isinstance(message, dict) else m, u, 'mejorar_evento')
        elif s == 'mejorar_dieta':
            dietas = {
                '1': 'keto',
                '2': 'vegana',
                '3': 'mediterránea',
                '4': 'ayuno 16:8',
                '5': 'vegetariana',
                '6': 'colesterol bajo',
            }
            dieta = dietas.get(m.strip(), m.strip().lower())
            data = u['data']
            data['dieta_especial'] = dieta
            perfil_usuario = self._profile_for_prompt(data)
            prompt = (
                'Eres ZIA nutricionista. Genera un plan específico de dieta '
                + dieta
                + ' adaptado al perfil del usuario. Genera un plan semanal específico con pautas claras, desayunos, comidas y cenas, alimentos recomendados y alimentos a evitar. Perfil: '
                + perfil_usuario
                + '. Responde en español con emojis, máximo 450 palabras.'
            )
            menu = self._append_menu_footer(data)
            u['state'] = 'menu_principal'
            try:
                r = self.openai.chat.completions.create(
                    model=self.config.get('ai', {}).get('model', 'gpt-4o-mini'),
                    messages=[
                        {'role': 'system', 'content': COACH_TONE + ' Eres ZIA nutricionista. Responde en español con emojis.'},
                        {'role': 'user', 'content': prompt},
                    ],
                    max_tokens=850,
                    temperature=0.7,
                    timeout=30,
                )
                return r.choices[0].message.content + menu
            except Exception as e:
                return self._gpt_libre_same_state(message if isinstance(message, dict) else m, u, 'mejorar_dieta')
        elif s == 'mejorar_progreso':
            data = u['data']
            perfil_usuario = self._profile_for_prompt(data)
            prompt = (
                'Eres ZIA coach nutricional motivadora. El usuario cuenta cómo le ha ido esta semana: '
                + m.strip()
                + '. Analiza con empatía, celebra avances, detecta 2 ajustes prácticos para la semana siguiente y cierra con ánimo. Perfil: '
                + perfil_usuario
                + '. Máximo 180 palabras. Emojis.'
            )
            menu = self._append_menu_footer(data)
            u['state'] = 'menu_principal'
            try:
                r = self.openai.chat.completions.create(
                    model=self.config.get('ai', {}).get('model', 'gpt-4o-mini'),
                    messages=[
                        {'role': 'system', 'content': COACH_TONE + ' Eres ZIA nutricionista. Responde en español con emojis.'},
                        {'role': 'user', 'content': prompt},
                    ],
                    max_tokens=450,
                    temperature=0.7,
                    timeout=25,
                )
                return r.choices[0].message.content + menu
            except Exception as e:
                return self._gpt_libre_same_state(message if isinstance(message, dict) else m, u, 'mejorar_progreso')
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
                                            + '. Restricciones y alergias (respétalas): '
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
                    menu = self._append_menu_footer(data)
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
            menu = self._append_menu_footer(data)
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
                menu = self._append_menu_footer(data)
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
                return r.choices[0].message.content + self._append_menu_footer(data)
            except Exception as e:
                u['state'] = 'menu_principal'
                return (
                    'No pude analizar tu progreso ahora mismo por un error o timeout, pero no pasa nada 💪 '
                    'Cuéntamelo de nuevo en unos minutos y lo revisamos juntas.'
                    + self._append_menu_footer(data)
                )
        elif s == 'suplementos':
            data = u['data']
            ml = m.strip().lower()
            num = m.strip()
            if not u['data'].get('suplementos_med_checked'):
                u['data']['suplementos_medicacion'] = m.strip() or 'No indicado'
                u['data']['suplementos_med_checked'] = True
                pending = u['data'].get('suplementos_pending_query', '').strip()
                if pending:
                    m = pending
                    ml = m.strip().lower()
                    num = m.strip()
                else:
                    return (
                        'Gracias por decírmelo 🙏 ¿Cuál es tu principal preocupación ahora mismo? 💊\n'
                        '1️⃣ Me falta energía\n'
                        '2️⃣ Quiero ganar músculo\n'
                        '3️⃣ Mejorar digestión\n'
                        '4️⃣ Dormir mejor\n'
                        '5️⃣ Reforzar defensas\n'
                        '6️⃣ Perder peso'
                    )
            if num in ['1'] or any(w in ml for w in ['energi','cansancio','fatiga']):
                opcion = 'energia'
            elif num in ['2'] or any(w in ml for w in ['musculo','fuerza','proteina','gym']):
                opcion = 'musculo'
            elif num in ['3'] or any(w in ml for w in ['digest','estomago','intestin','hincha']):
                opcion = 'digestion'
            elif num in ['4'] or any(w in ml for w in ['dormir','sueño','insomnio','descanso']):
                opcion = 'sueno'
            elif num in ['5'] or any(w in ml for w in ['defensa','inmunidad','resfriado']):
                opcion = 'defensas'
            elif num in ['6'] or any(w in ml for w in ['perder','adelgazar','peso','grasa']):
                opcion = 'peso'
            else:
                return self._gpt_libre_same_state(message if isinstance(message, dict) else m, u, 'suplementos')
            u['data']['last_suplementos_opcion'] = opcion
            prompt = (
                'Eres ZIA, experta en nutrición y suplementación deportiva. El usuario quiere '
                + opcion
                + '. Medicación habitual indicada por el usuario: '
                + u['data'].get('suplementos_medicacion', 'No indicado')
                + '. Dame los 4-5 mejores suplementos específicos para este objetivo con:\n'
                'Si el usuario toma medicación o no queda claro, incluye una advertencia breve para consultar con su médico/farmacéutico antes de tomar suplementos.\n'
                '- Nombre del suplemento y para qué sirve\n'
                '- Dosis recomendada diaria\n'
                '- Precio orientativo en España (€/mes)\n'
                '- Cuándo tomarlo (antes/después entreno, con comida, etc)\n'
                'energia: Vitamina B12, Hierro, Magnesio, CoQ10, Vitamina D\n'
                'musculo: Creatina monohidrato, Proteína whey, BCAA, Glutamina, ZMA\n'
                'digestion: Probióticos, Enzimas digestivas, Aloe vera, Jengibre, Fibra\n'
                'sueno: Melatonina (0.5-5mg noche ~8€), Magnesio glicinato (300mg noche ~15€), Ashwagandha (300mg noche ~20€), L-Triptófano (500mg noche ~12€), Valeriana (300mg noche ~8€)\n'
                'defensas: Vitamina C, Zinc, Equinácea, Vitamina D, Probióticos\n'
                'peso: L-carnitina, CLA, Té verde, Proteína whey, Fibra\n'
                'Para cada suplemento: nombre, para qué sirve, dosis, cuándo tomarlo, precio €/mes.\n'
                'Tono experto y motivador. Emojis. Máximo 250 palabras.'
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
                return r.choices[0].message.content + self._append_menu_footer(data)
            except Exception as e:
                u['state'] = 'menu_principal'
                return (
                    'No pude generar recomendaciones de suplementos ahora mismo por un error o timeout. '
                    'Aun asi, podemos seguir avanzando juntos 💪 Intentalo de nuevo en unos minutos.'
                    + self._append_menu_footer(data)
                )
        else:
            u['state'] = 'welcome'
            return 'Escribe Hola para empezar 👋'

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
                  'Restricciones y alergias: ' + data.get('restricciones','Ninguna') + '\n'
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
            return 'Error generando plan: ' + str(e)[:60] + '. Escribe Hola para reintentar.'

    def _generar_plan_partes(self, data):
        self._ensure_grupo_nutricion_defaults(data)
        company = self.config['branding']['company_name']
        cal = calorias(data)
        personas = data.get('personas', '1 persona')
        presupuesto = data.get('presupuesto', '65')
        num_personas = data.get('num_personas', 1)
        super_nombre = data.get('supermercado', 'Mercadona')
        try:
            peso = float(data.get('peso', 70))
        except Exception:
            peso = 70
        actividad_norm = normalize_text(data.get('actividad', '') + ' ' + data.get('actividad_tag', ''))
        agua_litros = peso * 0.035
        if 'activo' in actividad_norm:
            agua_litros += 0.5
        if 'muy' in actividad_norm:
            agua_litros += 0.3
        agua_litros = round(agua_litros, 1)
        pauta_nutricional = (
            'El usuario necesita exactamente ' + str(cal) + ' kcal/día. '
            'Distribuye: 30% proteínas, 40% carbohidratos, 30% grasas. '
            'PROHIBIDO repetir el mismo proteína dos días seguidos. '
            'Varía entre pollo, pescado, legumbres, huevos, ternera, pavo. '
            'Varía también los carbohidratos entre arroz, quinoa, patata, pasta integral, avena. '
            'CRÍTICO: Si restricciones incluye sin gluten, PROHIBIDO incluir trigo, cebada, centeno, avena normal en ningún día. '
            'Si sin lactosa, PROHIBIDO lácteos en ningún día. '
            'Al final de cada día añade exactamente: 💧 Agua recomendada: ' + str(agua_litros) + ' litros según peso y actividad. '
        )
        intol = (data.get('restricciones') or 'Ninguna').strip()
        intol_norm = normalize_text(intol)
        if intol_norm and intol_norm not in ('ninguna', 'no', 'nada'):
            pauta_nutricional += (
                'CRÍTICO restricciones/alergias declaradas (' + intol + '): '
                'PROHIBIDO incluir alimentos que las contradigan o trazas no seguras. '
            )
            if any(x in intol_norm for x in ('gluten', 'celia', 'trigo', 'avena')):
                pauta_nutricional += 'Sin gluten estricto: ningún trigo, cebada, centeno ni avena no certificada GF. '
            if 'lactosa' in intol_norm or 'lacteo' in intol_norm:
                pauta_nutricional += 'Sin lácteos con lactosa. '
            if 'huevo' in intol_norm:
                pauta_nutricional += 'Sin huevo ni mayonesa con huevo. '
            if 'frutos secos' in intol_norm or 'cacahu' in intol_norm or 'nueces' in intol_norm or 'almendra' in intol_norm:
                pauta_nutricional += 'Sin frutos secos y evita trazas. '
            if 'marisco' in intol_norm or 'crustaceo' in intol_norm or 'molusco' in intol_norm:
                pauta_nutricional += 'Sin marisco, crustáceos ni moluscos. '
        rninos = (data.get('restricciones_ninos') or '').strip()
        rn_norm = normalize_text(rninos)
        if rninos and rn_norm not in ('ninguna', 'no', 'nada'):
            pauta_nutricional += (
                'CRÍTICO restricciones/alergias de menores en la familia (' + rninos + '): '
                'PROHIBIDO incluir alimentos que las contradigan o trazas no seguras. '
            )
            if any(x in rn_norm for x in ('gluten', 'celia', 'trigo', 'avena')):
                pauta_nutricional += 'Sin gluten estricto para menores: ningún trigo, cebada, centeno ni avena no certificada GF. '
            if 'lactosa' in rn_norm or 'lacteo' in rn_norm:
                pauta_nutricional += 'Sin lácteos con lactosa en recetas para menores. '
            if 'huevo' in rn_norm:
                pauta_nutricional += 'Sin huevo ni mayonesa con huevo donde afecte a menores. '
            if 'frutos secos' in rn_norm or 'cacahu' in rn_norm or 'nueces' in rn_norm or 'almendra' in rn_norm:
                pauta_nutricional += 'Sin frutos secos y evita trazas en comidas compartidas. '
            if 'marisco' in rn_norm or 'crustaceo' in rn_norm or 'molusco' in rn_norm:
                pauta_nutricional += 'Sin marisco, crustáceos ni moluscos donde afecte a menores. '
        cocina_minima = ''
        if data.get('cocina') == 'Cocina para vagos':
            cocina_minima = (
                'IMPORTANTE: Este usuario prefiere cocina mínima. Usa SOLO productos ready-to-eat: '
                'arroz Brillante, tortilla hecha, cremas tetra brik, ensaladas bolsa, conservas, '
                'hummus listo, pollo envasado. PROHIBIDO recetas que requieran más de 5 minutos. '
            )
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
            obj_txt = (data.get('objetivo') or '').strip()
            perfil = (
                'PERFIL GRUPAL: ' + descripcion_grupo + '. '
                + ('Objetivo(s): ' + obj_txt + '. ' if obj_txt else '')
                + 'Plan para: ' + personas + ' (' + str(num_personas) + ' personas). '
                'Presupuesto MAXIMO: ' + presupuesto + ' euros/semana. '
                'Actividad: ' + data.get('actividad', '') + '. '
                'Numero de comidas: ' + data.get('num_comidas', '') + '. '
                'Restricciones y alergias: ' + data.get('restricciones', 'Ninguna') + '. '
                + pauta_nutricional +
                cocina_minima +
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
                'Restricciones y alergias: ' + data.get('restricciones', 'Ninguna') + '. '
                'Presupuesto MAXIMO: ' + presupuesto + ' euros/semana. '
                'Actividad: ' + data.get('actividad', '') + '. '
                'Numero de comidas: ' + data.get('num_comidas', '') + '. '
                + pauta_nutricional +
                cocina_minima +
                'Adapta TODAS las cantidades para ' + str(num_personas) + ' persona(s). '
                + catalogo
            )

        model = self.config.get('ai', {}).get('model', 'gpt-4o-mini')
        system = COACH_TONE + ' Eres ZIA nutricionista de ' + company + '. Responde en espanol con emojis.'

        prompt1 = (
            'INSTRUCCIÓN ABSOLUTA: Tu respuesta debe empezar EXACTAMENTE con la línea "📅 Lunes:" como primera línea. '
            'Nada antes. Genera SOLO Lunes, Martes y Miércoles con Desayuno, Comida y Cena. '
            'PARA en la Cena del Miércoles. '
            'Formato obligatorio de cada día (sin asteriscos ni #, solo emojis y texto):\n'
            '📅 Lunes:\n'
            '🌅 Desayuno: [descripción con pesos] | Kcal: X | P: Xg | C: Xg | G: Xg\n'
            '🍽️ Comida: [descripción con pesos] | Kcal: X | P: Xg | C: Xg | G: Xg\n'
            '🌙 Cena: [descripción con pesos] | Kcal: X | P: Xg | C: Xg | G: Xg\n'
            '💧 Agua recomendada: ' + str(agua_litros) + ' litros según peso y actividad.\n'
            'Eres ZIA nutricionista. INSTRUCCION ESTRICTA: Genera UNICAMENTE Lunes, Martes y Miercoles. '
            'PROHIBIDO incluir Jueves, Viernes, Sabado o Domingo. '
            'Empieza con 📅 Lunes: Cada dia: Desayuno, Comida y Cena. '
            'Prohibido markdown (# * ** viñetas con guión). '
            'Termina exactamente en la Cena del Miercoles. Sin texto despues. '
            + perfil
        )
        prompt2 = (
            'INSTRUCCIÓN ABSOLUTA: Tu respuesta debe empezar EXACTAMENTE con la línea "📅 Jueves:" como primera línea. '
            'Nada antes. Genera SOLO Jueves, Viernes y Sábado con Desayuno, Comida y Cena. '
            'PARA en la Cena del Sábado. '
            'OBLIGATORIO incluir Desayuno, Comida Y Cena para Jueves, Viernes Y Sábado. '
            'PROHIBIDO terminar en Comida del Sábado. La Cena del Sábado es OBLIGATORIA. '
            'Formato obligatorio de cada día (sin asteriscos ni #):\n'
            '📅 Jueves:\n'
            '🌅 Desayuno: [descripción con pesos] | Kcal: X | P: Xg | C: Xg | G: Xg\n'
            '🍽️ Comida: [descripción con pesos] | Kcal: X | P: Xg | C: Xg | G: Xg\n'
            '🌙 Cena: [descripción con pesos] | Kcal: X | P: Xg | C: Xg | G: Xg\n'
            '💧 Agua recomendada: ' + str(agua_litros) + ' litros según peso y actividad.\n'
            'Eres ZIA nutricionista. INSTRUCCION ESTRICTA: Genera UNICAMENTE Jueves, Viernes y Sabado. '
            'PROHIBIDO incluir Lunes, Martes, Miercoles o Domingo. '
            'Empieza directamente con 📅 Jueves: Cada dia: Desayuno, Comida y Cena. '
            'Prohibido markdown (# * ** viñetas con guión). '
            'Termina exactamente en la Cena del Sabado. Sin texto antes ni despues. '
            + perfil
        )
        prompt3 = (
            'INSTRUCCIÓN ABSOLUTA: Tu respuesta debe empezar EXACTAMENTE con la línea "📅 Domingo:" como primera línea. '
            'Nada antes. Genera SOLO el Domingo con Desayuno, Comida y Cena. '
            'Formato obligatorio (sin asteriscos ni #):\n'
            '📅 Domingo:\n'
            '🌅 Desayuno: [descripción con pesos] | Kcal: X | P: Xg | C: Xg | G: Xg\n'
            '🍽️ Comida: [descripción con pesos] | Kcal: X | P: Xg | C: Xg | G: Xg\n'
            '🌙 Cena: [descripción con pesos] | Kcal: X | P: Xg | C: Xg | G: Xg\n'
            '💧 Agua recomendada: ' + str(agua_litros) + ' litros según peso y actividad.\n'
            'Eres ZIA nutricionista. INSTRUCCION ESTRICTA: Genera UNICAMENTE el Domingo. '
            'PROHIBIDO incluir cualquier otro dia de la semana. '
            'PROHIBIDO incluir lista de la compra o precios. '
            'Empieza directamente con 📅 Domingo: con Desayuno, Comida y Cena. '
            'Prohibido markdown (# * ** viñetas con guión). '
            'Termina exactamente en la Cena del Domingo. Sin texto antes ni despues. '
            + perfil
        )
        prompt4 = (
            'Eres ZIA nutricionista. INSTRUCCION ESTRICTA: Genera UNICAMENTE la LISTA DE LA COMPRA '
            'completa para los 7 dias (Lunes a Domingo). PROHIBIDO incluir menus o dias de la semana. '
            'El TOTAL ESTIMADO NO puede superar ' + presupuesto + ' euros. '
            'Si los productos superan el presupuesto reduce cantidades o elige alternativas mas baratas. '
            'PROHIBIDO incluir especias, condimentos o aliños. '
            'Sin markdown (# * ** ni guiones como viñetas); usa emojis por sección. '
            'Organiza por secciones EXACTAMENTE asi con cantidades y precios para ' + super_nombre + ': '
            '🥩 Proteínas, 🥦 Verduras y hortalizas, 🍎 Frutas, 🌾 Cereales y legumbres, 🥚 Lácteos y huevos, 🫙 Otros. '
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

        def _call(prompt, max_tok, system_prompt=system):
            try:
                r = self.openai.chat.completions.create(
                    model=model,
                    messages=[
                        {'role': 'system', 'content': system_prompt},
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
        lista_system = (
            'Eres ZIA nutricionista. Genera SOLO la lista de la compra organizada por categorías con cantidades y precios. '
            'Texto plano para WhatsApp: prohibido markdown (# * ** viñetas con guión). Usa emojis por sección. '
            'Sin motivación ni texto extra.'
        )
        lista = _call(prompt4, 1000, lista_system).rstrip() + suffix4
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
        m_norm = normalize_text(text)
        special_prompt = None
        if any(p in m_norm for p in ['no se que cenar', 'no se que comer', 'que ceno', 'que como']):
            special_prompt = (
                'El usuario no sabe qué comer o cenar. Propón 3 opciones rápidas adaptadas a este perfil: '
                + perfil_usuario
                + '. Incluye ingredientes sencillos, tiempo aproximado y una recomendación principal.'
            )
        elif any(p in m_norm for p in ['nevera vacia', 'no tengo nada', 'caducado', 'se me ha caducado']):
            special_prompt = (
                'El usuario tiene la nevera vacía o productos caducados. Crea una lista de emergencia de 15 productos básicos adaptada a su perfil: '
                + perfil_usuario
                + '. Organiza por secciones y prioriza productos versátiles.'
            )
        elif any(p in m_norm for p in ['glovo', 'uber eats', 'siempre pido', 'delivery']):
            special_prompt = (
                'El usuario suele pedir delivery. Entiende por qué puede pasar, sin juzgar, y da una alternativa más fácil y rápida que pedir comida. Perfil: '
                + perfil_usuario
                + '. Incluye 3 opciones de supermercado o montaje rápido.'
            )
        elif any(p in m_norm for p in ['me lo cargo el finde', 'finde malo', 'me he pasado']):
            special_prompt = (
                'El usuario siente que se ha pasado el fin de semana. Responde sin juicios y crea un plan reset de 2 días motivador, con comidas concretas. Perfil: '
                + perfil_usuario
            )
        elif any(p in m_norm for p in ['sin lista', 'gasto mucho', 'gasto el doble']):
            special_prompt = (
                'El usuario compra sin lista o gasta demasiado. Genera una lista pre-generada basada en su perfil: '
                + perfil_usuario
                + '. Máximo 15 productos, organizada por secciones y con enfoque práctico.'
            )
        elif any(p in m_norm for p in ['no me llega', 'muy caro', 'presupuesto justo', 'poco dinero']):
            special_prompt = (
                'El usuario tiene presupuesto justo. Crea un plan económico real bajo 40€/semana adaptado a su perfil: '
                + perfil_usuario
                + '. Incluye alimentos baratos, saciantes y combinaciones simples.'
            )
        elif any(p in m_norm for p in ['turnos', 'horario irregular', 'trabajo de noche']):
            special_prompt = (
                'El usuario tiene turnos u horario irregular. Crea un plan flexible sin horario fijo adaptado a su perfil: '
                + perfil_usuario
                + '. Incluye opciones para antes, durante y después del turno.'
            )
        elif any(p in m_norm for p in ['viajo mucho', 'de viaje', 'hotel']):
            special_prompt = (
                'El usuario viaja mucho. Crea un plan portable y opciones saludables en restaurante/hotel adaptadas a su perfil: '
                + perfil_usuario
                + '. Da soluciones concretas y fáciles.'
            )
        elif any(p in m_norm for p in ['no come verdura', 'mis hijos', 'no le gusta']):
            special_prompt = (
                'El usuario necesita que alguien coma más verdura sin notarla. Propón recetas donde no se nota la verdura y trucos prácticos. Perfil: '
                + perfil_usuario
            )
        elif any(p in m_norm for p in ['no veo resultados', 'me desanimo', 'no funciona', 'no sirve']):
            special_prompt = (
                'El usuario no ve resultados y está desanimado. Celebra su esfuerzo, ajusta expectativas y da un plan concreto de próximos pasos. Perfil: '
                + perfil_usuario
            )
        if special_prompt:
            try:
                r = self.openai.chat.completions.create(
                    model=self.config.get('ai',{}).get('model','gpt-4o-mini'),
                    messages=[
                        {
                            'role': 'system',
                            'content': (
                                'Eres ZIA, nutricionista experta Y coach motivacional. Respondes siempre con empatía, '
                                'sin juzgar, con soluciones concretas y prácticas. Tono cercano, motivador y experto. '
                                'Máximo 150 palabras. Emojis. Texto plano para WhatsApp: sin markdown (# * ** '
                                'ni viñetas con guión).'
                            ),
                        },
                        {'role': 'user', 'content': special_prompt},
                    ],
                    max_tokens=350,
                    temperature=0.7,
                    timeout=20,
                )
                return r.choices[0].message.content
            except Exception as e:
                return 'No pude responder por un error o timeout. Intentalo de nuevo en unos minutos. Detalle: ' + str(e)[:80]
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
