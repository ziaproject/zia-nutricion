import os
import json
import stripe
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
from core.engine import ZiaEngine

app = Flask(__name__)
CORS(app, origins=["https://zianutricion.com", "https://www.zianutricion.com", "http://localhost:3000", "http://localhost:5001"])

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

from supabase import create_client
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

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

        # Registro en Supabase Auth
        res = supabase.auth.sign_up({
            "email": email,
            "password": password
        })

        if not res.user:
            return jsonify({"ok": False, "error": "Error al crear la cuenta"}), 400

        user_id = res.user.id

        # Guardar nombre en perfiles (no en usuarios)
        supabase.table("perfiles").upsert({
            "user_id": user_id,
            "nombre": nombre,
            "plan": "free"
        }).execute()

        return jsonify({"ok": True, "user_id": user_id})
    except Exception as e:
        error_msg = str(e)
        # Si el usuario ya existe, no es un error bloqueante
        if "already registered" in error_msg or "already exists" in error_msg:
            return jsonify({"ok": False, "error": "Este email ya está registrado"}), 400
        return jsonify({"ok": False, "error": error_msg}), 400

@app.route("/web/login", methods=["POST"])
def login():
    try:
        data = request.json
        res = supabase.auth.sign_in_with_password({
            "email": data.get("email"),
            "password": data.get("password")
        })
        return jsonify({
            "ok": True,
            "token": res.session.access_token,
            "user_id": res.user.id
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 401

@app.route("/web/perfil", methods=["POST"])
def guardar_perfil():
    try:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        user = supabase.auth.get_user(token)
        user_id = user.user.id
        data = request.json

        supabase.table("perfiles").upsert({
            "user_id": user_id,
            "objetivo": data.get("objetivo"),
            "peso": data.get("peso"),
            "altura": data.get("altura"),
            "intolerancias": data.get("intolerancias", "ninguna"),
            "supermercado": data.get("supermercado"),
            "presupuesto": data.get("presupuesto")
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

        # Leer plan desde perfiles
        perfil = supabase.table("perfiles").select("*").eq("user_id", user_id).single().execute()
        p = perfil.data or {}
        plan_usuario = p.get("plan", "free")
        dias = 7 if plan_usuario != "free" else 1

        import openai
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        prompt = f"""Eres ZIA, nutricionista inteligente. Crea un plan de {dias} día(s) para:
- Objetivo: {p.get('objetivo', 'comer sano')}
- Intolerancias: {p.get('intolerancias', 'ninguna')}
- Supermercado preferido: {p.get('supermercado', 'Mercadona')}
- Presupuesto semanal: {p.get('presupuesto', 60)}€

Devuelve SOLO JSON con esta estructura exacta:
{{
  "dias": [
    {{
      "dia": "Lunes",
      "desayuno": {{"nombre": "", "calorias": 0, "proteinas": 0, "carbos": 0, "grasas": 0}},
      "comida": {{"nombre": "", "calorias": 0, "proteinas": 0, "carbos": 0, "grasas": 0}},
      "cena": {{"nombre": "", "calorias": 0, "proteinas": 0, "carbos": 0, "grasas": 0}}
    }}
  ],
  "lista_compra": null
}}
Si el plan es de 7 días, incluye lista_compra agrupada por categorías con precios de {p.get('supermercado', 'Mercadona')}."""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=4000
        )

        plan_json = json.loads(response.choices[0].message.content)
        plan_json["plan_usuario"] = plan_usuario

        supabase.table("planes").upsert({
            "user_id": user_id,
            "plan_data": plan_json
        }).execute()

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

        # Leer plan
        try:
            plan = supabase.table("planes").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(1).single().execute()
            plan_data = plan.data.get("plan_data")
        except:
            plan_data = None

        # Leer perfil (nombre y plan)
        try:
            perfil = supabase.table("perfiles").select("nombre, plan").eq("user_id", user_id).single().execute()
            nombre = perfil.data.get("nombre", "")
            plan_usuario = perfil.data.get("plan", "free")
        except:
            nombre = ""
            plan_usuario = "free"

        return jsonify({
            "ok": True,
            "plan": plan_data,
            "plan_usuario": plan_usuario,
            "nombre": nombre,
            "email": user_email
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 404

@app.route("/web/chat", methods=["POST"])
def web_chat():
    try:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        user = supabase.auth.get_user(token)
        user_id = user.user.id
        data = request.json
        mensaje = data.get("mensaje", "")

        # Leer plan y chat_count desde perfiles
        perfil = supabase.table("perfiles").select("*").eq("user_id", user_id).single().execute()
        p = perfil.data or {}
        plan = p.get("plan", "free")
        chat_count = p.get("chat_count", 0) or 0

        if plan == "free" and chat_count >= 3:
            return jsonify({
                "ok": True,
                "respuesta": "Has usado tus mensajes gratuitos. Para seguir chateando con ZIA sin límites, elige tu plan 👇",
                "paywall": True
            })

        import openai
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        historial = data.get("historial", [])
        mensajes = [{"role": "system", "content": f"""Eres ZIA, nutricionista inteligente y cercana. Respondes en español, sin markdown, máximo 3 párrafos cortos y directos.
Perfil: objetivo={p.get('objetivo','')}, intolerancias={p.get('intolerancias','')}, supermercado={p.get('supermercado','Mercadona')}, nombre={p.get('nombre','')}."""}]

        for h in historial[-6:]:
            mensajes.append({"role": h["role"], "content": h["content"]})
        mensajes.append({"role": "user", "content": mensaje})

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=mensajes,
            max_tokens=500
        )
        respuesta = response.choices[0].message.content

        # Actualizar chat_count en perfiles
        supabase.table("perfiles").update({"chat_count": chat_count + 1}).eq("user_id", user_id).execute()

        return jsonify({"ok": True, "respuesta": respuesta, "paywall": False})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/web/checkout", methods=["POST"])
def checkout():
    try:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        user = supabase.auth.get_user(token)
        user_id = user.user.id
        data = request.json
        plan = data.get("plan", "individual")

        precios = {
            "individual": os.getenv("STRIPE_PRICE_INDIVIDUAL"),
            "dos_personas": os.getenv("STRIPE_PRICE_DOS_PERSONAS"),
            "familiar": os.getenv("STRIPE_PRICE_FAMILIAR")
        }
        precio_id = precios.get(plan, os.getenv("STRIPE_PRICE_INDIVIDUAL"))

        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": precio_id, "quantity": 1}],
            success_url="https://zianutricion.com/?pago=ok",
            cancel_url="https://zianutricion.com/?pago=cancelado",
            metadata={"user_id": user_id, "plan": plan}
        )
        return jsonify({"ok": True, "url": session.url})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/web/webhook", methods=["POST"])
def webhook():
    payload = request.data
    sig = request.headers.get("Stripe-Signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig, os.getenv("STRIPE_WEBHOOK_SECRET"))
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = session["metadata"]["user_id"]
        plan = session["metadata"]["plan"]
        # Actualizar plan en perfiles
        supabase.table("perfiles").update({"plan": plan}).eq("user_id", user_id).execute()

    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(debug=True, port=5001)
