from dotenv import load_dotenv
load_dotenv()
import os
import threading
import main
import requests
import base64
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient

app = Flask(__name__)
sesiones = {}

TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

def enviar_mensaje_twilio(to, texto):
    """Envía mensaje proactivo via Twilio (para respuestas asíncronas)."""
    try:
        cl = TwilioClient(TWILIO_SID, TWILIO_TOKEN)
        # Dividir en partes si es muy largo
        partes = [texto[i:i+1500] for i in range(0, len(texto), 1500)]
        for parte in partes:
            cl.messages.create(body=parte, from_=TWILIO_FROM, to=to)
    except Exception as e:
        print(f"Error enviando mensaje: {e}")

def sesion_nueva():
    memoria = main.cargar_memoria()
    return {
        "memoria": memoria,
        "historial": [],
        "estado": "inicio",
        "perfil_tmp": {},
        "tipo_plan": None,
        "onboarding_step": 0,
    }

def enviar(texto):
    resp = MessagingResponse()
    # Dividir si es muy largo
    partes = [texto[i:i+1500] for i in range(0, len(texto), 1500)]
    for parte in partes:
        resp.message(parte)
    return str(resp), 200, {"Content-Type": "text/xml"}

PREGUNTAS_INDIVIDUAL = [
    ("nombre", "¿Cómo te llamamos?"),
    ("datos_fisicos", "Para personalizar tu plan necesito algunos datos en un mensaje:\nGénero, edad, peso (kg) y altura (cm)\nEjemplo: hombre, 35 años, 80 kg, 178 cm"),
    ("objetivo", "¿Cuál es tu objetivo principal? Elige uno:\n1️⃣ Perder grasa\n2️⃣ Ganar músculo\n3️⃣ Mantenimiento\n4️⃣ Comer más sano\n5️⃣ Más energía"),
    ("presupuesto", "¿Cuánto quieres gastar a la semana en comida? (en euros, ej.: 80)"),
    ("supermercado", "¿En qué supermercado sueles comprar? (Mercadona, Lidl, Carrefour…)"),
    ("restricciones", "¿Tienes alguna alergia o intolerancia?\nSi no hay ninguna, escribe «ninguna»."),
    ("tiempo_cocina", "¿Cuánto tiempo tienes para cocinar al día?\n1️⃣ Menos de 20 minutos\n2️⃣ Entre 20 y 40 minutos\n3️⃣ Tengo tiempo, me gusta cocinar"),
]

PREGUNTAS_FAMILIAR = [
    ("nombre", "¿Cómo te llamamos?"),
    ("num_personas", "¿Cuántas personas coméis en casa?"),
    ("ninos_edades", "¿Hay niños en casa? Si sí, indica edades.\nSi no, escribe «no»."),
    ("gustos_familia", "Cuéntame gustos o comidas favoritas y si alguien no come algo."),
    ("restricciones", "¿Hay alergias o intolerancias?\nSi no hay, escribe «ninguna»."),
    ("presupuesto", "¿Cuánto queréis gastar a la semana? (en euros, ej.: 150)"),
    ("supermercado", "¿En qué supermercado soléis comprar? (Mercadona, Lidl, Carrefour…)"),
    ("tiempo_cocina", "¿Cuánto tiempo tenéis para cocinar?\n1️⃣ Menos de 20 minutos\n2️⃣ Entre 20 y 40 minutos\n3️⃣ Tenemos tiempo, nos gusta cocinar"),
]

MAPA_OBJETIVO = {
    "1": "Perder grasa", "2": "Ganar músculo", "3": "Mantenimiento",
    "4": "Comer más sano", "5": "Más energía",
}
MAPA_TIEMPO = {
    "1": "menos de 20 minutos",
    "2": "entre 20 y 40 minutos",
    "3": "tengo tiempo, me gusta cocinar",
}

def procesar_campo(campo, valor):
    if campo == "objetivo":
        return MAPA_OBJETIVO.get(valor.strip(), valor)
    if campo == "tiempo_cocina":
        return MAPA_TIEMPO.get(valor.strip(), valor)
    return valor

def generar_plan_async(phone, perfil, memoria):
    """Genera el plan en segundo plano y envía el resultado por Twilio."""
    try:
        client = main.crear_cliente()
        enviar_mensaje_twilio(phone, "⏳ Generando tu plan semanal personalizado... Dame un momento.")
        plan, _ = main.generar_plan_semanal_respuesta(client, perfil, memoria, None)
        memoria["plan_semanal_actual"] = plan
        memoria["ultimo_plan"] = plan
        main.añadir_lista_al_historial(memoria, plan)
        main.guardar_memoria(memoria)
        if phone in sesiones:
            sesiones[phone]["estado"] = "esperando_lista"
            sesiones[phone]["memoria"] = memoria
        nombre_super = main.nombre_supermercado_perfil(perfil)
        enviar_mensaje_twilio(phone, plan[:3000])
        enviar_mensaje_twilio(
            phone,
            f"¿Quieres la lista de la compra de {nombre_super} o prefieres comparar precios?\n"
            f"1️⃣ Lista de {nombre_super}\n"
            f"2️⃣ Comparar precios con otros supermercados"
        )
    except Exception as e:
        enviar_mensaje_twilio(phone, f"Error generando el plan: {e}\nEscribe 'reset' para empezar de nuevo.")

def generar_lista_async(phone, perfil, memoria):
    """Genera la lista de compra en segundo plano."""
    try:
        client = main.crear_cliente()
        enviar_mensaje_twilio(phone, "⏳ Preparando tu lista de la compra...")
        plan_ref = (memoria.get("plan_semanal_actual") or memoria.get("ultimo_plan") or "").strip()
        lista = main.generar_lista_compra_respuesta(client, perfil, plan_ref)
        memoria["lista_compra_actual"] = lista
        memoria["ultimo_plan"] = (plan_ref + "\n\n" + lista).strip()
        main.guardar_memoria(memoria)
        if phone in sesiones:
            sesiones[phone]["estado"] = "esperando_confirmar"
            sesiones[phone]["memoria"] = memoria
        nombre_super = main.nombre_supermercado_perfil(perfil)
        enviar_mensaje_twilio(phone, lista[:3000])
        enviar_mensaje_twilio(
            phone,
            f"¿Confirmas la compra en {nombre_super} o quieres comparar precios?\n"
            f"1️⃣ Confirmar en {nombre_super}\n"
            f"2️⃣ Comparar precios con otros supermercados"
        )
    except Exception as e:
        enviar_mensaje_twilio(phone, f"Error generando la lista: {e}")

def generar_comparativa_async(phone, memoria):
    """Genera comparativa de precios en segundo plano."""
    try:
        client = main.crear_cliente()
        enviar_mensaje_twilio(phone, "⏳ Calculando precios en todos los supermercados...")
        totales = main.generar_totales_comparativa(client, memoria)
        if phone in sesiones:
            sesiones[phone]["estado"] = "elegir_super"
        enviar_mensaje_twilio(phone, totales)
        enviar_mensaje_twilio(phone, "¿En qué supermercado quieres hacer la compra?\nEscribe el nombre (Mercadona, Lidl, Aldi…)")
    except Exception as e:
        enviar_mensaje_twilio(phone, f"Error comparando precios: {e}")

@app.route("/webhook", methods=["POST"])
def webhook():
    phone = request.form.get("From")
    message = request.form.get("Body", "").strip()
    media_url = request.form.get("MediaUrl0")
    media_type = request.form.get("MediaContentType0", "image/jpeg")
    tl = message.lower().strip()

    if phone not in sesiones:
        sesiones[phone] = sesion_nueva()

    sesion = sesiones[phone]
    memoria = sesion["memoria"]
    perfil = memoria.get("perfil", {})
    estado = sesion["estado"]

    # RESET
    if tl in ("reset", "reiniciar", "nuevo perfil", "empezar de nuevo"):
        main.reset_memoria_tras_nuevo(memoria)
        sesiones[phone] = sesion_nueva()
        return enviar(
            "Perfil borrado ✅\n\n"
            "¡Hola! Soy ZIA, tu nutricionista personal 🥗\n\n"
            "¿El plan es para ti solo o para toda tu familia?\n"
            "1️⃣ Para mí solo\n"
            "2️⃣ Para mi familia"
        )

    # INICIO
    if estado == "inicio" or not main.perfil_tiene_datos(perfil):
        if tl in ("1", "para mi", "para mí", "solo", "individual", "yo"):
            sesion["tipo_plan"] = "individual"
            sesion["estado"] = "onboarding"
            sesion["onboarding_step"] = 0
            sesion["perfil_tmp"] = {"tipo_plan": "individual"}
            _, pregunta = PREGUNTAS_INDIVIDUAL[0]
            return enviar(f"Perfecto, vamos con tu plan personalizado 💪\n\n{pregunta}")

        if tl in ("2", "familia", "familiar", "todos", "para mi familia"):
            sesion["tipo_plan"] = "familiar"
            sesion["estado"] = "onboarding"
            sesion["onboarding_step"] = 0
            sesion["perfil_tmp"] = {"tipo_plan": "familiar"}
            _, pregunta = PREGUNTAS_FAMILIAR[0]
            return enviar(f"Perfecto, vamos con el plan familiar 👨‍👩‍👧\n\n{pregunta}")

        return enviar(
            "¡Hola! Soy ZIA, tu nutricionista personal 🥗\n\n"
            "¿El plan es para ti solo o para toda tu familia?\n"
            "1️⃣ Para mí solo\n"
            "2️⃣ Para mi familia"
        )

    # ONBOARDING
    if estado == "onboarding":
        tipo = sesion.get("tipo_plan", "individual")
        preguntas = PREGUNTAS_INDIVIDUAL if tipo == "individual" else PREGUNTAS_FAMILIAR
        step = sesion["onboarding_step"]
        campo_actual = preguntas[step][0]

        # Validar objetivo doble
        if campo_actual == "objetivo":
            rl = message.lower()
            if any(x in rl for x in (" y ", ",", " también")):
                return enviar(
                    "Entiendo, pero necesito que elijas tu objetivo PRINCIPAL:\n\n"
                    "1️⃣ Perder grasa\n2️⃣ Ganar músculo\n3️⃣ Mantenimiento\n"
                    "4️⃣ Comer más sano\n5️⃣ Más energía"
                )

        valor = procesar_campo(campo_actual, message)
        sesion["perfil_tmp"][campo_actual] = valor
        sesion["onboarding_step"] += 1

        # Siguiente pregunta
        if sesion["onboarding_step"] < len(preguntas):
            _, siguiente = preguntas[sesion["onboarding_step"]]
            return enviar(siguiente)

        # Onboarding completo
        perfil = sesion["perfil_tmp"].copy()
        if "num_personas" not in perfil:
            perfil["num_personas"] = "1"
        memoria["perfil"] = perfil
        main.guardar_memoria(memoria)
        sesion["estado"] = "generando_plan"
        sesion["memoria"] = memoria

        # Generar plan en segundo plano
        t = threading.Thread(target=generar_plan_async, args=(phone, perfil, memoria))
        t.daemon = True
        t.start()

        return enviar("✅ Perfil guardado. Generando tu plan semanal...")

    # ESPERANDO LISTA
    if estado == "esperando_lista":
        nombre_super = main.nombre_supermercado_perfil(perfil)
        if tl in ("1", "si", "sí", "s", "yes", "ok", "vale", "claro", "lista"):
            sesion["estado"] = "generando_lista"
            t = threading.Thread(target=generar_lista_async, args=(phone, perfil, memoria))
            t.daemon = True
            t.start()
            return enviar("⏳ Preparando tu lista...")

        if tl in ("2", "comparar", "comparar precios"):
            sesion["estado"] = "generando_comparativa"
            t = threading.Thread(target=generar_comparativa_async, args=(phone, memoria))
            t.daemon = True
            t.start()
            return enviar("⏳ Calculando precios...")

        return enviar(f"Escribe 1 para la lista de {nombre_super} o 2 para comparar precios.")

    # ESPERANDO CONFIRMAR LISTA
    if estado == "esperando_confirmar":
        nombre_super = main.nombre_supermercado_perfil(perfil)
        if tl in ("1", "si", "sí", "confirmar", "ok", "vale"):
            sesion["estado"] = "chat"
            url = main.SUPER_TIENDA_URL.get(
                main.ids_supermercados_detectados(perfil.get("supermercado",""))[0]
                if main.ids_supermercados_detectados(perfil.get("supermercado",""))
                else "mercadona"
            , ("Mercadona", "https://tienda.mercadona.es"))[1]
            return enviar(
                f"✅ ¡Perfecto! Tu lista está lista.\n\n"
                f"🛒 Ir a {nombre_super} → {url}\n\n"
                f"¿Necesitas algo más?"
            )
        if tl in ("2", "comparar", "comparar precios"):
            sesion["estado"] = "generando_comparativa"
            t = threading.Thread(target=generar_comparativa_async, args=(phone, memoria))
            t.daemon = True
            t.start()
            return enviar("⏳ Calculando precios en todos los supermercados...")

        return enviar(f"Escribe 1 para confirmar en {nombre_super} o 2 para comparar precios.")

    # ELEGIR SUPER TRAS COMPARATIVA
    if estado == "elegir_super":
        cid = main.detectar_id_supermercado_en_texto(message)
        if cid:
            nombre_c, url_c = main.SUPER_TIENDA_URL[cid]
            sesion["estado"] = "chat"
            return enviar(
                f"✅ Perfecto, tu compra en {nombre_c}.\n\n"
                f"🛒 Ir a {nombre_c} → {url_c}\n\n"
                f"¿Necesitas algo más?"
            )
        return enviar("Dime el nombre del supermercado (Mercadona, Lidl, Aldi, Carrefour…)")

    # CHAT LIBRE con imagen
    if media_url:
        try:
            sid = os.getenv("TWILIO_ACCOUNT_SID")
            tok = os.getenv("TWILIO_AUTH_TOKEN")
            r = requests.get(media_url, auth=(sid, tok))
            img_b64 = base64.b64encode(r.content).decode("utf-8")
            client = main.crear_cliente()
            messages = [
                {"role": "system", "content": main.system_para_vision()},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{img_b64}"}},
                    {"type": "text", "text": message or "Analiza esta imagen y sugiere recetas con lo que ves"}
                ]}
            ]
            respuesta = main.completar(client, messages, max_tokens=2048)
            sesion["historial"].append({"role": "assistant", "content": respuesta})
            return enviar(respuesta[:1500])
        except Exception as e:
            return enviar(f"Error analizando imagen: {e}")

    # CHAT LIBRE
    client = main.crear_cliente()
    historial = sesion.get("historial", [])
    historial.append({"role": "user", "content": message})
    tail = historial[-20:]
    messages = [
        {"role": "system", "content": main.system_chat_con_memoria(perfil, memoria) + "\n\nIMPORTANTE: Estás en WhatsApp. Máximo 300 palabras. Sin markdown con asteriscos."},
        *tail,
    ]
    try:
        respuesta = main.completar(client, messages, max_tokens=1024)
        historial.append({"role": "assistant", "content": respuesta})
        sesion["historial"] = historial[-20:]
        main.guardar_memoria(memoria)
        return enviar(respuesta[:1500])
    except Exception as e:
        return enviar(f"Error: {e}")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    app.run(host="0.0.0.0", debug=False, port=port)
