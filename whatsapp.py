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

def send(to, texto):
    try:
        cl = TwilioClient(TWILIO_SID, TWILIO_TOKEN)
        partes = [texto[i:i+1500] for i in range(0, len(texto), 1500)]
        for parte in partes:
            cl.messages.create(body=parte, from_=TWILIO_FROM, to=to)
    except Exception as e:
        print(f"Error enviando: {e}")

def enviar(texto):
    resp = MessagingResponse()
    partes = [texto[i:i+1500] for i in range(0, len(texto), 1500)]
    for parte in partes:
        resp.message(parte)
    return str(resp), 200, {"Content-Type": "text/xml"}

import json
from pathlib import Path

DATA_DIR = Path("/tmp/zia_estados")
DATA_DIR.mkdir(parents=True, exist_ok=True)

def estado_path(phone):
    safe = phone.replace("+","").replace(":","_").replace(" ","_").replace("whatsapp","")
    return DATA_DIR / f"sesion_{safe}.json"

def cargar_estado(phone):
    p = estado_path(phone)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except:
            pass
    return {}

def guardar_estado(phone, datos):
    try:
        estado_path(phone).write_text(
            json.dumps(datos, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception as e:
        print(f"Error guardando estado: {e}")

def sesion_nueva(phone):
    memoria = main.cargar_memoria()
    estado_guardado = cargar_estado(phone)
    return {
        "memoria": memoria,
        "historial": [],
        "estado": estado_guardado.get("estado", "inicio"),
        "perfil_tmp": estado_guardado.get("perfil_tmp", {}),
        "tipo_plan": estado_guardado.get("tipo_plan"),
        "onboarding_step": estado_guardado.get("onboarding_step", 0),
    }

def guardar_estado_sesion(phone, sesion):
    guardar_estado(phone, {
        "estado": sesion.get("estado", "inicio"),
        "perfil_tmp": sesion.get("perfil_tmp", {}),
        "tipo_plan": sesion.get("tipo_plan"),
        "onboarding_step": sesion.get("onboarding_step", 0),
    })

PREGUNTAS_INDIVIDUAL = [
    ("nombre", "¿Cómo te llamamos?"),
    ("datos_fisicos", "Para personalizar tu plan necesito algunos datos en un mensaje:\nGénero, edad, peso (kg) y altura (cm)\nEjemplo: hombre, 35 años, 80 kg, 178 cm"),
    ("objetivo", "¿Cuál es tu objetivo principal?\n1️⃣ Perder grasa\n2️⃣ Ganar músculo\n3️⃣ Mantenimiento\n4️⃣ Comer más sano\n5️⃣ Más energía"),
    ("presupuesto", "¿Cuánto quieres gastar a la semana en comida? (ej: 80€)"),
    ("supermercado", "¿En qué supermercado sueles comprar?\nMercadona, Lidl, Aldi, Carrefour, Consum…"),
    ("restricciones", "¿Tienes alguna alergia o intolerancia?\nSi no hay ninguna escribe: ninguna"),
    ("tiempo_cocina", "¿Cuánto tiempo tienes para cocinar al día?\n1️⃣ Menos de 20 minutos\n2️⃣ Entre 20 y 40 minutos\n3️⃣ Tengo tiempo, me gusta cocinar"),
]

PREGUNTAS_FAMILIAR = [
    ("nombre", "¿Cómo te llamamos?"),
    ("num_personas", "¿Cuántas personas coméis en casa?"),
    ("ninos_edades", "¿Hay niños en casa? Si sí indica edades.\nSi no escribe: no"),
    ("gustos_familia", "Cuéntame gustos o comidas favoritas y si alguien no come algo."),
    ("restricciones", "¿Hay alergias o intolerancias en casa?\nSi no hay escribe: ninguna"),
    ("presupuesto", "¿Cuánto queréis gastar a la semana? (ej: 150€)"),
    ("supermercado", "¿En qué supermercado soléis comprar?\nMercadona, Lidl, Aldi, Carrefour, Consum…"),
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

SUPERS_COMPARATIVA = ["mercadona", "lidl", "aldi", "carrefour", "consum"]

def procesar_campo(campo, valor):
    if campo == "objetivo":
        return MAPA_OBJETIVO.get(valor.strip(), valor)
    if campo == "tiempo_cocina":
        return MAPA_TIEMPO.get(valor.strip(), valor)
    return valor

def nombre_super(perfil):
    return main.nombre_supermercado_perfil(perfil)

def url_super(perfil):
    ids = main.ids_supermercados_detectados(perfil.get("supermercado", ""))
    cid = ids[0] if ids else "mercadona"
    return main.SUPER_TIENDA_URL[cid][1]

# ─── GENERACIÓN ASÍNCRONA ────────────────────────────────────────

def generar_plan_async(phone, perfil, memoria):
    try:
        client = main.crear_cliente()

        # System sin tiempos de preparación en el plan semanal
        system_sin_minutos = main.system_zia_completo().replace(
            "SIEMPRE indica el tiempo de preparación en minutos para cada receta (ej: 15 min).",
            "NO incluyas tiempos de preparación en el plan semanal. Los tiempos solo aparecen cuando el usuario pide una receta específica."
        )

        messages = [
            {"role": "system", "content": system_sin_minutos},
            {"role": "user", "content": main.mensaje_plan_semanal(perfil, memoria)},
        ]
        plan = main.completar(client, messages, max_tokens=8192)

        memoria["plan_semanal_actual"] = plan
        memoria["ultimo_plan"] = plan
        main.añadir_lista_al_historial(memoria, plan)
        main.guardar_memoria(memoria)

        if phone in sesiones:
            sesiones[phone]["estado"] = "esperando_cambios"
            sesiones[phone]["memoria"] = memoria

        # Enviar plan en partes
        partes = [plan[i:i+1400] for i in range(0, len(plan), 1400)]
        for parte in partes:
            send(phone, parte)

        send(phone, "¿Quieres cambiar o añadir algo al plan? (sí/no)")

    except Exception as e:
        send(phone, f"Error generando el plan: {e}\nEscribe reset para empezar de nuevo.")
        if phone in sesiones:
            sesiones[phone]["estado"] = "chat"

def generar_lista_async(phone, perfil, memoria):
    try:
        client = main.crear_cliente()
        plan_ref = (memoria.get("plan_semanal_actual") or memoria.get("ultimo_plan") or "").strip()
        lista = main.generar_lista_compra_respuesta(client, perfil, plan_ref)
        memoria["lista_compra_actual"] = lista
        memoria["ultimo_plan"] = (plan_ref + "\n\n" + lista).strip()
        main.guardar_memoria(memoria)

        if phone in sesiones:
            sesiones[phone]["estado"] = "esperando_pago_o_comparar"
            sesiones[phone]["memoria"] = memoria

        ns = nombre_super(perfil)
        partes = [lista[i:i+1400] for i in range(0, len(lista), 1400)]
        for parte in partes:
            send(phone, parte)

        send(phone,
            f"¿Qué quieres hacer?\n"
            f"1️⃣ Pagar en {ns}\n"
            f"2️⃣ Comparar precios con otros supermercados"
        )
    except Exception as e:
        send(phone, f"Error generando la lista: {e}")
        if phone in sesiones:
            sesiones[phone]["estado"] = "chat"

def generar_comparativa_async(phone, memoria):
    try:
        client = main.crear_cliente()

        # Generar totales para los 5 supers
        ref = (memoria.get("ultimo_plan") or "").strip()
        prompt = f"""Basándote en esta lista de la compra, calcula el TOTAL estimado para cada supermercado.

Lista:
---
{ref[:10000]}
---

{main.texto_factores_precio_supermercados()}

Muestra SOLO estas 5 líneas con el total de cada supermercado, sin más texto:
🏪 Mercadona → XX.XX€
🏪 Lidl → XX.XX€
🏪 Aldi → XX.XX€
🏪 Carrefour → XX.XX€
🏪 Consum → XX.XX€

Al final de la línea más económica añade: ⭐ MÁS ECONÓMICO
Sin texto adicional."""

        messages = [
            {"role": "system", "content": "Respondes solo las líneas pedidas. Sin texto extra."},
            {"role": "user", "content": prompt},
        ]
        totales = main.completar(client, messages, temperature=0.2, max_tokens=300)

        if phone in sesiones:
            sesiones[phone]["estado"] = "elegir_super_comparativa"

        send(phone, totales)
        # Excluir el super habitual del usuario
        ids_habitual = main.ids_supermercados_detectados(
            sesiones[phone]["memoria"].get("perfil", {}).get("supermercado", "")
        )
        cid_habitual = ids_habitual[0] if ids_habitual else "mercadona"
        todos = [("1","mercadona","Mercadona"),("2","lidl","Lidl"),("3","aldi","Aldi"),("4","carrefour","Carrefour"),("5","consum","Consum")]
        opciones = [f"{n}️⃣ {nombre}" for n, cid, nombre in todos if cid != cid_habitual]
        send(phone, "Con cual te quedas?\n" + "\n".join(opciones))

    except Exception as e:
        send(phone, f"Error comparando precios: {e}")
        if phone in sesiones:
            sesiones[phone]["estado"] = "chat"

# ─── WEBHOOK ─────────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    phone = request.form.get("From")
    message = request.form.get("Body", "").strip()
    media_url = request.form.get("MediaUrl0")
    media_type = request.form.get("MediaContentType0", "image/jpeg")
    tl = message.lower().strip()

    if phone not in sesiones:
        sesiones[phone] = sesion_nueva(phone)
    memoria = sesion["memoria"]
    perfil = memoria.get("perfil", {})
    estado = sesion.get("estado", "inicio")

    # ── RESET ──
    if tl in ("reset", "reiniciar", "nuevo perfil", "empezar de nuevo"):
        main.reset_memoria_tras_nuevo(memoria)
        sesiones[phone] = sesion_nueva(phone)
        return enviar(
            "*¡Hola! Soy ZIA, tu nutricionista personal* 🥗\n\n"
            "¿El plan es para ti solo o para toda tu familia?\n"
            "1️⃣ Para mí solo\n"
            "2️⃣ Para mi familia"
        )

    # ── INICIO ──
    if estado == "inicio" or not main.perfil_tiene_datos(perfil):
        if tl in ("1", "para mi", "para mí", "solo", "individual", "yo"):
            sesion["tipo_plan"] = "individual"
            sesion["estado"] = "onboarding"
            sesion["onboarding_step"] = 0
            sesion["perfil_tmp"] = {"tipo_plan": "individual"}
            guardar_estado_sesion(phone, sesion)
            _, pregunta = PREGUNTAS_INDIVIDUAL[0]
            return enviar(f"Perfecto 💪\n\n{pregunta}")

        if tl in ("2", "familia", "familiar", "todos", "para mi familia"):
            sesion["tipo_plan"] = "familiar"
            sesion["estado"] = "onboarding"
            sesion["onboarding_step"] = 0
            sesion["perfil_tmp"] = {"tipo_plan": "familiar"}
            guardar_estado_sesion(phone, sesion)
            _, pregunta = PREGUNTAS_FAMILIAR[0]
            return enviar(f"Perfecto 👨‍👩‍👧\n\n{pregunta}")

        return enviar(
            "*¡Hola! Soy ZIA, tu nutricionista personal* 🥗\n\n"
            "¿El plan es para ti solo o para toda tu familia?\n"
            "1️⃣ Para mí solo\n"
            "2️⃣ Para mi familia"
        )

    # ── ONBOARDING ──
    if estado == "onboarding":
        tipo = sesion.get("tipo_plan", "individual")
        preguntas = PREGUNTAS_INDIVIDUAL if tipo == "individual" else PREGUNTAS_FAMILIAR
        step = sesion["onboarding_step"]
        campo_actual = preguntas[step][0]

        if campo_actual == "objetivo":
            rl = message.lower()
            if any(x in rl for x in (" y ", ",", " también")):
                return enviar(
                    "Elige solo tu objetivo PRINCIPAL:\n\n"
                    "1️⃣ Perder grasa\n2️⃣ Ganar músculo\n3️⃣ Mantenimiento\n"
                    "4️⃣ Comer más sano\n5️⃣ Más energía"
                )

        valor = procesar_campo(campo_actual, message)
        sesion["perfil_tmp"][campo_actual] = valor
        sesion["onboarding_step"] += 1

        if sesion["onboarding_step"] < len(preguntas):
            _, siguiente = preguntas[sesion["onboarding_step"]]
            guardar_estado_sesion(phone, sesion)
            return enviar(siguiente)

        # Onboarding completo
        perfil = sesion["perfil_tmp"].copy()
        if "num_personas" not in perfil:
            perfil["num_personas"] = "1"
        memoria["perfil"] = perfil
        main.guardar_memoria(memoria)
        sesion["estado"] = "generando_plan"
        sesion["memoria"] = memoria

        t = threading.Thread(target=generar_plan_async, args=(phone, perfil, memoria))
        t.daemon = True
        t.start()

        return enviar("✅ Perfecto. Generando tu plan semanal personalizado... Dame un momento 🔄")

    # ── ESPERANDO CAMBIOS AL PLAN ──
    if estado == "esperando_cambios":
        if tl in ("no", "n", "nop", "nope", "no gracias", "así está bien", "me gusta"):
            sesion["estado"] = "esperando_si_lista"
            ns = nombre_super(perfil)
            return enviar(f"¿Quieres que prepare tu lista de la compra en {ns}? (sí/no)")

        if tl in ("si", "sí", "s", "yes", "ok", "vale", "claro"):
            sesion["estado"] = "escuchando_cambios"
            return enviar("Dime qué quieres cambiar o añadir al plan.")

        # Respuesta ambigua — tratar como cambio
        sesion["estado"] = "escuchando_cambios"
        return enviar("Dime qué quieres cambiar o añadir al plan.")

    # ── ESCUCHANDO CAMBIOS ──
    if estado == "escuchando_cambios":
        try:
            client = main.crear_cliente()
            plan_actual = memoria.get("plan_semanal_actual", "")
            messages = [
                {"role": "system", "content": main.system_zia_completo()},
                {"role": "user", "content": f"Plan actual:\n{plan_actual[:6000]}\n\nEl usuario quiere cambiar: {message}\n\nActualiza el plan con este cambio. Sin tiempos de preparación en el plan."},
            ]
            plan_nuevo = main.completar(client, messages, max_tokens=6000)
            memoria["plan_semanal_actual"] = plan_nuevo
            memoria["ultimo_plan"] = plan_nuevo
            main.guardar_memoria(memoria)
            sesion["memoria"] = memoria
            sesion["estado"] = "esperando_cambios"

            partes = [plan_nuevo[i:i+1400] for i in range(0, len(plan_nuevo), 1400)]
            resp = MessagingResponse()
            for parte in partes:
                resp.message(parte)
            resp.message("¿Quieres cambiar algo más? (sí/no)")
            return str(resp), 200, {"Content-Type": "text/xml"}
        except Exception as e:
            sesion["estado"] = "esperando_cambios"
            return enviar(f"Error: {e}\n¿Quieres cambiar algo más? (sí/no)")

    # ── ESPERANDO SI QUIERE LISTA ──
    if estado == "esperando_si_lista":
        ns = nombre_super(perfil)
        if tl in ("si", "sí", "s", "yes", "ok", "vale", "claro"):
            sesion["estado"] = "generando_lista"
            t = threading.Thread(target=generar_lista_async, args=(phone, perfil, memoria))
            t.daemon = True
            t.start()
            return enviar("⏳ Preparando tu lista de la compra...")

        if tl in ("no", "n", "nop", "ahora no"):
            sesion["estado"] = "chat"
            return enviar("De acuerdo. Cuando quieras la lista dímelo 😊")

        return enviar(f"¿Quieres que prepare tu lista de la compra en {ns}? Escribe sí o no.")

    # ── ESPERANDO PAGO O COMPARAR ──
    if estado == "esperando_pago_o_comparar":
        ns = nombre_super(perfil)
        us = url_super(perfil)

        if tl in ("1", "pagar", "pagar aqui", "confirmar", "si", "sí", "ok", "vale", ns.lower()):
            sesion["estado"] = "chat"
            return enviar(
                f"✅ ¡Perfecto! Aquí tienes el enlace para comprar:\n\n"
                f"🛒 {ns} → {us}\n\n"
                f"¿Necesitas algo más?"
            )

        if tl in ("2", "comparar", "comparar precios", "otros", "otros supermercados"):
            sesion["estado"] = "generando_comparativa"
            t = threading.Thread(target=generar_comparativa_async, args=(phone, memoria))
            t.daemon = True
            t.start()
            return enviar("⏳ Calculando precios en 5 supermercados...")

        return enviar(
            f"Escribe:\n"
            f"1️⃣ para pagar en {ns}\n"
            f"2️⃣ para comparar precios"
        )

    # ── ELEGIR SUPER TRAS COMPARATIVA ──
    if estado == "elegir_super_comparativa":
        mapa_num = {
            "1": "mercadona", "2": "lidl", "3": "aldi",
            "4": "carrefour", "5": "consum",
        }
        cid = mapa_num.get(tl) or main.detectar_id_supermercado_en_texto(message)
        if cid and cid in main.SUPER_TIENDA_URL:
            nombre_c, url_c = main.SUPER_TIENDA_URL[cid]
            sesion["estado"] = "chat"
            return enviar(
                f"✅ Perfecto, tu compra en {nombre_c}.\n\n"
                f"🛒 {nombre_c} → {url_c}\n\n"
                f"¿Necesitas algo más?"
            )
        return enviar(
            "Elige el supermercado:\n"
            "1️⃣ Mercadona\n2️⃣ Lidl\n3️⃣ Aldi\n4️⃣ Carrefour\n5️⃣ Consum"
        )

    # ── CHAT CON IMAGEN ──
    if media_url:
        try:
            r = requests.get(media_url, auth=(TWILIO_SID, TWILIO_TOKEN))
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
            return enviar(respuesta[:1500])
        except Exception as e:
            return enviar(f"Error analizando imagen: {e}")

    # ── CHAT LIBRE ──
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
