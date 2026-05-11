import os
import json
import stripe
from flask import Flask, request, jsonify
from flask_cors import CORS
from core.engine import ZiaEngine
from dotenv import load_dotenv
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

load_dotenv()

app = Flask(__name__)
CORS(app, origins=["https://zianutricion.com", "https://www.zianutricion.com", "http://localhost:3000", "http://localhost:5001"])

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

from supabase import create_client
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

SYSTEM_PROMPT_ELITE = """Eres ZIA, nutricionista de élite con 20 años de experiencia clínica. Tu especialidad es crear planes nutricionales personalizados, precisos y científicamente fundamentados.

REGLAS ABSOLUTAS:
- Respondes SIEMPRE en español
- Nunca usas markdown (sin **, sin ##, sin guiones de lista)
- Eres cálida, moti y directa
- Cuando generas una dieta, incluyes SIEMPRE: nombre del plato, cantidad en gramos, calorías, proteínas, carbohidratos y grasas
- Los macros son calculados con precisión basándote en el perfil del usuario
- Adaptas TODO al supermercado del usuario y su presupuesto

FORMATO DIETA SEMANAL:
Cuando presentes la dieta semanal, usa este formato exacto para cada día:

LUNES
Desayuno: [nombre] ([Xg])
Calorías: X | Proteínas: Xg | Carbos: Xg | Grasas: Xg

Almuerzo: [nombre] ([Xg])
Calorías: X | Proteínas: Xg | Carbos: Xg | Grasas: Xg

Cena: [nombre] ([Xg])
Calorías: X | Proteínas: Xg | Carbos: Xg | Grasas: Xg

TOTAL DÍA: X kcal | Proteínas: Xg | Carbos: Xg | Grasas: Xg

[Repite para cada día de la semana]

FLUJO POST-DIETA:
Después de presentar la dieta completa, pregunta:
"¿Quieres cambiar algún plato o ingrediente de tu plan?"

Si el usuario quiere cambios: modifica solo ese plato manteniendo los macros similares.
Si no quiere cambios: pregunta "¿Te paso la lista de la compra optimizaddo] con precios aproximados?"

LISTA DE LA COMPRA:
Agrupa por categorías (Carnes y pescados, Verduras, Frutas, Lácteos, Cereales y legumbres, Otros).
Incluye cantidad total necesaria para la semana y precio aproximado por producto.
Al final indica el total estimado y confirma que está dentro del presupuesto."""

@app.route("/web/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "zia-nutricion-web"})

@app.route("/web/registro", methods=["POST"])
def registro():
    try:
        data = request.json
        email = data.get("email")
        password = data.get("password")
        nombre = data.get("nombre", "")
        res = supabase.auth.sign_up({"email": email, "password": password})
        if not res.user:
            return jsonify({"ok": False, "error": "Error al crear la cuenta"}), 400
        user_id = res.user.id
        supabase.table("perfiles").upsert({
            "user_id": user_id,
            "nombre": nombre,
            "plan": "free"
        }).execut
        token = res.session.access_token if res.session else ""
        return jsonify({"ok": True, "token": token, "nombre": nombre})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@app.route("/web/login", methods=["POST"])
def login():
    try:
        data = request.json
        email = data.get("email")
        password = data.get("password")
        res = supabase.auth.sign_in_with_password({"email": email, "password": password})
        if not res.user:
            return jsonify({"ok": False, "error": "Credenciales incorrectas"}), 401
        token = res.session.access_token
        user_id = res.user.id
        try:
            perfil = supabase.table("perfiles").select("nombre, plan").eq("user_id", user_id).single().execute()
            nombre = perfil.data.get("nombre", "")
            plan = perfil.data.get("plan", "free")
        except:
            nombre = ""
            plan = "free"
        return jsonify({"ok": True, "token": token, "nombre": nombre, "plan": plan})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@app.route("/web/perfil", methods=["GET"])
def perfil():
    try:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if token and token != "anonimo":
            user = supabase.auth.get_user(token)
            user_id = user.user.id
        else:
            user_id = "anonimo"
        user_email = user.user.email
        try:
            plan_row = supabase.table("planes").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(1).single().execute()
            plan_data = plan_row.data.get("plan_data")
        except:
            plan_data = None
        try:
            perfil = supabase.table("perfiles").select("nombre, plan").eq("user_id", user_id).single().execute()
            nombre = perfil.data.get("nombre", "")
            plan_usuario = perfil.data.get("plan", "free")
        except:
            nombre = ""
            plan_usuario = "free"
        return jsonify({"ok": True, "plan": plan_data, "plan_usuario": plan_usuario, "nombre": nombre, "email": user_email})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 404

@app.route("/web/guardar-perfil-onboarding", methods=["POST"])
def guardar_perfil_onboarding():
    try:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        user = supabase.auth.get_user(token)
        user_id = user.user.id
        data = request.json
        supabase.table("perfiles").upsert({
            "user_id": user_id,
            "nombre": data.get("nombre", ""),
            "objetivo": data.get("objetivo", ""),
            "ejercicio": data.get("ejercicio", ""),
            "cocina": data.get("cocina", ""),
            "comidas_dia": data.get("comidas_dia", ""),
            "intolerancias": data.get("intolerancias", "ninguna"),
            "presupuesto": data.get("presupuesto", 60),
            "supermercado": data.get("supermercado", "Mercadona"),
            "genero": data.get("genero", ""),
            "edad": data.get("edad", ""),
            "peso": data.get("peso", ""),
            "altura": data.get("altura", ""),
            "plan": "free",
            "chat_count": 0
        }).execute()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@app.route("/web/generar-plan", methods=["POST"])
def generar_plan():
    try:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        user = supabase.auth.get_user(token)
        user_id = user.user.id
        perfil = supabase.table("perfiles").select("*").eq("user_id", user_id).single().execute()
        p = perfil.data or {}
        import openai
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        prompt = f"""Eres ZIA, nutricionista de élite. Crea un plan nutricional semanal completo (7 días) para:
- Nombre: {p.get('nombre', '')}
- Género: {p.get('genero', '')} | Edad: {p.get('edad', '')} años | Peso: {p.get('peso', '')}kg | Altura: {p.get('altura', '')}cm
- Objetivo: et('objetivo', 'comer sano')}
- Ejercicio: {p.get('ejercicio', 'moderado')}
- Relación con cocina: {p.get('cocina', 'normal')}
- Comidas al día: {p.get('comidas_dia', '3')}
- Intolerancias/restricciones: {p.get('intolerancias', 'ninguna')}
- Presupuesto semanal: {p.get('presupuesto', 60)}€
- Supermercado: {p.get('supermercado', 'Mercadona')}

Devuelve SOLO JSON con esta estructura exacta:
{{
  "dias": [
    {{
      "dia": "Lunes",
      "desayuno": {{"nombre": "", "calorias": 0, "proteinas": 0, "carbos": 0, "grasas": 0, "cantidad": ""}},
      "almuerzo": {{"nombre": "", "calorias": 0, "proteinas": 0, "carbos": 0, "grasas": 0, "cantidad": ""}},
      "cena": {{"nombre": "", "calorias": 0, "proteinas": 0, "carbos": 0, "grasas": 0, "cantidad": ""}}
    }}
  ],
  "lista_compra": null,
  "total_semana_kcal": 0,
  "proteinas_diarias_media": 0
}}"""
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            response_ft={"type": "json_object"},
            max_tokens=4000
        )
        plan_json = json.loads(response.choices[0].message.content)
        supabase.table("planes").upsert({"user_id": user_id, "plan_data": plan_json}).execute()
        return jsonify({"ok": True, "plan": plan_json})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/web/mi-plan", methods=["GET"])
def mi_plan():
    try:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        user = supabase.auth.get_user(token)
        user_id = user.user.id
        user_email = user.user.email
        try:
            plan = supabase.table("planes").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(1).single().execute()
            plan_data = plan.data.get("plan_data")
        except:
            plan_data = None
        try:
            perfil = supabase.table("perfiles").select("nombre, plan").eq("user_id", user_id).single().execute()
            nombre = perfil.data.get("nombre", "")
            plan_usuario = perfil.data.get("plan", "free")
        except:
            nombre = ""
            plan_usuario = "free"
        return jsonify({"ok": True, "plan": plan_data, "plan_usuario": plan_usuario, "nombre": nombre, "email": user_email})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 404

@app.route("/web/chat", methods=["POST"])
def web_chat():
    try:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        user = supabase.auth.get_user(token)
        user_id = user.user.id
        data = request.json
        mensaje = data.get("mensaje", "") or data.get("message", "")
        historial = data.get("historial", [])
        try:
            perfil = supabase.table("perfiles").select("*").eq("user_id", user_id).single().execute()
            p = perfil.data or {}
        except:
            p = {}
        import openai
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        system = SYSTEM_PROMPT_ELITE + f"""

PERFIL DEL USUARIO:
- Nombre: {p.get('nombre', '')}
- Género: {p.get('genero', '')} | Edad: {p.get('edad', '')} | Peso: {p.get('peso', '')}kg | Altura: {p.get('altura', '')}cm
- Objetivo: {p.get('objetivo', '')}
- Ejercicio: {p.get('ejercicio', '')}
- Cocina: {p.get('cocina', '')}
- Comidas/día: {p.get('comidas_dia', '')}
- Intolerancias: {p.get('intolerancias', 'ninguna')}
- Presupuesto: {p.get('presupuesto', 60)}€/semana
- Supermercado: {p.get('supermercado', 'Mercadona')}"""
        mensajes = [{"role": "system", "content": system}]
        for h in historial[-10:]:
            mensajes.append({"role": h["role"], "content": h["content"]})
        mensajes.append({"role": "user", "content": mensaje})
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=mensajes,
            max_tokens=2000
        )
        respuesta = response.choices[0].message.content
        supabase.table("perfiles").update({"chat_count": et("chat_count", 0) or 0) + 1}).eq("user_id", user_id).execute()
        return jsonify({"ok": True, "paywall": False, "respuesta": respuesta})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/web/checkout", methods=["POST"])
def checkout():
    try:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        user = supabase.auth.get_user(token)
        data = request.json
        plan = data.get("plan", "individual")
        price_map = {
            "individual": os.getenv("STRIPE_PRICE_INDIVIDUAL"),
            "dos_personas": os.getenv("STRIPE_PRICE_2_PERSONAS"),
            "familiar": os.getenv("STRIPE_PRICE_FAMILIAR")
        }
        price_id = price_map.get(plan)
        if not price_id:
            return jsonify({"ok": False, "error": "Plan no válido"}), 400
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": rice_id, "quantity": 1}],
            success_url="https://zianutricion.com?payment=success",
            cancel_url="https://zianutricion.com?payment=cancel",
            customer_email=user.user.email
        )
        return jsonify({"ok": True, "url": session.url})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/web/webhook", methods=["POST"])
def webhook():
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, os.getenv("STRIPE_WEBHOOK_SECRET"))
        if event["type"] == "checkout.session.completed":
            session = event["data"]["object"]
            customer_email = session.get("customer_email")
            if customer_email:
                user_res = supabase.auth.admin.list_users()
                for u in user_res:
                    if u.email == customer_email:
                        supabase.table("perfiles").update({"plan": "individual"}).eq("user_id", u.id).execute()
                        break
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
