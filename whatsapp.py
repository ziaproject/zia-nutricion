import main
import requests
import base64
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

app = Flask(__name__)
sesiones = {}

def descargar_imagen_base64(url):
    try:
        sid = main.API_KEY
        r = requests.get(url, auth=(
            os.getenv("TWILIO_ACCOUNT_SID"),
            open("/Users/enriquecollado/zia_project/.twilio_token").read().strip()
        ))
        return base64.b64encode(r.content).decode("utf-8")
    except:
        return None

@app.route("/webhook", methods=["POST"])
def webhook():
    phone = request.form.get("From")
    message = request.form.get("Body", "").strip()
    media_url = request.form.get("MediaUrl0")
    media_type = request.form.get("MediaContentType0", "image/jpeg")

    if phone not in sesiones:
        memoria = main.cargar_memoria()
        sesiones[phone] = {"memoria": memoria, "historial": []}

    sesion = sesiones[phone]
    memoria = sesion["memoria"]
    historial = sesion["historial"]
    perfil = memoria.get("perfil", {})

    if media_url:
        img_b64 = descargar_imagen_base64(media_url)
        if img_b64:
            historial.append({"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{img_b64}"}},
                {"type": "text", "text": message or "Analiza esta imagen y sugiere recetas con lo que ves"}
            ]})
        else:
            historial.append({"role": "user", "content": message or "imagen no disponible"})
    else:
        historial.append({"role": "user", "content": message})

    messages = main.mensaje_chat_libre(perfil, message or "foto")
    system = messages[0].copy()
    system["content"] += "\n\nIMPORTANTE: Estás en WhatsApp. Máximo 300 palabras."
    messages = [system] + historial

    respuesta = main.completar(main.OpenAI(api_key=main.API_KEY), messages)
    historial.append({"role": "assistant", "content": respuesta})
    if len(historial) > 20:
        sesion["historial"] = historial[-20:]

    main.guardar_memoria(memoria)
    resp = MessagingResponse()
    resp.message(respuesta)
    return str(resp), 200, {"Content-Type": "text/xml"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=False, port=5001)
