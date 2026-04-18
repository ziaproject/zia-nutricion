from dotenv import load_dotenv
load_dotenv()
import os, json, threading, requests, base64, time, datetime
import main
import pytz
from pathlib import Path
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient

app = Flask(__name__)
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

MENU = "¿Qué necesitas ahora?\n\n1️⃣ Dime qué como hoy 🍽️\n2️⃣ Ajustar mi plan 💪\n3️⃣ Sorpréndeme con una receta ⚡\n4️⃣ Miro mi nevera 🧊\n5️⃣ Hacer la compra 🛒"

def estado_file(phone):
    safe = phone.replace("+","").replace(":","_").replace(" ","_")
    return DATA_DIR / f"s_{safe}.json"

def cargar_sesion(phone):
    f = estado_file(phone)
    if f.is_file():
        try:
            return json.loads(f.read_text())
        except:
            pass
    return {"estado":"inicio","perfil_tmp":{},"tipo_plan":None,"onboarding_step":0}

def guardar_sesion(phone, datos):
    try:
        estado_file(phone).write_text(json.dumps(datos, ensure_ascii=False))
    except Exception as e:
        print(f"Error guardando sesion: {e}")

def send(to, texto):
    try:
        cl = TwilioClient(TWILIO_SID, TWILIO_TOKEN)
        for parte in [texto[i:i+1500] for i in range(0, len(texto), 1500)]:
            cl.messages.create(body=parte, from_=TWILIO_FROM, to=to)
    except Exception as e:
        print(f"Error enviando: {e}")

def enviar(texto):
    resp = MessagingResponse()
    for parte in [texto[i:i+1500] for i in range(0, len(texto), 1500)]:
        resp.message(parte)
    return str(resp), 200, {"Content-Type": "text/xml"}

PREGUNTAS_INDIVIDUAL = [
    ("nombre", "👋 ¿Cómo te llamamos?"),
    ("datos_fisicos", "📊 Para personalizar tu plan necesito algunos datos:\nGenero, edad, peso (kg) y altura (cm)\nEjemplo: hombre, 36 anos, 80 kg, 175 cm"),
    ("objetivo", "🎯 ¿Cuál es tu objetivo principal?\n1️⃣ Perder grasa\n2️⃣ Ganar músculo\n3️⃣ Mantenimiento\n4️⃣ Comer más sano\n5️⃣ Más energía"),
    ("presupuesto", "💰 ¿Cuánto quieres gastar a la semana? (ej: 80€)"),
    ("supermercado", "🛒 ¿En qué supermercado/s sueles comprar?\nEj: Mercadona, 2️⃣ Lidl"),
    ("restricciones", "⚠️ ¿Tienes alguna alergia o intolerancia?\nSi no hay ninguna escribe: ninguna"),
    ("tiempo_cocina", "⏱️ ¿Cuánto tiempo tienes para cocinar?\n1️⃣ Menos de 20 minutos\n2️⃣ Entre 20 y 40 minutos\n3️⃣ Tengo tiempo, me gusta cocinar"),
]

PREGUNTAS_FAMILIAR = [
    ("nombre", "👋 ¿Cómo os llamamos?"),
    ("num_personas", "Cuantas personas coméis en casa?"),
    ("ninos_edades", "🧒 ¿Hay niños en casa? Si es así indica edades.\nSi no escribe: no"),
    ("gustos_familia", "🍽️ ¿Cuáles son los gustos o comidas favoritas de la familia?\nSi alguien no come algo, indicalo"),
    ("restricciones", "⚠️ ¿Hay alergias o intolerancias en la familia?\nSi no hay ninguna escribe: ninguna"),
    ("presupuesto", "💰 ¿Cuánto queréis gastar a la semana? (ej: 150€)"),
    ("supermercado", "🛒 ¿En qué supermercado/s soléis comprar?\nEj: Mercadona, 2️⃣ Lidl"),
    ("tiempo_cocina", "⏱️ ¿Cuánto tiempo tenéis para cocinar?\n1️⃣ Menos de 20 minutos\n2️⃣ Entre 20 y 40 minutos\n3️⃣ Tenemos tiempo, nos gusta cocinar"),
]

MAPA_OBJETIVO = {"1":"Perder grasa","2":"Ganar musculo","3":"Mantenimiento","4":"Comer mas sano","5":"Mas energia"}
MAPA_TIEMPO = {"1":"menos de 20 minutos","2":"entre 20 y 40 minutos","3":"tiempo libre, me gusta cocinar"}

def procesar_campo(campo, valor):
    if campo == "objetivo": return MAPA_OBJETIVO.get(valor.strip(), valor)
    if campo == "tiempo_cocina": return MAPA_TIEMPO.get(valor.strip(), valor)
    return valor

def ns(perfil):
    super_texto = perfil.get("supermercado","")
    if "herbolario" in super_texto.lower() or "navarro" in super_texto.lower():
        return "Herbolario Navarro"
    if "supercor" in super_texto.lower() or "corte" in super_texto.lower():
        return "Supercor"
    return main.nombre_supermercado_perfil(perfil)
def us(perfil):
    super_texto = perfil.get("supermercado","")
    ids = main.ids_supermercados_detectados(super_texto)
    if ids and ids[0] in main.SUPER_TIENDA_URL:
        return main.SUPER_TIENDA_URL[ids[0]][1]
    if "herbolario" in super_texto.lower() or "navarro" in super_texto.lower():
        return "https://www.herbolarionavarro.es"
    if "supercor" in super_texto.lower() or "corte" in super_texto.lower():
        return "https://www.supercor.es"
    return "https://www.mercadona.es"

def generar_plan_async(phone, perfil, memoria):
    try:
        client = main.crear_cliente()
        system = main.system_zia_completo() + "\n\nIMPORTANTE: PROHIBIDO añadir preguntas, comentarios o frases finales. Termina EXACTAMENTE con la cena del domingo."
        plan = main.completar(client, [
            {"role":"system","content":system},
            {"role":"user","content":main.mensaje_plan_semanal(perfil, memoria)}
        ], max_tokens=3192)
        memoria["plan_semanal_actual"] = plan
        memoria["ultimo_plan"] = plan
        main.añadir_lista_al_historial(memoria, plan)
        main.guardar_memoria_usuario(phone, memoria)
        s = cargar_sesion(phone)
        s["estado"] = "esperando_cambios"
        guardar_sesion(phone, s)
        partes = [plan[i:i+1400] for i in range(0, len(plan), 1400)]
        for parte in partes:
            send(phone, parte)
            time.sleep(2)
        time.sleep(3)
        send(phone, "💪 ¿Quieres cambiar algo del plan? (sí/no)")
    except Exception as e:
        send(phone, "❌ Error generando el plan. Escribe reset para empezar.")
        s = cargar_sesion(phone); s["estado"] = "chat"; guardar_sesion(phone, s)

def generar_lista_async(phone, perfil, memoria):
    try:
        plan_ref = memoria.get("plan_semanal_actual") or memoria.get("ultimo_plan") or ""
        if not plan_ref:
            send(phone, "¡Aún no tengo tu plan! Escribe reset 🚀")
            s = cargar_sesion(phone); s["estado"] = "chat"; guardar_sesion(phone, s)
            return
        lista = main.generar_lista_compra_respuesta(client=main.crear_cliente(), perfil=perfil, plan_texto=plan_ref)
        memoria["lista_compra_actual"] = lista
        memoria["ultimo_plan"] = (plan_ref + "\n\n" + lista).strip()
        main.guardar_memoria_usuario(phone, memoria)
        s = cargar_sesion(phone); s["estado"] = "esperando_pago_o_comparar"; guardar_sesion(phone, s)
        partes = [lista[i:i+1400] for i in range(0, len(lista), 1400)]
        for parte in partes:
            send(phone, parte)
            time.sleep(1)
        time.sleep(2)
        send(phone, f"¿Qué quieres hacer?\n\n1️⃣ Pagar en {ns(perfil)}\n2️⃣ Comparar precios en supermercados")
    except Exception as e:
        send(phone, "❌ Error generando la lista. Inténtalo de nuevo.")
        s = cargar_sesion(phone); s["estado"] = "chat"; guardar_sesion(phone, s)

def generar_comparativa_async(phone, memoria, perfil):
    try:
        client = main.crear_cliente()
        presupuesto = perfil.get("presupuesto", "no especificado")
        lista_ref = memoria.get("lista_compra_actual") or memoria.get("ultimo_plan") or ""
        prompt = f"""El usuario tiene un presupuesto de {presupuesto}. Usa ese total como base y ajusta el precio a cada supermercado usando diferencias reales de precio en Espana. Mercadona es el precio base. Lidl y Aldi son 10-15% mas baratos. Carrefour similar a Mercadona. Consum 3-5% mas caro. Herbolario Navarro es especialista en productos eco y naturales, 15-25% mas caro. Supercor (El Corte Ingles) es el mas caro, 20-30% mas que Mercadona. Marca claramente cual es el MAS BARATO.

Lista de la compra: {lista_ref[:2000]}

Devuelve EXACTAMENTE este formato, sin texto extra:

🛒 Comparativa de precios:

1️⃣ Mercadona → XX.XX euros
2️⃣ Lidl → XX.XX euros
3️⃣ Aldi → XX.XX euros
4️⃣ Carrefour → XX.XX euros
5️⃣ Consum → XX.XX euros
6️⃣ Herbolario Navarro → XX.XX euros
7️⃣ Supercor → XX.XX euros

💚 MAS BARATO: [nombre] → XX.XX euros

¿Con cuál te quedas? 👆"""
        totales = main.completar(client, [
            {"role":"system","content":"Experto en precios de supermercados espanoles. Devuelve solo el formato pedido, sin texto extra."},
            {"role":"user","content":prompt}
        ], max_tokens=350)
        s = cargar_sesion(phone); s["estado"] = "elegir_super_comparativa"; guardar_sesion(phone, s)
        send(phone, totales)
    except Exception as e:
        send(phone, "❌ Error calculando precios. Inténtalo de nuevo.")
        s = cargar_sesion(phone); s["estado"] = "chat"; guardar_sesion(phone, s)

@app.route("/webhook", methods=["POST"])
def webhook():
    phone = request.form.get("From")
    message = request.form.get("Body","").strip()
    media_url = request.form.get("MediaUrl0")
    media_type = request.form.get("MediaContentType0","image/jpeg")
    tl = message.lower().strip()

    sesion = cargar_sesion(phone)
    estado = sesion.get("estado","inicio")
    memoria = main.cargar_memoria_usuario(phone)
    perfil = memoria.get("perfil",{})

    if tl in ("hola","buenas","hey","ey","buenos dias","buenas tardes","buenas noches","q tal","que tal","hi","hello"):
        nombre_saludo = perfil.get('nombre', '').strip()
        saludo = f"¡Hola, {nombre_saludo}! 👋" if nombre_saludo else "¡Hola! 👋"
        return enviar(f"{saludo}\n\n{MENU}")

    if tl in ("reset","reiniciar","nuevo perfil","empezar de nuevo","volver a empezar","comenzar de nuevo","empezar","inicio"):
        main.reset_memoria_tras_nuevo(memoria)
        guardar_sesion(phone, {"estado":"inicio","perfil_tmp":{},"tipo_plan":None,"onboarding_step":0})
        return enviar("¡Hola! Soy ZIA, tu nutricionista personal 🥗\n\n¿El plan es para ti solo o para toda tu familia?\n\n1️⃣ Plan individual\n2️⃣ Plan familiar")

    if estado == "inicio":
        if tl in ("1","para mi","para mi solo","solo","individual","yo"):
            sesion.update({"tipo_plan":"individual","estado":"onboarding","onboarding_step":0,"perfil_tmp":{"tipo_plan":"individual"}})
            guardar_sesion(phone, sesion)
            return enviar(f"¡Perfecto! 🎯 Vamos a crear tu plan personalizado\n\n{PREGUNTAS_INDIVIDUAL[0][1]}")
        if tl in ("2","familia","familiar","todos","para mi familia"):
            sesion.update({"tipo_plan":"familiar","estado":"onboarding","onboarding_step":0,"perfil_tmp":{"tipo_plan":"familiar"}})
            guardar_sesion(phone, sesion)
            return enviar(f"¡Perfecto! 👨‍👩‍👧‍👦 Vamos a crear el plan para toda la familia\n\n{PREGUNTAS_FAMILIAR[0][1]}")
        return enviar("¡Hola! Soy ZIA, tu nutricionista personal 🥗\n\n¿El plan es para ti solo o para toda tu familia?\n\n1️⃣ Plan individual\n2️⃣ Plan familiar")

    if estado == "onboarding":
        tipo = sesion.get("tipo_plan","individual")
        preguntas = PREGUNTAS_INDIVIDUAL if tipo == "individual" else PREGUNTAS_FAMILIAR
        step = sesion.get("onboarding_step", 0)
        if step >= len(preguntas): step = len(preguntas) - 1
        campo_actual = preguntas[step][0]
        sesion["perfil_tmp"][campo_actual] = procesar_campo(campo_actual, message)
        sesion["onboarding_step"] = step + 1
        if sesion["onboarding_step"] < len(preguntas):
            guardar_sesion(phone, sesion)
            return enviar(preguntas[sesion["onboarding_step"]][1])
        perfil = sesion["perfil_tmp"].copy()
        if "num_personas" not in perfil: perfil["num_personas"] = "1"
        memoria["perfil"] = perfil
        main.guardar_memoria_usuario(phone, memoria)
        sesion["estado"] = "generando_plan"
        guardar_sesion(phone, sesion)
        t = threading.Thread(target=generar_plan_async, args=(phone, perfil, memoria))
        t.daemon = True; t.start()
        return enviar("🚀 Perfecto. Generando tu plan semanal... Dame un momento 🧠")

    if estado == "esperando_cambios":
        if tl in ("no","n","no gracias","nop","nel","nope","no cambies","esta bien","perfecto"):
            sesion["estado"] = "generando_lista"; guardar_sesion(phone, sesion)
            memoria_fresca = main.cargar_memoria_usuario(phone)
            perfil_fresco = memoria_fresca.get("perfil", perfil)
            t = threading.Thread(target=generar_lista_async, args=(phone, perfil_fresco, memoria_fresca))
            t.daemon = True; t.start()
            return enviar("🛒 ¡Perfecto! Preparando tu lista de la compra...")
        if tl in ("si","s","yes","ok","vale","claro","cambiar","quiero cambiar"):
            sesion["estado"] = "escuchando_cambios"; guardar_sesion(phone, sesion)
            return enviar("✏️ Dime qué quieres cambiar.")
        return enviar("¿Quieres cambiar algo? Escribe sí o no 👆")

    if estado == "escuchando_cambios":
        if tl in ("no","nada","no cambiar","sin cambios","esta bien","perfecto","me gusta","no cambies"):
            sesion["estado"] = "generando_lista"; guardar_sesion(phone, sesion)
            memoria_fresca = main.cargar_memoria_usuario(phone)
            perfil_fresco = memoria_fresca.get("perfil", perfil)
            t = threading.Thread(target=generar_lista_async, args=(phone, perfil_fresco, memoria_fresca))
            t.daemon = True; t.start()
            return enviar("🛒 ¡Perfecto! Preparando tu lista de la compra...")
        try:
            client = main.crear_cliente()
            plan_nuevo = main.completar(client, [
                {"role":"system","content":"Eres ZIA. Aplica el cambio y devuelve el plan completo actualizado. PROHIBIDO preguntar nada. Solo el plan. Termina con la cena del domingo."},
                {"role":"user","content":f"Plan actual:\n\n{memoria.get('plan_semanal_actual','')[:6000]}\n\nCAMBIO: {message}\n\nDevuelve el plan completo actualizado."}
            ], max_tokens=3000)
            memoria["plan_semanal_actual"] = plan_nuevo
            memoria["ultimo_plan"] = plan_nuevo
            main.guardar_memoria_usuario(phone, memoria)
            sesion["estado"] = "esperando_cambios"; guardar_sesion(phone, sesion)
            resp = MessagingResponse()
            for p in [plan_nuevo[i:i+1400] for i in range(0, len(plan_nuevo), 1400)]: resp.message(p)
            resp.message("✅ Plan actualizado. ¿Quieres cambiar algo más? (sí/no)")
            return str(resp), 200, {"Content-Type":"text/xml"}
        except Exception as e:
            sesion["estado"] = "esperando_cambios"; guardar_sesion(phone, sesion)
            return enviar("❌ Error al actualizar. Dime de nuevo qué cambiar.")

    if estado == "esperando_pago_o_comparar":
        if tl in ("1","pagar","confirmar","si","ok","vale"):
            sesion["estado"] = "chat"; guardar_sesion(phone, sesion)
            send(phone, f"🛒 Aquí tienes tu enlace para comprar: {us(perfil)}")
            time.sleep(1)
            return enviar(MENU)
        if tl in ("2","comparar","comparar precios"):
            sesion["estado"] = "generando_comparativa"; guardar_sesion(phone, sesion)
            memoria_fresca = main.cargar_memoria_usuario(phone)
            t = threading.Thread(target=generar_comparativa_async, args=(phone, memoria_fresca, perfil))
            t.daemon = True; t.start()
            return enviar("🔍 Calculando precios en supermercados...")
        return enviar(f"¿Qué quieres hacer?\n\n1️⃣ Pagar en {ns(perfil)}\n2️⃣ Comparar precios")

    if estado == "elegir_super_comparativa":
        mapa = {"1":"mercadona","2":"lidl","3":"aldi","4":"carrefour","5":"consum","6":"herbolarionavarro","7":"supercor"}
        cid = mapa.get(tl) or main.detectar_id_supermercado_en_texto(message)
        if cid and cid in main.SUPER_TIENDA_URL:
            nombre_s, url_s = main.SUPER_TIENDA_URL[cid]
            memoria["perfil"]["supermercado"] = nombre_s
            main.guardar_memoria_usuario(phone, memoria)
            send(phone, f"Tu compra en {nombre_s}: {url_s}")
            sesion["estado"] = "chat"; guardar_sesion(phone, sesion)
            time.sleep(1)
            return enviar(MENU)
        sesion["estado"] = "chat"; guardar_sesion(phone, sesion)
        return enviar(f"🛒 Aquí tienes el enlace: {us(perfil)}\n\n{MENU}")

    if media_url:
        try:
            r = requests.get(media_url, auth=(TWILIO_SID, TWILIO_TOKEN), timeout=15)
            if r.status_code != 200:
                return enviar("❌ No pude descargar la imagen. Inténtalo de nuevo.")
            img_b64 = base64.b64encode(r.content).decode("utf-8")
            detected_type = media_type if media_type.startswith("image/") else "image/jpeg"
            from openai import OpenAI as OpenAIClient
            nombre = perfil.get("nombre", "")
            objetivo = perfil.get("objetivo", "")
            system_img = f"Eres ZIA, nutricionista personal. El usuario te manda foto de su nevera. HAZ ESTO: 1) Una linea diciendo que ves. 2) Propón EXACTAMENTE 2 recetas rapidas menos de 20 min con esos ingredientes, con nombre en MAYUSCULAS, ingredientes y pasos cortos. 3) Termina con una frase motivadora e ingeniosa con humor. PROHIBIDO: asteriscos, markdown, ###, negrita, consejos genericos. Solo texto plano. Max 350 palabras. Español. Perfil: nombre={nombre}, objetivo={objetivo}."
            oa_client = OpenAIClient(api_key=os.getenv("OPENAI_API_KEY"))
            resp_img = oa_client.chat.completions.create(
                model="gpt-4o",
                max_tokens=1024,
                messages=[
                    {"role":"system","content":system_img},
                    {"role":"user","content":[
                        {"type":"image_url","image_url":{"url":f"data:{detected_type};base64,{img_b64}"}},
                        {"type":"text","text":"Analiza esta imagen y dame consejos nutricionales personalizados"}
                    ]}
                ]
            )
            respuesta = resp_img.choices[0].message.content or ""
            sesion["estado"] = "chat"; guardar_sesion(phone, sesion)
            return enviar(respuesta[:1400])
        except Exception as e:
            print(f"Error imagen: {e}")
            return enviar("❌ Error analizando la imagen. Inténtalo de nuevo.")

    if tl in ("1","que como hoy","que desayuno hoy","que ceno hoy","que meriendo hoy"):
        spain = pytz.timezone("Europe/Madrid")
        ahora = datetime.datetime.now(spain)
        hora = ahora.hour
        dias = ["LUNES","MARTES","MIERCOLES","JUEVES","VIERNES","SABADO","DOMINGO"]
        hoy = dias[ahora.weekday()]
        if hora < 11: momento = "DESAYUNO"
        elif hora < 14: momento = "COMIDA"
        elif hora < 17: momento = "MERIENDA"
        else: momento = "CENA"
        plan = (memoria.get("plan_semanal_actual") or memoria.get("ultimo_plan") or "").strip()
        if plan:
            try:
                client = main.crear_cliente()
                resumen = main.completar(client, [
                    {"role":"system","content":f"Extrae SOLO el {momento} del {hoy}. Nombre, ingredientes y macros. Max 120 palabras."},
                    {"role":"user","content":f"Plan:\n{plan[:4000]}\nDame solo el {momento.lower()} del {hoy}."}
                ], max_tokens=400)
                sesion["momento_actual"] = momento
                sesion["plato_actual"] = resumen
                sesion["estado"] = "esperando_respuesta_comida"
                guardar_sesion(phone, sesion)
                return enviar(f"Tu {momento.lower()} de hoy ({hoy}):\n\n{resumen}\n\n¿Tienes todo? Responde: sí / no / cámbialo 👆")
            except:
                return enviar("Error leyendo el plan. Escribe reset.")
        return enviar("¡Aún no tienes plan! Escribe reset para crear uno 🚀")

    if tl in ("2","ajustar plan","cambiar plan"):
        sesion["estado"] = "escuchando_cambios"; guardar_sesion(phone, sesion)
        return enviar("✏️ Dime qué quieres cambiar.")

    if tl in ("3","receta","sorprendeme"):
        spain = pytz.timezone("Europe/Madrid")
        horas = datetime.datetime.now(spain).hour
        tipo = "desayuno" if horas < 11 else "comida" if horas < 16 else "merienda" if horas < 18 else "cena"
        try:
            client = main.crear_cliente()
            r = main.completar(client, [
                {"role":"system","content":"Eres ZIA. Propón UNA receta rapida max 20 min. Nombre, ingredientes, pasos cortos, macros. Max 150 palabras. IMPORTANTE: NO uses asteriscos ni markdown. Usa texto plano con MAYUSCULAS para titulos."},
                {"role":"user","content":f"⚡ Receta rápida para {tipo}. Perfil: {json.dumps(perfil)}"}
            ], max_tokens=400)
            sesion["estado"] = "chat"; guardar_sesion(phone, sesion)
            return enviar(f"{r}\n\n¿Tienes los ingredientes? (sí/no) 👆")
        except:
            return enviar(MENU)

    if tl in ("4","nevera","miro mi nevera","foto nevera"):
        return enviar("📸 Mándame la foto de tu nevera y te preparo recetas con lo que tienes.")

    if tl in ("5","compra","hacer la compra","nueva lista"):
        plan = (memoria.get("plan_semanal_actual") or memoria.get("ultimo_plan") or "").strip()
        if not plan:
            sesion["estado"] = "generando_lista"; guardar_sesion(phone, sesion)
            memoria_fresca = main.cargar_memoria_usuario(phone)
            perfil_fresco = memoria_fresca.get("perfil", perfil)
            t = threading.Thread(target=generar_lista_async, args=(phone, perfil_fresco, memoria_fresca))
            t.daemon = True; t.start()
            return enviar("🛒 Generando tu lista de la compra...")
        sesion["estado"] = "generando_lista"; guardar_sesion(phone, sesion)
        memoria_fresca = main.cargar_memoria_usuario(phone)
        perfil_fresco = memoria_fresca.get("perfil", perfil)
        t = threading.Thread(target=generar_lista_async, args=(phone, perfil_fresco, memoria_fresca))
        t.daemon = True; t.start()
        return enviar("🛒 Generando tu lista de la compra...")

    if estado == "esperando_respuesta_comida":
        plato = sesion.get("plato_actual","")
        if tl in ("si","s","yes","ok","vale","perfecto","tengo todo"):
            sesion["estado"] = "chat"; guardar_sesion(phone, sesion)
            return enviar(f"¡Que aproveche! 😋\n\n{MENU}")
        if tl in ("no","n","no tengo","falta","me falta"):
            sesion["estado"] = "esperando_faltante"; guardar_sesion(phone, sesion)
            return enviar("🔍 ¿Qué te falta? Dímelo y yo lo busco")
        if tl in ("cambialo","cambia","cambiar","cambia el plato"):
            try:
                client = main.crear_cliente()
                nuevo = main.completar(client, [
                    {"role":"system","content":"Eres ZIA. Cambia este plato por otro similar con macros parecidos. Solo el nuevo plato. Max 100 palabras."},
                    {"role":"user","content":f"Plato actual:\n{plato}\nDame una alternativa."}
                ], max_tokens=300)
                sesion["plato_actual"] = nuevo; guardar_sesion(phone, sesion)
                return enviar(f"🔄 Te cambio el plato:\n\n{nuevo}\n\n¿Este te va mejor? (sí/no)")
            except:
                sesion["estado"] = "chat"; guardar_sesion(phone, sesion)
                return enviar(MENU)
        sesion["estado"] = "chat"; guardar_sesion(phone, sesion)
        return enviar(MENU)

    if estado == "esperando_faltante":
        producto = message.strip()
        if producto and len(producto) > 1:
            super_url = us(perfil)
            super_nombre = ns(perfil)
            sesion["estado"] = "chat"; guardar_sesion(phone, sesion)
            return enviar(f"✅ Anotado! Cómpralo en {super_nombre}: {super_url}\n\n{MENU}")
        return enviar("Que te falta? Escribelo.")

    client = main.crear_cliente()
    historial = sesion.get("historial", [])
    historial.append({"role":"user","content":message})
    try:
        system_chat = main.system_chat_con_memoria(perfil, memoria)
        system_chat += f"\n\nIMPORTANTE: Max 250 palabras. Solo hablas de nutricion y alimentacion. Si el usuario menciona cambios en su supermercado, presupuesto, alergias u objetivo, actualiza su perfil internamente y confirmaselo. Responde siempre en base a su perfil actualizado. NUNCA digas que no puedes ver fotos o imagenes - SI PUEDES verlas perfectamente cuando el usuario las mande. Si el usuario dice que va a mandar una foto, animale a mandarla directamente.\n\n{MENU}"
        # Detectar cambios de perfil en el mensaje
        tl_msg = message.lower()
        import re as _re
        # Cambio supermercado
        if any(s in tl_msg for s in ["mercadona","lidl","aldi","carrefour","consum","herbolario","supercor"]):
            for sid, (sname, surl) in main.SUPER_TIENDA_URL.items():
                if sid in tl_msg or sname.lower() in tl_msg:
                    perfil["supermercado"] = sname
                    memoria["perfil"] = perfil
                    main.guardar_memoria_usuario(phone, memoria)
                    break
        # Cambio presupuesto
        m_pres = _re.search(r"(\d+)\s*(?:euros?|€)?\s*(?:de\s+)?presupuesto|presupuesto\s+(?:de\s+)?(\d+)|gasto\s+(\d+)|me\s+quedo\s+con\s+(\d+)", tl_msg)
        if m_pres:
            cantidad = next(x for x in m_pres.groups() if x)
            perfil["presupuesto"] = f"{cantidad}€"
            memoria["perfil"] = perfil
            main.guardar_memoria_usuario(phone, memoria)
        # Cambio objetivo
        if any(x in tl_msg for x in ["quiero perder","quiero ganar","quiero mantener","mi objetivo es","cambio mi objetivo"]):
            if "grasa" in tl_msg or "peso" in tl_msg or "adelgazar" in tl_msg:
                perfil["objetivo"] = "Perder grasa"
            elif "musculo" in tl_msg or "músculo" in tl_msg or "masa" in tl_msg:
                perfil["objetivo"] = "Ganar musculo"
            elif "mantener" in tl_msg or "mantenimiento" in tl_msg:
                perfil["objetivo"] = "Mantenimiento"
            elif "sano" in tl_msg or "salud" in tl_msg:
                perfil["objetivo"] = "Comer mas sano"
            elif "energia" in tl_msg or "energía" in tl_msg:
                perfil["objetivo"] = "Mas energia"
            memoria["perfil"] = perfil
            main.guardar_memoria_usuario(phone, memoria)
        respuesta = main.completar(client, [
            {"role":"system","content":system_chat},
            *historial[-20:],
        ], max_tokens=1024)
        historial.append({"role":"assistant","content":respuesta})
        sesion["historial"] = historial[-20:]
        guardar_sesion(phone, sesion)
        main.guardar_memoria_usuario(phone, memoria)
        return enviar(respuesta[:1500])
    except Exception as e:
        return enviar("❌ Error. Inténtalo de nuevo o escribe reset.")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    app.run(host="0.0.0.0", debug=False, port=port)
