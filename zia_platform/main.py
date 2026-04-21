"""
ZIA PLATFORM v2.0
"""
import os, json, re
from openai import OpenAI
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()
client_openai = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
user_memory = {}

def get_user(uid):
    if uid not in user_memory:
        user_memory[uid] = {'state':'welcome','data':{},'last_plan':None,'plan_count':0}
    return user_memory[uid]

def save_user(uid, u): user_memory[uid] = u
def set_state(u, s): u['state'] = s
def save_data(u, k, v): u['data'][k] = v

def parse_personal_data(text):
    d = {}
    t = text.lower()
    if any(w in t for w in ['hombre','masculino']): d['gender'] = 'Hombre'
    elif any(w in t for w in ['mujer','femenino']): d['gender'] = 'Mujer'
    else: d['gender'] = 'No especificado'
    m = re.match(r'^([A-Za-z][a-z]+)', text)
    if m and m.group(1).lower() not in ['hombre','mujer','soy','tengo']:
        d['name'] = m.group(1).capitalize()
    else: d['name'] = ''
    nums = [int(n) for n in re.findall(r'\d+', text)]
    for n in nums:
        if 10<=n<=100 and 'age' not in d: d['age']=str(n)
        elif 40<=n<=200 and 'weight' not in d and str(n)!=d.get('age',''): d['weight']=str(n)
        elif 130<=n<=230 and 'height' not in d: d['height']=str(n)
    return d

def validate_personal_data(d):
    m = []
    if 'age' not in d: m.append('edad')
    if 'weight' not in d: m.append('peso en kg')
    if 'height' not in d: m.append('altura en cm')
    return m

def calculate_calories(d):
    try:
        w=float(d.get('weight',70)); h=float(d.get('height',170)); a=float(d.get('age',30))
        if d.get('gender')=='Hombre': bmr=10*w+6.25*h-5*a+5
        else: bmr=10*w+6.25*h-5*a-161
        return int(bmr*1.55)
    except: return 2000

def normalize(t): return t.strip().lower()
def is_reset(t): return normalize(t) in ['hola','inicio','reset','empezar','reiniciar','start','menu','nuevo plan']
def is_affirmative(t): return any(a in normalize(t) for a in ['si','yes','ok','vale','vamos','genial'])
def is_family(t): return any(w in t.lower() for w in ['pareja','familia','2 personas','dos personas'])

def build_catalog(config):
    cats = config.get('catalog',{}).get('categories',[])
    if not cats: return ""
    txt = f"\nPRODUCTOS DE {config['branding']['company_name'].upper()}:\n"
    for cat in cats:
        txt += f"\n{cat['name']}:\n"
        for p in cat.get('products',[]):
            line = f"  - {p['name']}"
            if p.get('price'): line += f" ({p['price']})"
            if p.get('bestseller'): line += " ESTRELLA"
            txt += line + "\n"
    return txt

def generate_plan(data, config, modification=None):
    try:
        cal = calculate_calories(data)
        company = config['branding']['company_name']
        catalog = build_catalog(config)
        profile = data.get('profile','Solo para mi')
        family = any(w in profile.lower() for w in ['pareja','familia'])
        prompt = f"""Eres ZIA, nutricionista de {company}.
PERFIL: {data.get('name','')} | {data.get('gender','')} | {data.get('age','')} anos | {data.get('weight','')}kg | {data.get('height','')}cm | {cal} kcal/dia
Plan para: {profile} | Objetivo: {data.get('goal','')} | Estilo: {data.get('lifestyle','')}
Restricciones: {data.get('restrictions','Ninguna')} | Alergias: {data.get('allergies','Ninguna')}
Presupuesto: {data.get('budget','')} euros/semana
{catalog}
{"MODIFICACION: " + modification if modification else ""}

Genera menu LUNES-DOMINGO con Desayuno/Comida/Merienda/Cena, cantidades en gramos, tiempo preparacion.
{"Plan equilibrado para toda la familia." if family else "Plan personalizado al objetivo."}
RESPETA ABSOLUTAMENTE restricciones y alergias.
Lista compra por categorias con precios. Total estimado dentro del presupuesto.
Recomienda 1-2 productos estrella del catalogo que ayuden al objetivo.
Maximo 500 palabras. Tono cercano y motivador."""
        r = client_openai.chat.completions.create(
            model=config.get('ai',{}).get('model','gpt-4o-mini'),
            messages=[{"role":"system","content":f"Eres ZIA, nutricionista de {company}. Respondes en espanol."},
                      {"role":"user","content":prompt}],
            max_tokens=config.get('ai',{}).get('max_tokens',1800),
            temperature=config.get('ai',{}).get('temperature',0.7)
        )
        return r.choices[0].message.content
    except Exception as e:
        return f"Error generando plan: {str(e)[:80]}\n\nEscribe Hola para intentarlo de nuevo."

def msg(user, config):
    s = user['state']
    d = user['data']
    name = d.get('name','')
    company = config['branding']['company_name']
    msgs = {
        'welcome': f"Hola! Soy ZIA de {company}\n\nEn 2 min te preparo tu menu semanal + lista de la compra lista para el carrito\n\nEmpezamos?\n\n  Si, vamos!\n  Cuentame mas",
        'personal_data': "Para crear tu plan perfecto necesito conocerte.\n\nEscribeme en una linea:\nNombre, genero, edad, peso (kg) y altura (cm)\n\nEjemplo: Maria, mujer, 34, 65, 165",
        'profile': f"Perfecto{', '+name if name else ''}!\n\nPara cuantas personas preparamos el plan?\n\n  Solo para mi\n  Para mi pareja\n  Para mi familia",
        'goal_individual': f"Cual es tu objetivo{', '+name if name else ''}?\n\n  Ganar musculo\n  Perder grasa\n  Mas energia\n  Comer mejor",
        'goal_family': "Cual es el objetivo de la familia?\n\n  Comer mas sano\n  Perder peso\n  Mas energia\n  Ahorrar en la compra\n  Comer mas variado",
        'lifestyle': "Como es tu relacion con la cocina?\n\n  Tengo poco tiempo\n  Me gusta cocinar\n  Cocino lo justo\n  Solo recetas rapidas",
        'restrictions': "Tienes alguna restriccion alimentaria?\n\n  Ninguna\n  Vegano/Vegetariano\n  Sin gluten\n  Sin lactosa\n  Sin pescado\n  Otra (escribela)",
        'allergies': "Tienes alguna alergia? (importante para tu seguridad)\n\n  No tengo alergias\n  Frutos secos\n  Huevo\n  Marisco\n  Otra (escribela)",
        'budget': "Ultimo paso! Cuanto quieres gastar esta semana?\n\n  30-50\n  50-80\n  80-120\n  Mas de 120",
        'generating': f"Analizando tu perfil...\nSeleccionando productos de {company}...\nCreando tu menu...\n\nDame un momento",
        'cta': "Que quieres hacer?\n\n  Anadir todo al carrito\n  Cambiar algo del plan\n  Anadir o quitar productos\n  Solo guardar la lista",
        'add_products': "Dime que quieres cambiar en la lista:\n- Anade leche de avena\n- Quita el tofu\n- Mas fruta",
        'modify': "Dime que quieres cambiar:\n- Quita el pollo\n- Cambia la cena del martes\n- Mas verduras",
        'checkout': f"Tu carrito esta listo!\n\nCompra en {company}: {config.get('integrations',{}).get('cart',{}).get('checkout_url', config['branding']['website'])}\n\nQuieres que cada semana te prepare el plan automaticamente?\n\n  Si, me interesa\n  No por ahora",
        'subscription': f"Anotado! Cada semana ZIA te preparara tu plan.\nGracias por confiar en {company}\n\nEscribe Hola cuando quieras un nuevo plan",
        'done': f"Hasta pronto{', '+name if name else ''}!\nEscribe Hola para un nuevo plan\nZIA - {company}",
        'returning': f"Hola de nuevo{', '+name if name else ''}! Me alegra que vuelvas!\nUltimo objetivo: {d.get('goal','comer sano')}\n\nQue hacemos?\n\n  Mismo plan de la semana pasada\n  Nuevo plan personalizado\n  Modificar el anterior",
    }
    return msgs.get(s, "Escribe Hola para empezar")

def process_message(uid, message, config):
    user = get_user(uid)
    s = user['state']
    m = message.strip()

    if is_reset(m):
        set_state(user, 'returning' if user.get('plan_count',0)>0 else 'welcome')
        save_user(uid, user)
        return msg(user, config)

    if s == 'welcome':
        set_state(user, 'personal_data')
    elif s == 'personal_data':
        parsed = parse_personal_data(m)
        missing = validate_personal_data(parsed)
        if missing:
            save_user(uid, user)
            return f"Solo me falta: {', '.join(missing)}\n\nEj: Carlos, hombre, 38, 82, 178"
        for k,v in parsed.items(): save_data(user,k,v)
        set_state(user, 'profile')
    elif s == 'profile':
        save_data(user,'profile',m)
        set_state(user, 'goal_family' if is_family(m) else 'goal_individual')
    elif s in ['goal_individual','goal_family']:
        save_data(user,'goal',m); set_state(user,'lifestyle')
    elif s == 'lifestyle':
        save_data(user,'lifestyle',m); set_state(user,'restrictions')
    elif s == 'restrictions':
        save_data(user,'restrictions',m); set_state(user,'allergies')
    elif s == 'allergies':
        save_data(user,'allergies',m); set_state(user,'budget')
    elif s == 'budget':
        save_data(user,'budget',m)
        set_state(user,'generating')
        save_user(uid,user)
        gen_msg = msg(user,config)
        plan = generate_plan(user['data'],config)
        user['last_plan']=plan; user['plan_count']=user.get('plan_count',0)+1
        set_state(user,'cta'); save_user(uid,user)
        return gen_msg+"\n\n"+plan+"\n\n"+msg(user,config)
    elif s == 'cta':
        ml = normalize(m)
        if any(w in ml for w in ['carrito','anadir','comprar','si']): set_state(user,'checkout')
        elif any(w in ml for w in ['cambiar','modificar']): set_state(user,'modify')
        elif any(w in ml for w in ['producto','quitar','agregar']): set_state(user,'add_products')
        else: set_state(user,'checkout')
    elif s == 'add_products':
        plan=generate_plan(user['data'],config,modification=f"Cambios en lista: {m}")
        user['last_plan']=plan; set_state(user,'cta'); save_user(uid,user)
        return plan+"\n\n"+msg(user,config)
    elif s == 'modify':
        plan=generate_plan(user['data'],config,modification=m)
        user['last_plan']=plan; set_state(user,'cta'); save_user(uid,user)
        return plan+"\n\n"+msg(user,config)
    elif s == 'checkout':
        set_state(user,'subscription' if is_affirmative(m) else 'done')
    elif s == 'subscription':
        set_state(user,'done')
    elif s == 'returning':
        ml=normalize(m)
        if any(w in ml for w in ['mismo','igual','repetir']):
            plan=generate_plan(user['data'],config)
            user['last_plan']=plan; user['plan_count']+=1; set_state(user,'cta'); save_user(uid,user)
            return plan+"\n\n"+msg(user,config)
        set_state(user,'personal_data' if any(w in ml for w in ['nuevo','diferente']) else 'modify')
    else:
        set_state(user,'welcome')

    save_user(uid,user)
    return msg(user,config)

def handle_whatsapp_message(from_number, body, config):
    return process_message(from_number.replace('whatsapp:','').replace('+',''), body, config)

if __name__ == '__main__':
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    client_id = os.environ.get('CLIENT_ID','zia-nutricion')
    config_path = os.path.join(os.path.dirname(__file__),'..','clients',client_id,'config.json')
    with open(config_path,encoding='utf-8') as f: config=json.load(f)
    print(f"\nZIA Platform v2.0 - {config['branding']['company_name']}")
    print("Escribe 'salir' para terminar\n" + "-"*50)
    uid='test_001'
    print("\nZIA:\n"+process_message(uid,'hola',config)+"\n")
    while True:
        try: ui=input("Tu: ").strip()
        except (EOFError,KeyboardInterrupt): break
        if ui.lower()=='salir': break
        if ui: print("\nZIA:\n"+process_message(uid,ui,config)+"\n"+"-"*50)
