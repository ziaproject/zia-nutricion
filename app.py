from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import os, json
from pathlib import Path

app = Flask(__name__)
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

def estado_path(numero):
    safe = numero.replace("+","").replace(":","_")
    return DATA_DIR / f"estado_{safe}.json"

def memoria_path(numero):
    safe = numero.replace("+","").replace(":","_")
    return DATA_DIR / f"memoria_{safe}.json"

def cargar_json(path, default):
    try:
        return json.loads(path.read_text()) if path.is_file() else default
    except:
        return default

def guardar_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))

def enviar_en_partes(resp, texto, max_chars=1500):
    partes = [texto[i:i+max_chars] for i in range(0, len(texto), max_chars)]
    for parte in partes:
        resp.message(parte)

@app.route("/", methods=["GET"])
@app.route("/webhook", methods=["GET"])
def health():
    return "ZIA OK", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    from main import (crear_cliente, guardar_memoria,
        perfil_tiene_datos, ONBOARDING_QUESTIONS, generar_plan_semanal_respuesta,
        añadir_lista_al_historial, system_chat_con_memoria, completar,
        memoria_por_defecto)

    numero = request.form.get("From", "")
    texto = request.form.get("Body", "").strip()

    mem_path = memoria_path(numero)
    est_path = estado_path(numero)

    memoria = cargar_json(mem_path, memoria_por_defecto())
    estado = cargar_json(est_path, {"paso": 0, "perfil_tmp": {}})

    client = crear_cliente()
    perfil = {k: str(v) for k, v in memoria.get("perfil", {}).items()}

    resp = MessagingResponse()

    try:
        paso = estado.get("paso", 0)

        if paso == 0 and not perfil_tiene_datos(memoria.get("perfil", {})):
            estado["paso"] = -1
            guardar_json(est_path, estado)
            resp.message("Hola! Soy ZIA, tu nutricionista familiar. Te hare 8 preguntas rapidas. Escribe si para empezar.")
            return str(resp)

        if paso == -1:
            if texto.lower() in ("si", "si", "dale", "ok", "yes", "vamos", "claro"):
                estado["paso"] = 1
                estado["perfil_tmp"] = {}
                guardar_json(est_path, estado)
                _, pregunta = ONBOARDING_QUESTIONS[0]
                resp.message(f"ZIA: {pregunta}")
                return str(resp)
            resp.message("Escribe si cuando quieras empezar")
            return str(resp)

        if 1 <= paso <= len(ONBOARDING_QUESTIONS):
            campo, _ = ONBOARDING_QUESTIONS[paso - 1]
            estado["perfil_tmp"][campo] = texto
            if paso < len(ONBOARDING_QUESTIONS):
                estado["paso"] = paso + 1
                _, siguiente = ONBOARDING_QUESTIONS[paso]
                guardar_json(est_path, estado)
                resp.message(f"ZIA: {siguiente}")
                return str(resp)
            else:
                perfil_nuevo = {k: str(v) for k, v in estado["perfil_tmp"].items()}
                memoria["perfil"] = perfil_nuevo
                estado["paso"] = 100
                estado["perfil_tmp"] = {}
                guardar_json(est_path, estado)
                plan, _ = generar_plan_semanal_respuesta(client, perfil_nuevo, memoria, None)
                memoria["plan_semanal_actual"] = plan
                memoria["ultimo_plan"] = plan
                añadir_lista_al_historial(memoria, plan)
                guardar_json(mem_path, memoria)
                nombre = perfil_nuevo.get("nombre", "")
                enviar_en_partes(resp, f"Perfecto {nombre}! Aqui tienes tu plan semanal:\n\n{plan}")
                resp.message("Quieres la lista de la compra? (si/no)")
                return str(resp)

        # Chat normal
        messages = [
            {"role": "system", "content": system_chat_con_memoria(perfil, memoria)},
            {"role": "user", "content": texto}
        ]
        respuesta = completar(client, messages, max_tokens=2048)
        guardar_json(mem_path, memoria)
        enviar_en_partes(resp, respuesta)

    except Exception as e:
        resp.message(f"ZIA: Error: {str(e)[:200]}")

    return str(resp)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
