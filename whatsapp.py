from dotenv import load_dotenv
load_dotenv()
import os, json, threading, requests, base64
import main
import time
import datetime
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
    ("datos_fisicos", "Para personalizar tu plan necesito algunos datos:\nGenero, edad, peso (kg) y altura (cm)\nEjemplo: hombre, 36 anos, 80 kg, 175 cm"),
    ("objetivo", "Cual es tu objetivo principal?\n1 Perder grasa\n2 Ganar musculo\n3 Mantenimiento\n4 Comer mas sano\n5 Mas energia"),
    ("presupuesto", "💰 ¿Cuánto quieres gastar a la semana en comida? (ej: 80€)"),
    ("supermercado", "En que supermercado/s sueles comprar?\nEj: Mercadona, Lidl"),
    ("restricciones", "Tienes alguna alergia o intolerancia?\nSi no hay ninguna escribe: ninguna"),
    ("tiempo_cocina", "Cuanto tiempo tienes para cocinar?\n1 Menos de 20 minutos\n2 Entre 20 y 40 minutos\n3 Tengo tiempo, me gusta cocinar"),
]

PREGUNTAS_FAMILIAR = [
    ("nombre", "👋 ¿Cómo te llamamos?"),
    ("num_personas", "👨‍👩‍👧‍👦 ¿Cuántas personas coméis en casa?"),
    ("ninos_edades", "Hay ninos en casa? Si es asi indica edades.\nSi no escribe: no"),
    ("gustos_familia", "Cuales son los gustos o comidas favoritas de la familia?\nSi alguien no come algo, indicalo"),
    ("restricciones", "Hay alergias o intolerancias en la familia?\nSi no hay ninguna escribe: ninguna"),
    ("presupuesto", "💰 ¿Cuánto queréis gastar a la semana? (ej: 150€)"),
    ("supermercado", "En que supermercado/s soleis comprar?\nEj: Mercadona, Lidl"),
    ("tiempo_cocina", "Cuanto tiempo teneis para cocinar?\n1 Menos de 20 minutos\n2 Entre 20 y 40 minutos\n3 Tenemos tiempo, nos gusta cocinar"),
]

MAPA_OBJETIVO = {"1":"Perder grasa","2":"Ganar musculo","3":"Mantenimiento","4":"Comer mas sano","5":"Mas energia"}
MAPA_TIEMPO = {"1":"menos de 20 minutos","2":"entre 20 y 40 minutos","3":"tiempo libre, me gusta cocinar"}

def procesar_campo(campo, valor):
    if campo == "objetivo": return MAPA_OBJETIVO.get(valor.strip(), valor)
    if campo == "tiempo_cocina": return MAPA_TIEMPO.get(valor.strip(), valor)
    return valor

def ns(perfil): return main.nombre_supermercado_perfil(perfil)
def us(perfil):
    ids = main.ids_supermercados_detectados(perfil.get("supermercado",""))
    return main.SUPER_TIENDA_URL[ids[0]] if ids else "Mercadona"

def generar_plan_async(phone, perfil, memoria):
    try:
        client = main.crear_cliente()
        system = main.system_zia_completo()
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
        for parte in [plan[i:i+1400] for i in range(0, len(plan), 1400)]:
            send(phone, parte)
            time.sleep(1)
        send(phone, "💪 ¿Quieres cambiar algo del plan? (sí/no)")
    except Exception as e:
        send(phone, "Error generando plan. Escribe reset para empezar.")
        s = cargar_sesion(phone); s["estado"] = "chat"; guardar_sesion(phone, s)

def generar_lista_async(phone, perfil, memoria):
    try:
        plan_ref = memoria.get("plan_semanal_actual") or memoria.get("ultimo_plan") or ""
        if not plan_ref:
            send(phone, "No tengo plan. Escribe reset para crear uno")
            s = cargar_sesion(phone); s["estado"] = "chat"; guardar_sesion(phone, s)
            return
        lista = main.generar_lista_compra_respuesta(client=main.crear_cliente(), perfil=perfil, plan_texto=plan_ref)
        memoria["lista_compra_actual"] = lista
        memoria["ultimo_plan"] = (plan_ref + "\n\n" + lista).strip()
        main.guardar_memoria_usuario(phone, memoria)
        s = cargar_sesion(phone); s["estado"] = "esperando_pago_o_comparar"; guardar_sesion(phone, s)
        for parte in [lista[i:i+1400] for i in range(0, len(lista), 1400)]:
            send(phone, parte)
            time.sleep(1)
        send(phone, f"Que quieres hacer?\n\n1 Pagar en {ns(perfil)}\n2 Comparar precios")
    except Exception as e:
        send(phone, "Error generando lista. Intentalo de nuevo.")
        s = cargar_sesion(phone); s["estado"] = "chat"; guardar_sesion(phone, s)

def generar_comparativa_async(phone, memoria, perfil):
    try:
        client = main.crear_cliente()
        prompt = f"Lista pedidos.\n{main.texto_factores_precio_supermercados()}\n\nMuestra SOLO 5 lineas:\nMercadona XX.XX euros\nLidl XX.XX euros\nAldi XX.XX euros\nCarrefour XX.XX euros\nConsum XX.XX euros\nMAS ECONOMICO al mas barato."
        totales = main.completar(client, [
            {"role":"system","content":"Solo las 5 líneas pedidas con el TOTAL de toda la lista de la compra en cada supermercado. Sin texto extra."},
            {"role":"user","content":prompt}
        ], max_tokens=250)
        s = cargar_sesion(phone); s["estado"] = "elegir_super_comparativa"; guardar_sesion(phone, s)
        send(phone, totales)
        send(phone, "Con cual te quedas? Escribe el numero.")
    except Exception as e:
        send(phone, "Error calculando precios. Intentalo de nuevo.")
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

    if tl in ("reset","reiniciar","nuevo perfil","empezar de nuevo"):
        main.reset_memoria_tras_nuevo(memoria)
        guardar_sesion(phone, {"estado":"inicio","perfil_tmp":{},"tipo_plan":None,"onboarding_step":0})
        return enviar("¡Hola! Soy ZIA, tu nutricionista personal 🥗\n\n¿El plan es para ti solo o para toda tu familia?\n\n1️⃣ Para mí solo\n2️⃣ Para mi familia")

    if estado == "inicio":
        if tl in ("1","para mi","para mi solo","solo","individual","yo"):
            sesion.update({"tipo_plan":"individual","estado":"onboarding","onboarding_step":0,"perfil_tmp":{"tipo_plan":"individual"}})
            guardar_sesion(phone, sesion)
            return enviar(f"¡Perfecto! 🎯\n\n{PREGUNTAS_INDIVIDUAL[0][1]}")
        if tl in ("2","familia","familiar","todos","para mi familia"):
            sesion.update({"tipo_plan":"familiar","estado":"onboarding","onboarding_step":0,"perfil_tmp":{"tipo_plan":"familiar"}})
            guardar_sesion(phone, sesion)
            return enviar(f"¡Perfecto! Vamos a crear el plan para toda la familia 👨‍👩‍👧‍👦\n\n{PREGUNTAS_FAMILIAR[0][1]}")
        return enviar("¡Hola! Soy ZIA, tu nutricionista personal 🥗\n\n¿El plan es para ti solo o para toda tu familia?\n\n1️⃣ Para mí solo\n2️⃣ Para mi familia")

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
        if tl in ("no","n","no gracias","nop","nel","nope"):
            sesion["estado"] = "generando_lista"; guardar_sesion(phone, sesion)
            memoria_fresca = main.cargar_memoria_usuario(phone)
            perfil_fresco = memoria_fresca.get("perfil", perfil)
            t = threading.Thread(target=generar_lista_async, args=(phone, perfil_fresco, memoria_fresca))
            t.daemon = True; t.start()
            return enviar("¡Perfecto! 🛒 Preparando tu lista de la compra...")
        if tl in ("si","s","yes","ok","vale","claro","quiero cambiar","cambiar"):
            sesion["estado"] = "escuchando_cambios"; guardar_sesion(phone, sesion)
            return enviar("Dime que quieres cambiar.")
        return enviar("Quieres cambiar algo? Escribe si o no.")

    if estado == "escuchando_cambios":
        if tl in ("no","nada","no cambiar","sin cambios","esta bien","perfecto","me gusta","no cambies"):
            sesion["estado"] = "generando_lista"; guardar_sesion(phone, sesion)
            t = threading.Thread(target=generar_lista_async, args=(phone, perfil, memoria))
            t.daemon = True; t.start()
            return enviar("¡Perfecto! 🛒 Preparando tu lista de la compra...")
        try:
            client = main.crear_cliente()
            plan_nuevo = main.completar(client, [
                {"role":"system","content":"Eres ZIA Nutricionista. Aplica el cambio y devuelve el plan completo actualizado. PROHIBIDO preguntar nada. Solo el plan. Termina con la cena del domingo."},
                {"role":"user","content":f"Plan actual:\n\n{memoria.get(chr(112)+chr(108)+chr(97)+chr(110)+chr(95)+chr(115)+chr(101)+chr(109)+chr(97)+chr(110)+chr(97)+chr(108)+chr(95)+chr(97)+chr(99)+chr(116)+chr(117)+chr(97)+chr(108),'')[:6000]}\n\nCAMBIO: {message}\n\nDevuelve el plan completo actualizado."}
            ], max_tokens=3000)
            memoria["plan_semanal_actual"] = plan_nuevo
            memoria["ultimo_plan"] = plan_nuevo
            main.guardar_memoria_usuario(phone, memoria)
            sesion["estado"] = "esperando_cambios"; guardar_sesion(phone, sesion)
            resp = MessagingResponse()
            for p in [plan_nuevo[i:i+1400] for i in range(0, len(plan_nuevo), 1400)]: resp.message(p)
            resp.message("Plan actualizado. Quieres cambiar algo mas? (si/no)")
            return str(resp), 200, {"Content-Type":"text/xml"}
        except Exception as e:
            sesion["estado"] = "esperando_cambios"; guardar_sesion(phone, sesion)
            return enviar("Error al actualizar. Dime de nuevo que cambiar.")

    if estado == "esperando_pago_o_comparar":
        if tl in ("1","pagar","confirmar","si","ok","vale"):
            sesion["estado"] = "chat"; guardar_sesion(phone, sesion)
            send(phone, f"Aqui tienes el enlace: {us(perfil)}")
            time.sleep(1)
            return enviar(MENU)
        if tl in ("2","comparar","comparar precios","comparar precios"):
            sesion["estado"] = "generando_comparativa"; guardar_sesion(phone, sesion)
            t = threading.Thread(target=generar_comparativa_async, args=(phone, memoria, perfil))
            t.daemon = True; t.start()
            return enviar("Calculando precios en supermercados...")
        return enviar(f"1 Pagar en {ns(perfil)}\n2 Comparar precios")

    if estado == "elegir_super_comparativa":
        mapa = {"1":"Mercadona","2":"Lidl","3":"Aldi","4":"Carrefour","5":"Consum"}
        cid = mapa.get(tl) or main.detectar_id_supermercado_en_texto(message)
        if cid and cid in main.SUPER_TIENDA_URL:
            nombre_s, url_s = main.SUPER_TIENDA_URL[cid]
            send(phone, f"Tu compra en {nombre_s}: {url_s}")
            sesion["estado"] = "chat"; guardar_sesion(phone, sesion)
            time.sleep(1)
            return enviar(MENU)
        return enviar("Elige un numero:\n1 Mercadona\n2 Lidl\n3 Aldi\n4 Carrefour\n5 Consum")

    if media_url:
        try:
            r = requests.get(media_url, auth=(TWILIO_SID, TWILIO_TOKEN))
            img_b64 = base64.b64encode(r.content).decode("utf-8")
            client = main.crear_cliente()
            respuesta = main.completar(client, [
                {"role":"system","content":"Eres ZIA, nutricionista. Analiza la imagen y da consejos nutricionales. Max 300 palabras."},
                {"role":"user","content":[
                    {"type":"image","source":{"type":"base64","media_type":media_type,"data":img_b64}},
                    {"type":"text","text":"Analiza esta imagen"}
                ]}
            ], max_tokens=2048)
            sesion["estado"] = "chat"; guardar_sesion(phone, sesion)
            return enviar(respuesta[:1400] + f"\n\n{MENU}")
        except Exception as e:
            return enviar("Error analizando la imagen. Intentalo de nuevo.")

    if tl in ("1","que como hoy","que desayuno hoy"):
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
                sesion["estado"] = "esperando_respuesta_comida"
                sesion["momento_actual"] = momento
                sesion["plato_actual"] = resumen
                guardar_sesion(phone, sesion)
                return enviar(f"Tu {momento.lower()} de hoy ({hoy}):\n\n{resumen}\n\nTienes todo? Responde: si / no / cambialo")
            except:
                return enviar("Error leyendo el plan. Escribe reset.")
        return enviar("No tienes plan aun. Escribe reset para crear uno")

    if tl in ("2","ajustar plan","cambiar plan"):
        sesion["estado"] = "escuchando_cambios"; guardar_sesion(phone, sesion)
        return enviar("Dime que quieres cambiar.")

    if tl in ("3","receta","sorprendeme"):
        spain = pytz.timezone("Europe/Madrid")
        horas = datetime.datetime.now(spain).hour
        tipo = "desayuno" if horas < 11 else "comida" if horas < 16 else "merienda" if horas < 18 else "cena"
        try:
            client = main.crear_cliente()
            r = main.completar(client, [
                {"role":"system","content":"Eres ZIA. Propón UNA receta rapida max 20 min. Nombre, ingredientes, pasos cortos, macros. Max 150 palabras."},
                {"role":"user","content":f"Receta rapida para {tipo}. Perfil: {json.dumps(perfil)}"}
            ], max_tokens=400)
            sesion["estado"] = "chat"; guardar_sesion(phone, sesion)
            return enviar(f"{r}\n\nTienes los ingredientes? (si/no)")
        except:
            return enviar(MENU)

    if tl in ("4","nevera","miro mi nevera","foto nevera"):
        return enviar("Mandame la foto de tu nevera y te preparo recetas con lo que tienes.")

    if tl in ("5","compra","hacer la compra","nueva lista"):
        plan = (memoria.get("plan_semanal_actual") or memoria.get("ultimo_plan") or "").strip()
        if not plan:
            return enviar("Necesito tu plan primero. Escribe reset")
        sesion["estado"] = "generando_lista"; guardar_sesion(phone, sesion)
        t = threading.Thread(target=generar_lista_async, args=(phone, perfil, memoria))
        t.daemon = True; t.start()
        return enviar("Generando tu lista...")

    client = main.crear_cliente()
    historial = sesion.get("historial", [])
    historial.append({"role":"user","content":message})
    try:
        system_chat = main.system_chat_con_memoria(perfil, memoria)
        system_chat += f"\n\nIMPORTANTE: WhatsApp. Max 250 palabras. Solo hablas de nutricion y alimentacion.\n\n{MENU}"
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
        return enviar("Error. Intentalo de nuevo o escribe reset.")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    app.run(host="0.0.0.0", debug=False, port=port)
