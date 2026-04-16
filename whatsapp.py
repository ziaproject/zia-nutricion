from dotenv import load_dotenv
load_dotenv()
import os
import main
import requests
import base64
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

app = Flask(__name__)
sesiones = {}

def descargar_imagen_base64(url):
    try:
        twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
        twilio_token = os.getenv("TWILIO_AUTH_TOKEN")
        r = requests.get(url, auth=(twilio_sid, twilio_token))
        return base64.b64encode(r.content).decode("utf-8")
    except:
        return None

def sesion_nueva(phone):
    memoria = main.cargar_memoria()
    return {
        "memoria": memoria,
        "historial": [],
        "estado": "inicio",
        "perfil_tmp": {},
        "tipo_plan": None,
        "onboarding_step": 0,
        "esperando_lista": False,
        "carrito_fase": None,
    }

def enviar(texto):
    resp = MessagingResponse()
    resp.message(texto)
    return str(resp), 200, {"Content-Type": "text/xml"}

PREGUNTAS_INDIVIDUAL = [
    ("nombre", "¿Cómo te llamamos?"),
    ("datos_fisicos", "Para personalizar tu plan necesito algunos datos. Dímelos en un mensaje:\nGénero, edad, peso (kg) y altura (cm)\nEjemplo: hombre, 35 años, 80 kg, 178 cm"),
    ("objetivo", "¿Cuál es tu objetivo principal? Elige uno:\n1️⃣ Perder grasa\n2️⃣ Ganar músculo\n3️⃣ Mantenimiento\n4️⃣ Comer más sano\n5️⃣ Más energía"),
    ("presupuesto", "¿Cuánto quieres gastar a la semana en comida? (en euros, ej.: 80)"),
    ("supermercado", "¿En qué supermercado sueles comprar? (Mercadona, Lidl, Carrefour…)"),
    ("restricciones", "¿Tienes alguna alergia o intolerancia alimentaria?\nSi no hay ninguna, escribe «ninguna»."),
    ("tiempo_cocina", "¿Cuánto tiempo tienes para cocinar al día?\n1️⃣ Menos de 20 minutos\n2️⃣ Entre 20 y 40 minutos\n3️⃣ Tengo tiempo, me gusta cocinar"),
]

PREGUNTAS_FAMILIAR = [
    ("nombre", "¿Cómo te llamamos?"),
    ("num_personas", "¿Cuántas personas coméis habitualmente en casa?"),
    ("ninos_edades", "¿Hay niños en casa? Si es que sí, indica sus edades.\nSi no hay, escribe «no»."),
    ("gustos_familia", "Cuéntame los gustos o comidas favoritas de la familia y si hay algo que no le guste a alguien."),
    ("restricciones", "¿Hay alergias o intolerancias en casa?\nSi no hay ninguna, escribe «ninguna»."),
    ("presupuesto", "¿Cuánto queréis gastar a la semana en la compra? (en euros, ej.: 150)"),
    ("supermercado", "¿En qué supermercado soléis comprar? (Mercadona, Lidl, Carrefour…)"),
    ("tiempo_cocina", "¿Cuánto tiempo tenéis para cocinar al día?\n1️⃣ Menos de 20 minutos\n2️⃣ Entre 20 y 40 minutos\n3️⃣ Tenemos tiempo, nos gusta cocinar"),
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

def procesar_respuesta_onboarding(campo, valor):
    if campo == "objetivo":
        return MAPA_OBJETIVO.get(valor, valor)
    if campo == "tiempo_cocina":
        return MAPA_TIEMPO.get(valor, valor)
    return valor

@app.route("/webhook", methods=["POST"])
def webhook():
    phone = request.form.get("From")
    message = request.form.get("Body", "").strip()
    media_url = request.form.get("MediaUrl0")
    media_type = request.form.get("MediaContentType0", "image/jpeg")
    tl = message.lower().strip()

    if phone not in sesiones:
        sesiones[phone] = sesion_nueva(phone)

    sesion = sesiones[phone]
    memoria = sesion["memoria"]
    historial = sesion["historial"]
    perfil = memoria.get("perfil", {})
    estado = sesion["estado"]
    client = main.crear_cliente()

    # RESET
    if tl in ("reset", "reiniciar", "nuevo perfil", "empezar de nuevo"):
        main.reset_memoria_tras_nuevo(memoria)
        sesiones[phone] = sesion_nueva(phone)
        return enviar("Perfil borrado. Vamos a empezar desde cero.\n\n¿El plan es para ti solo o para toda tu familia?\n1️⃣ Para mí solo\n2️⃣ Para mi familia")

    # INICIO — sin perfil
    if estado == "inicio" or not main.perfil_tiene_datos(perfil):
        if tl in ("1", "para mi", "para mí", "solo", "individual", "yo"):
            sesion["tipo_plan"] = "individual"
            sesion["estado"] = "onboarding"
            sesion["onboarding_step"] = 0
            sesion["perfil_tmp"] = {"tipo_plan": "individual"}
            campo, pregunta = PREGUNTAS_INDIVIDUAL[0]
            return enviar(f"Perfecto, vamos con tu plan personalizado.\n\n{pregunta}")

        if tl in ("2", "familia", "familiar", "para mi familia", "todos"):
            sesion["tipo_plan"] = "familiar"
            sesion["estado"] = "onboarding"
            sesion["onboarding_step"] = 0
            sesion["perfil_tmp"] = {"tipo_plan": "familiar"}
            campo, pregunta = PREGUNTAS_FAMILIAR[0]
            return enviar(f"Perfecto, vamos con el plan familiar.\n\n{pregunta}")

        return enviar("¡Hola! Soy ZIA, tu nutricionista personal 🥗\n\n¿El plan es para ti solo o para toda tu familia?\n1️⃣ Para mí solo\n2️⃣ Para mi familia")

    # ONBOARDING
    if estado == "onboarding":
        tipo = sesion.get("tipo_plan", "individual")
        preguntas = PREGUNTAS_INDIVIDUAL if tipo == "individual" else PREGUNTAS_FAMILIAR
        step = sesion["onboarding_step"]
        campo_actual = preguntas[step][0]

        # Validar objetivo con dos cosas
        if campo_actual == "objetivo":
            rl = message.lower()
            if any(x in rl for x in (" y ", ",", " también")):
                return enviar("Entiendo que quieres las dos cosas, pero necesito que elijas tu objetivo PRINCIPAL:\n\n1️⃣ Perder grasa\n2️⃣ Ganar músculo\n3️⃣ Mantenimiento\n4️⃣ Comer más sano\n5️⃣ Más energía")

        valor = procesar_respuesta_onboarding(campo_actual, message)
        sesion["perfil_tmp"][campo_actual] = valor
        sesion["onboarding_step"] += 1

        # Siguiente pregunta
        if sesion["onboarding_step"] < len(preguntas):
            _, siguiente_pregunta = preguntas[sesion["onboarding_step"]]
            return enviar(siguiente_pregunta)

        # Onboarding completo — guardar perfil
        perfil = sesion["perfil_tmp"].copy()
        if "num_personas" not in perfil:
            perfil["num_personas"] = "1"
        memoria["perfil"] = perfil
        main.guardar_memoria(memoria)
        sesion["estado"] = "generando_plan"

        # Generar plan
        nombre = perfil.get("nombre", "")
        nombre_super = main.nombre_supermercado_perfil(perfil)
        try:
            plan, _ = main.generar_plan_semanal_respuesta(client, perfil, memoria, None)
            memoria["plan_semanal_actual"] = plan
            memoria["ultimo_plan"] = plan
            main.añadir_lista_al_historial(memoria, plan)
            main.guardar_memoria(memoria)
            sesion["estado"] = "esperando_lista"
            # Dividir plan en partes si es muy largo
            partes = [plan[i:i+1500] for i in range(0, len(plan), 1500)]
            resp = MessagingResponse()
            for parte in partes:
                resp.message(parte)
            resp.message(f"¿Quieres la lista de la compra de {nombre_super} o prefieres comparar precios?\n1️⃣ Lista de {nombre_super}\n2️⃣ Comparar precios")
            return str(resp), 200, {"Content-Type": "text/xml"}
        except Exception as e:
            sesion["estado"] = "chat"
            return enviar(f"Hubo un error generando el plan: {e}\nEscribe 'reset' para empezar de nuevo.")

    # ESPERANDO RESPUESTA LISTA
    if estado == "esperando_lista":
        nombre_super = main.nombre_supermercado_perfil(perfil)
        if tl in ("1", "si", "sí", "s", "yes", "ok", "vale", "claro", f"lista de {nombre_super.lower()}", "lista"):
            try:
                plan_ref = (memoria.get("plan_semanal_actual") or memoria.get("ultimo_plan") or "").strip()
                lista = main.generar_lista_compra_respuesta(client, perfil, plan_ref)
                memoria["lista_compra_actual"] = lista
                memoria["ultimo_plan"] = (plan_ref + "\n\n" + lista).strip()
                main.guardar_memoria(memoria)
                sesion["estado"] = "chat"
                partes = [lista[i:i+1500] for i in range(0, len(lista), 1500)]
                resp = MessagingResponse()
                for parte in partes:
                    resp.message(parte)
                resp.message(f"✅ Lista lista. ¿Quieres comparar precios con otros supermercados o confirmas en {nombre_super}?\n1️⃣ Confirmar en {nombre_super}\n2️⃣ Comparar precios")
                return str(resp), 200, {"Content-Type": "text/xml"}
            except Exception as e:
                sesion["estado"] = "chat"
                return enviar(f"Error generando la lista: {e}")

        if tl in ("2", "comparar", "comparar precios"):
            try:
                totales = main.generar_totales_comparativa(client, memoria)
                sesion["estado"] = "chat"
                return enviar(f"{totales}\n\n¿En qué supermercado quieres hacer la compra?")
            except Exception as e:
                return enviar(f"Error comparando precios: {e}")

        if tl in ("no", "n"):
            sesion["estado"] = "chat"
            return enviar("De acuerdo. ¿En qué puedo ayudarte?")

        return enviar(f"Escribe 1 para la lista de {nombre_super} o 2 para comparar precios.")

    # CHAT LIBRE con imagen
    if media_url:
        img_b64 = descargar_imagen_base64(media_url)
        if img_b64:
            messages = [
                {"role": "system", "content": main.system_para_vision()},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{img_b64}"}},
                    {"type": "text", "text": message or "Analiza esta imagen y sugiere recetas con lo que ves"}
                ]}
            ]
            try:
                respuesta = main.completar(client, messages, max_tokens=2048)
                historial.append({"role": "assistant", "content": respuesta})
                main.guardar_memoria(memoria)
                return enviar(respuesta[:1500])
            except Exception as e:
                return enviar(f"Error analizando imagen: {e}")

    # CHAT LIBRE
    historial.append({"role": "user", "content": message})
    tail = historial[-20:]
    messages = [
        {"role": "system", "content": main.system_chat_con_memoria(perfil, memoria) + "\n\nIMPORTANTE: Estás en WhatsApp. Máximo 300 palabras. Sin markdown con asteriscos."},
        *tail,
    ]
    try:
        respuesta = main.completar(client, messages, max_tokens=1024)
        historial.append({"role": "assistant", "content": respuesta})
        if len(historial) > 20:
            sesion["historial"] = historial[-20:]
        main.guardar_memoria(memoria)
        return enviar(respuesta[:1500])
    except Exception as e:
        return enviar(f"Error: {e}")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    app.run(host="0.0.0.0", debug=False, port=port)
# v2 jueves, 16 de abril de 2026, 19:20:19 CEST
