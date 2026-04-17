from dotenv import load_dotenv
load_dotenv()
import os, json, threading, requests, base64
import main
import time
from pathlib import Path
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient

app = Flask(__name__)
DATA_DIR = Path("/app/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

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
    ("nombre", "¿Cómo te llamamos?"),
    ("datos_fisicos", "Para personalizar tu plan necesito algunos datos:\nGénero, edad, peso (kg) y altura (cm)\nEjemplo: hombre, 35 años, 80 kg, 178 cm"),
    ("objetivo", "¿Cuál es tu objetivo principal?\n1️⃣ Perder grasa\n2️⃣ Ganar músculo\n3️⃣ Mantenimiento\n4️⃣ Comer más sano\n5️⃣ Más energía"),
    ("presupuesto", "¿Cuánto quieres gastar a la semana en comida? (ej: 80€)"),
    ("supermercado", "¿En qué supermercado/s sueles comprar?\nPuedes elegir varios separados por coma\nEj: Mercadona, Lidl"),
    ("restricciones", "¿Tienes alguna alergia o intolerancia?\nSi no hay ninguna escribe: ninguna"),
    ("tiempo_cocina", "¿Cuánto tiempo tienes para cocinar?\n1️⃣ Menos de 20 minutos\n2️⃣ Entre 20 y 40 minutos\n3️⃣ Tengo tiempo, me gusta cocinar"),
]

PREGUNTAS_FAMILIAR = [
    ("nombre", "¿Cómo te llamamos?"),
    ("num_personas", "¿Cuántas personas coméis en casa?"),
    ("ninos_edades", "¿Hay niños en casa? Si sí indica edades.\nSi no escribe: no"),
    ("gustos_familia", "Cuéntame gustos o comidas favoritas y si alguien no come algo."),
    ("restricciones", "¿Hay alergias o intolerancias?\nSi no hay escribe: ninguna"),
    ("presupuesto", "¿Cuánto queréis gastar a la semana? (ej: 150€)"),
    ("supermercado", "¿En qué supermercado/s soléis comprar?\nPodéis elegir varios separados por coma\nEj: Mercadona, Lidl"),
    ("tiempo_cocina", "¿Cuánto tiempo tenéis para cocinar?\n1️⃣ Menos de 20 minutos\n2️⃣ Entre 20 y 40 minutos\n3️⃣ Tenemos tiempo, nos gusta cocinar"),
]

MAPA_OBJETIVO = {"1":"Perder grasa","2":"Ganar músculo","3":"Mantenimiento","4":"Comer más sano","5":"Más energía"}
MAPA_TIEMPO = {"1":"menos de 20 minutos","2":"entre 20 y 40 minutos","3":"tengo tiempo, me gusta cocinar"}

def procesar_campo(campo, valor):
    if campo == "objetivo": return MAPA_OBJETIVO.get(valor.strip(), valor)
    if campo == "tiempo_cocina": return MAPA_TIEMPO.get(valor.strip(), valor)
    return valor

def ns(perfil): return main.nombre_supermercado_perfil(perfil)
def us(perfil):
    ids = main.ids_supermercados_detectados(perfil.get("supermercado",""))
    return main.SUPER_TIENDA_URL[ids[0] if ids else "mercadona"][1]

def generar_plan_async(phone, perfil, memoria):
    try:
        client = main.crear_cliente()
        system = main.system_zia_completo().replace(
            "SIEMPRE indica el tiempo de preparación en minutos para cada receta (ej: 15 min).",
            "NO incluyas tiempos de preparación. Termina SIEMPRE con la cena del domingo. PROHIBIDO añadir preguntas, comentarios, valoraciones o frases finales de ningún tipo."
        )
        plan = main.completar(client, [
            {"role":"system","content":system},
            {"role":"user","content":main.mensaje_plan_semanal(perfil, memoria)}
        ], max_tokens=8192)
        memoria["plan_semanal_actual"] = plan
        memoria["ultimo_plan"] = plan
        main.añadir_lista_al_historial(memoria, plan)
        main.guardar_memoria_usuario(phone, memoria)
        s = cargar_sesion(phone)
        s["estado"] = "esperando_cambios"
        guardar_sesion(phone, s)
        for parte in [plan[i:i+1400] for i in range(0, len(plan), 1400)]:
            send(phone, parte)
            time.sleep(2)
        send(phone, "💪 ¿Quieres cambiar algo del plan? (sí/no)")
        
    except Exception as e:
        send(phone, f"Error: {e}\nEscribe reset para empezar.")
        s = cargar_sesion(phone); s["estado"] = "chat"; guardar_sesion(phone, s)

def generar_lista_async(phone, perfil, memoria):
    try:
        client = main.crear_cliente()
        plan_ref = (memoria.get("plan_semanal_actual") or "").strip()
        lista = main.generar_lista_compra_respuesta(client, perfil, plan_ref)
        memoria["lista_compra_actual"] = lista
        memoria["ultimo_plan"] = (plan_ref + "\n\n" + lista).strip()
        main.guardar_memoria_usuario(phone, memoria)
        s = cargar_sesion(phone); s["estado"] = "esperando_pago_o_comparar"; guardar_sesion(phone, s)
        for parte in [lista[i:i+1400] for i in range(0, len(lista), 1400)]:
            send(phone, parte)
        send(phone, f"¿Qué quieres hacer?\n1️⃣ Pagar en {ns(perfil)}\n2️⃣ Comparar precios")
    except Exception as e:
        send(phone, f"Error: {e}")
        s = cargar_sesion(phone); s["estado"] = "chat"; guardar_sesion(phone, s)

def generar_comparativa_async(phone, memoria, perfil):
    try:
        client = main.crear_cliente()
        ref = (memoria.get("ultimo_plan") or "").strip()
        prompt = f"Lista:\n{ref[:10000]}\n\n{main.texto_factores_precio_supermercados()}\n\nMuestra SOLO estas 5 líneas:\n🏪 Mercadona → XX.XX€\n🏪 Lidl → XX.XX€\n🏪 Aldi → XX.XX€\n🏪 Carrefour → XX.XX€\n🏪 Consum → XX.XX€\nAñade ⭐ MÁS ECONÓMICO al más barato."
        totales = main.completar(client, [
            {"role":"system","content":"Solo las líneas pedidas."},
            {"role":"user","content":prompt}
        ], temperature=0.2, max_tokens=300)
        s = cargar_sesion(phone); s["estado"] = "elegir_super_comparativa"; guardar_sesion(phone, s)
        send(phone, totales)
        ids = main.ids_supermercados_detectados(perfil.get("supermercado",""))
        cid_hab = ids[0] if ids else "mercadona"
        todos = [("1","mercadona","Mercadona"),("2","lidl","Lidl"),("3","aldi","Aldi"),("4","carrefour","Carrefour"),("5","consum","Consum")]
        opciones = [f"{n}️⃣ {nombre}" for n,cid,nombre in todos if cid != cid_hab]
        send(phone, "¿Con cuál te quedas? Elige un número o escribe el nombre:\n" + "\n".join(opciones))
    except Exception as e:
        send(phone, f"Error: {e}")

@app.route("/webhook", methods=["POST"])
def webhook():
    phone = request.form.get("From")
    message = request.form.get("Body", "").strip()
    media_url = request.form.get("MediaUrl0")
    media_type = request.form.get("MediaContentType0", "image/jpeg")
    tl = message.lower().strip()

    sesion = cargar_sesion(phone)
    estado = sesion.get("estado", "inicio")
    memoria = main.cargar_memoria_usuario(phone)
    perfil = memoria.get("perfil", {})

    if tl in ("reset", "reiniciar", "nuevo perfil", "empezar de nuevo"):
        main.reset_memoria_tras_nuevo(memoria)
        guardar_sesion(phone, {"estado":"inicio","perfil_tmp":{},"tipo_plan":None,"onboarding_step":0})
        return enviar("*¡Hola! Soy ZIA, tu nutricionista personal* 🥗\n\n¿El plan es para ti solo o para toda tu familia?\n1️⃣ Para mí solo\n2️⃣ Para mi familia")

    if estado == "inicio":
        if tl in ("1","para mi","para mí","solo","individual","yo"):
            sesion.update({"tipo_plan":"individual","estado":"onboarding","onboarding_step":0,"perfil_tmp":{"tipo_plan":"individual"}})
            guardar_sesion(phone, sesion)
            return enviar(f"Perfecto 💪\n\n{PREGUNTAS_INDIVIDUAL[0][1]}")
        if tl in ("2","familia","familiar","todos","para mi familia"):
            sesion.update({"tipo_plan":"familiar","estado":"onboarding","onboarding_step":0,"perfil_tmp":{"tipo_plan":"familiar"}})
            guardar_sesion(phone, sesion)
            return enviar(f"Perfecto 👨‍👩‍👧\n\n{PREGUNTAS_FAMILIAR[0][1]}")
        return enviar("*¡Hola! Soy ZIA, tu nutricionista personal* 🥗\n\n¿El plan es para ti solo o para toda tu familia?\n1️⃣ Para mí solo\n2️⃣ Para mi familia")

    if estado == "onboarding":
        tipo = sesion.get("tipo_plan","individual")
        preguntas = PREGUNTAS_INDIVIDUAL if tipo == "individual" else PREGUNTAS_FAMILIAR
        step = sesion.get("onboarding_step", 0)
        campo_actual = preguntas[step][0]
        if campo_actual == "objetivo" and any(x in message.lower() for x in (" y ",","," también")):
            return enviar("Elige solo tu objetivo PRINCIPAL:\n1️⃣ Perder grasa\n2️⃣ Ganar músculo\n3️⃣ Mantenimiento\n4️⃣ Comer más sano\n5️⃣ Más energía")
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
        return enviar("✅ Perfecto. Generando tu plan semanal... Dame un momento 🔄")

    if estado == "esperando_cambios":
        if tl in ("no","n","nop","no gracias"):
            sesion["estado"] = "generando_lista"; guardar_sesion(phone, sesion)
            t = threading.Thread(target=generar_lista_async, args=(phone, perfil, memoria))
            t.daemon = True; t.start()
            return enviar("Perfecto 👌 Preparando tu lista de la compra...")
        sesion["estado"] = "escuchando_cambios"; guardar_sesion(phone, sesion)
        return enviar("Dime qué quieres cambiar.")

    if estado == "esperando_respuesta_comida":
        momento = sesion.get("momento_actual", "comida")
        plato = sesion.get("plato_actual", "")
        if tl in ("1", "tengo todo", "perfecto", "lo tengo"):
            sesion["estado"] = "chat"; guardar_sesion(phone, sesion)
            return enviar(f"A por ello 💪 Que aproveche.\n\n¿Qué necesitas ahora?\n1️⃣ Dime qué como hoy 🍽️\n2️⃣ Ajustar mi plan 💪\n3️⃣ Sorpréndeme con una receta ⚡\n4️⃣ Miro mi nevera 📸\n5️⃣ Hacer la compra 🛒")
        if tl in ("2", "me falta", "falta algo", "necesito algo"):
            sesion["estado"] = "chat"; guardar_sesion(phone, sesion)
            return enviar(f"¿Qué te falta para el {momento.lower()}? Dímelo y te lo busco.")
        if tl in ("3", "cambiame", "cambiar", "otro plato"):
            sesion["estado"] = "chat"; guardar_sesion(phone, sesion)
            client = main.crear_cliente()
            nuevo = main.completar(client, [
                {"role":"system","content":"Eres ZIA. Cambia este plato por otro similar manteniendo los mismos macros aproximados. Devuelve solo el nuevo plato con ingredientes y macros. Máximo 100 palabras."},
                {"role":"user","content":f"Plato actual:\n{plato}\n\nDame una alternativa con macros similares."}
            ], max_tokens=300)
            return enviar(f"Te cambio el plato 🔄\n\n{nuevo}\n\n¿Este te va mejor?\n1️⃣ Perfecto ✅\n2️⃣ Dame otro 🔄")
        if tl in ("4", "tengo poco", "foto nevera", "nevera"):
            sesion["estado"] = "chat"; guardar_sesion(phone, sesion)
            return enviar("Mándame la foto de tu nevera 📸 y te preparo algo rico con lo que tienes.")
        sesion["estado"] = "chat"; guardar_sesion(phone, sesion)

    if estado == "escuchando_cambios":
        try:
            client = main.crear_cliente()
            system_cambio = "Eres ZIA nutricionista. Tu UNICA tarea: aplicar el cambio pedido y devolver el plan completo actualizado. PROHIBIDO preguntar nada, hacer comentarios, preguntar como se siente el usuario o si quiere continuar. Solo el plan con el cambio aplicado."
            plan_nuevo = main.completar(client, [
                {"role":"system","content":system_cambio},
                {"role":"user","content":f"Plan actual:\n{memoria.get('plan_semanal_actual','')[:6000]}\n\nCAMBIO: {message}\n\nDevuelve el plan completo de 7 dias con el cambio aplicado. Sin preguntas ni comentarios al final. Termina con la cena del domingo."}
            ], max_tokens=6000)
            memoria["plan_semanal_actual"] = plan_nuevo
            memoria["ultimo_plan"] = plan_nuevo
            main.guardar_memoria_usuario(phone, memoria)
            sesion["estado"] = "esperando_cambios"; guardar_sesion(phone, sesion)
            resp = MessagingResponse()
            for p in [plan_nuevo[i:i+1400] for i in range(0,len(plan_nuevo),1400)]: resp.message(p)
            resp.message("✅ Plan actualizado. ¿Quieres cambiar algo más? (sí/no)")
            return str(resp), 200, {"Content-Type":"text/xml"}
        except Exception as e:
            sesion["estado"] = "esperando_cambios"; guardar_sesion(phone, sesion)
            return enviar(f"Error: {e}")

    if estado == "esperando_si_lista":
        if tl in ("si","sí","s","yes","ok","vale","claro"):
            sesion["estado"] = "generando_lista"; guardar_sesion(phone, sesion)
            t = threading.Thread(target=generar_lista_async, args=(phone, perfil, memoria))
            t.daemon = True; t.start()
            return enviar("⏳ Preparando tu lista...")
        if tl in ("no","n","nop"):
            sesion["estado"] = "chat"; guardar_sesion(phone, sesion)
            return enviar("De acuerdo. Cuando quieras la lista dímelo 😊")
        return enviar(f"Escribe sí para la lista de {ns(perfil)} o no para continuar.")

    if estado == "esperando_pago_o_comparar":
        if tl in ("1","pagar","confirmar","si","sí","ok","vale"):
            sesion["estado"] = "chat"; guardar_sesion(phone, sesion)
        send(phone, f"✅ Aquí tienes el enlace:\n\n🛒 {ns(perfil)} → {us(perfil)}")
        return enviar("¿Qué necesitas ahora?\n1️⃣ Dime qué como hoy 🍽️\n2️⃣ Ajustar mi plan 💪\n3️⃣ Sorpréndeme con una receta ⚡\n4️⃣ Miro mi nevera 📸\n5️⃣ Hacer la compra 🛒")
        if tl in ("2","comparar","comparar precios"):
            sesion["estado"] = "generando_comparativa"; guardar_sesion(phone, sesion)
            t = threading.Thread(target=generar_comparativa_async, args=(phone, memoria, perfil))
            t.daemon = True; t.start()
            return enviar("⏳ Calculando precios en 5 supermercados...")
        return enviar(f"1️⃣ Pagar en {ns(perfil)}\n2️⃣ Comparar precios")

    if estado == "elegir_super_comparativa":
        mapa = {"1":"mercadona","2":"lidl","3":"aldi","4":"carrefour","5":"consum"}
        cid = mapa.get(tl) or main.detectar_id_supermercado_en_texto(message)
        if cid and cid in main.SUPER_TIENDA_URL:
            nombre_c, url_c = main.SUPER_TIENDA_URL[cid]
            sesion["estado"] = "chat"; guardar_sesion(phone, sesion)
        send(phone, f"✅ Tu compra en {nombre_c}.\n\n🛒 {nombre_c} → {url_c}")
        return enviar("¿Qué necesitas ahora?\n1️⃣ Dime qué como hoy 🍽️\n2️⃣ Ajustar mi plan 💪\n3️⃣ Sorpréndeme con una receta ⚡\n4️⃣ Miro mi nevera 📸\n5️⃣ Hacer la compra 🛒")
        return enviar("Elige:\n1️⃣ Mercadona\n2️⃣ Lidl\n3️⃣ Aldi\n4️⃣ Carrefour\n5️⃣ Consum")

    if media_url:
        try:
            r = requests.get(media_url, auth=(TWILIO_SID, TWILIO_TOKEN))
            img_b64 = base64.b64encode(r.content).decode("utf-8")
            client = main.crear_cliente()
            respuesta = main.completar(client, [
                {"role":"system","content":"Eres ZIA, nutricionista personal experta. PUEDES VER Y ANALIZAR FOTOS perfectamente. Si es nevera o despensa: identifica todos los alimentos visibles y propón 2 recetas saludables con formato simple: NOMBRE, ingredientes en una línea, pasos numerados muy cortos. Sin markdown, sin asteriscos, sin ###. Si no es nevera: da consejo nutricional breve. Máximo 300 palabras. Español, motivador."},
                {"role":"user","content":[
                    {"type":"image_url","image_url":{"url":f"data:{media_type};base64,{img_b64}"}},
                    {"type":"text","text":message or "Analiza esta imagen"}
                ]}
            ], max_tokens=2048)
            return enviar(respuesta[:1500])
        except Exception as e:
            return enviar(f"Error: {e}")

    # MENU PRINCIPAL - respuestas numericas
    if tl in ("1", "ver plan", "mi plan", "ver mi plan", "que como", "que ceno", "que desayuno", "que meriendo"):
        import datetime, pytz
        spain = pytz.timezone("Europe/Madrid")
        ahora = datetime.datetime.now(spain)
        hora = ahora.hour
        dias = ["LUNES","MARTES","MIÉRCOLES","JUEVES","VIERNES","SÁBADO","DOMINGO"]
        hoy = dias[ahora.weekday()]
        if 6 <= hora < 11:
            momento = "DESAYUNO"; emoji = "☀️"
        elif 11 <= hora < 16:
            momento = "COMIDA"; emoji = "🥗"
        elif 16 <= hora < 18:
            momento = "MERIENDA"; emoji = "🍎"
        elif 18 <= hora < 24:
            momento = "CENA"; emoji = "🍽️"
        else:
            momento = "CENA"; emoji = "🌙"
        plan = (memoria.get("plan_semanal_actual") or memoria.get("ultimo_plan") or "").strip()
        if plan:
            client = main.crear_cliente()
            resumen = main.completar(client, [
                {"role":"system","content":f"Extrae SOLO el {momento} del {hoy}. Nombre del plato, ingredientes con gramos y macros (P/C/G/kcal). Max 120 palabras."},
                {"role":"user","content":f"Plan:\n{plan[:6000]}\n\nDame solo el {momento} del {hoy}."}
            ], max_tokens=400)
            sesion["estado"] = "esperando_respuesta_comida"
            sesion["momento_actual"] = momento
            sesion["plato_actual"] = resumen
            guardar_sesion(phone, sesion)
            return enviar(f"{emoji} Tu {momento.lower()} de hoy ({hoy}):\n\n{resumen}\n\n¿Todo listo?\n1️⃣ Tengo todo ✅\n2️⃣ Me falta algo 🛒\n3️⃣ Cámbiame este plato 🔄\n4️⃣ Foto de mi nevera 📸")
        return enviar("Aún no tienes un plan 💪 Escribe reset para crear uno.")

    if tl in ("2", "cambiar plan", "cambiar algo"):
        sesion["estado"] = "escuchando_cambios"
        guardar_sesion(phone, sesion)
        return enviar("Dime qué quieres cambiar del plan.")

    if tl in ("3", "ideas para hoy", "receta rapida", "receta rápida"):
        sesion["estado"] = "chat"
        guardar_sesion(phone, sesion)
        # Cae al chat general con contexto
        message = "Dame una idea de receta rápida para hoy según mi plan y objetivo"

    if tl in ("4", "foto nevera", "foto de mi nevera"):
        return enviar("Mándame la foto de tu nevera o despensa 📸 y te preparo recetas con lo que tienes.")

    if tl in ("5", "nueva lista", "nueva lista de la compra"):
        sesion["estado"] = "generando_lista"
        guardar_sesion(phone, sesion)
        t = threading.Thread(target=generar_lista_async, args=(phone, perfil, memoria))
        t.daemon = True; t.start()
        return enviar("⏳ Generando tu nueva lista de la compra...")

    client = main.crear_cliente()
    historial = sesion.get("historial", [])
    historial.append({"role":"user","content":message})
    try:
        respuesta = main.completar(client, [
            {"role":"system","content":main.system_chat_con_memoria(perfil, memoria)+"\n\nCUANDO EL USUARIO DIGA gracias, de nada, ok, perfecto, genial o cualquier cierre de conversacion, responde con UNA frase corta y amable y luego muestra SIEMPRE este menu exacto:\n\n¿Qué necesitas ahora?\n1️⃣ Dime qué como hoy 🍽️\n2️⃣ Ajustar mi plan 💪\n3️⃣ Sorpréndeme con una receta ⚡\n4️⃣ Miro mi nevera 📸\n5️⃣ Hacer la compra 🛒\n\nIMPORTANTE: WhatsApp. Máximo 300 palabras."},
            *historial[-20:]
        ], max_tokens=1024)
        historial.append({"role":"assistant","content":respuesta})
        sesion["historial"] = historial[-20:]
        guardar_sesion(phone, sesion)
        main.guardar_memoria_usuario(phone, memoria)
        return enviar(respuesta[:1500])
    except Exception as e:
        return enviar(f"Error: {e}")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    app.run(host="0.0.0.0", debug=False, port=port)
