import os
import json
import stripe
from flask import Flask, request, jsonify, make_response
from flask_cors import CORS
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
from core.engine import ZiaEngine

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=False)

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

@app.route("/web/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "zia-nutricion-web"})

@app.route("/web/registro", methods=["POST", "OPTIONS"])
def registro():
    if request.method == "OPTIONS":
        return make_response('', 204)
    try:
        data = request.json
        res = supabase.auth.sign_up({
            "email": data.get("email"),
            "password": data.get("password")
        })
        if res.user is None:
            return jsonify({"ok": False, "error": "No se pudo crear el usuario"}), 400
        user_id = res.user.id
        try:
            supabase.table("usuarios").insert({
                "id": user_id,
                "email": data.get("email"),
                "nombre": data.get("nombre", ""),
                "plan": "free"
            }).execute()
        except Exception as e_insert:
            print(f"[WARN] No se pudo insertar en usuarios: {e_insert}")
        return jsonify({"ok": True, "user_id": user_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@app.route("/web/login", methods=["POST", "OPTIONS"])
def login():
    if request.method == "OPTIONS":
        return make_response('', 204)
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

@app.route("/web/perfil", methods=["POST", "OPTIONS"])
def guardar_perfil():
    if request.method == "OPTIONS":
        return make_response('', 204)
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

@app.route("/web/generar-plan", methods=["POST", "OPTIONS"])
def generar_plan():
    if request.method == "OPTIONS":
        return make_response('', 204)
    try:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        user = supabase.auth.get_user(token)
        user_id = user.user.id
        data = request.json

        usuario = supabase.table("usuarios").select("plan").eq("id", user_id).single().execute()
        plan_usuario = usuario.data.get("plan", "free")
        dias = 7 if plan_usuario != "free" else 3

        perfil = supabase.table("perfiles").select("*").eq("user_id", user_id).single().execute()
        p = perfil.data

        import openai
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        prompt = f"""Eres ZIA, nutricionista inteligente. Crea un plan de {dias} días para:
- Objetivo: {p['objetivo']}
- Peso: {p['peso']}kg, Altura: {p['altura']}cm
- Intolerancias: {p['intolerancias']}
- Supermercado preferido: {p['supermercado']}
- Presupuesto semanal: {p['presupuesto']}

Devuelve SOLO JSON con esta estructura:
{{"dias": [{{"dia": "Lunes", "desayuno": {{"nombre": "", "calorias": 0, "proteinas": 0, "carbos": 0, "grasas": 0}}, "comida": {{"nombre": "", "calorias": 0, "proteinas": 0, "carbos": 0, "grasas": 0}}, "cena": {{"nombre": "", "calorias": 0, "proteinas": 0, "carbos": 0, "grasas": 0}}}}], "lista_compra": null}}"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=4000
        )
        plan_json = json.load(response.choices[0].message.content)
        plan_json["plan_usuario"] = plan_usuario
        plan_json["dias_generados"] = dias

        supabase.table("planes").upsert({
            "user_id": user_id,
            "plan_data": plan_json
        }).execute()

        return jsonify({"ok": True, "plan": plan_json})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/web/generar-plan-preview", methods=["POST", "OPTIONS"])
def generar_plan_preview():
    if request.method == "OPTIONS":
        return make_response('', 204)
    try:
        data = request.json or {}
        import openai
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        prompt = """Crea un plan de muestra de 1 día (solo lunes) para demostración. Devuelve SOLO JSON:
{"dias": [{"dia": "Lunes", "desayuno": {"nombre": "Tostadas con aguacate y huevo pochado", "calorias": 380, "proteinas": 18, "carbos": 32, "grasas": 22}, "comida": {"nombre": "Pollo al horno con verduras",calorias": 520, "proteinas": 42, "carbos": 28, "grasas": 18}, "cena": {"nombre": "Crema de calabaza con tostadas", "calorias": 310, "proteinas": 12, "carbos": 38, "grasas": 14}}], "lista_compra": null}"""
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=500
        )
        plan_json = json.loads(response.choices[0].message.content)
        return jsonify({"ok": True, "plan": plan_json})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/web/mi-plan", methods=["GET", "OPTIONS"])
def mi_plan():
    if request.method == "OPTIONS":
        return make_response('', 204)
    try:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        user = supabase.auth.get_user(token)
        user_id = user.user.id
        plan = supabase.table("planes").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(1).single().execute()
        usuario = supabase.table("usuarios").select("plan, nombre").eq("id", user_id).single().execute()
        return jsonify({
            "ok": True,
            "plan": plan.data.get("plan_data"),
            "plan_usuario": usuario.data.get("plan"),
            "nombre": usuario.data.get("nombre")
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 404

@app.route("/web/checkout", methods=["POST", "OPTIONS"])
def checkout():
    if request.method == "OPTIONS":
        return make_response('', 204)
    try:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        user = supabase.auth.get_user(token)
        user_id = user.user.id
        data = request.json
        precio_id = os.getenv("STRIPE_PRICE_INDIVIDUAL") if data.get("plan") == "individual" else os.getenv("STRIPE_PRICE_PRO")
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": precio_id, "quantity": 1}],
            success_url="https://zianutricion.com/app?pago=ok",
            cancel_url="https://zianutricion.com/app?pago=cancelado",
            metadata={"user_id": user_id, "plan": data.get("plan")}
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
        supabase.table("usuarios").update({"plan": plan}).eq("id", user_id).execute()
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(debug=True, port=5001)


@app.route("/web/chat", methods=["POST", "OPTIONS"])
def chat_web():
    if request.method == "OPTIONS":
        return make_response("", 204)
    try:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        user = supabase.auth.get_user(token)
        data = request.json
        mensaje = data.get("mensaje", "")
        historial = data.get("historial", [])
        perfil = data.get("perfil", {})

        import openai
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        system_prompt = (
            "Eres ZIA, nutricionista experta y coach motivacional. "
            "Respondes siempre en espanol con calidez, empatia y conocimiento real de nutricion. "
            "Hablas como una amiga que sabe muchisimo de nutricion, no como un robot. "
            "Eres motivadora, celebras los logros del usuario y das consejos practicos y concretos. "
            "Conoces los precios de los supermercados espanoles. "
            "Cuando el usuario pida ver su plan o lista, dile que puede verlos en las pestanas de arriba. "
            "Maximo 3-4 parrafos cortos. Usa emojis con naturalidad pero sin exceso.\n\n"
            "Perfil del usuario:\n"
            "- Objetivo: " + perfil.get("objetivo", "no especificado") + "\n"
            "- Intolerancias: " + perfil.get("intolerancias", "ninguna") + "\n"
            "- Supermercado: " + perfil.get("supermercado", "Mercadona") + "\n"
            "- Plan: " + perfil.get("plan", "free")
        )

        messages = [{"role": "system", "content": system_prompt}]
        messages += historial[-10:]
        messages.append({"role": "user", "content": mensaje})

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=400,
            temperature=0.85
        )

        respuesta = response.choices[0].message.content
        return jsonify({"ok": True, "respuesta": respuesta})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
