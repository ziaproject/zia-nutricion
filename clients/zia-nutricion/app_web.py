import os
import json
import stripe
from flask import Flask, request, jsonify, Blueprint
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

from supabase import create_client
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

web_bp = Blueprint('zia_nutricion_web', __name__)


@web_bp.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "service": "zia-nutricion-web"})


@web_bp.route('/registro', methods=['POST'])
def registro():
    try:
        data = request.json
        res = supabase.auth.sign_up({
            "email": data.get("email"),
            "password": data.get("password")
        })
        user_id = res.user.id
        supabase.table("usuarios").insert({
            "id": user_id,
            "email": data.get("email"),
            "nombre": data.get("nombre"),
            "plan": "free"
        }).execute()
        return jsonify({"ok": True, "user_id": user_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@web_bp.route('/login', methods=['POST'])
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


@web_bp.route('/perfil', methods=['POST'])
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


@web_bp.route('/generar-plan', methods=['POST'])
def generar_plan():
    try:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        user = supabase.auth.get_user(token)
        user_id = user.user.id

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
lista_compra es null si son 3 días (free). Si son 7 días incluye lista agrupada por categorías."""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=4000
        )

        plan_json = json.loads(response.choices[0].message.content)
        plan_json["plan_usuario"] = plan_usuario
        plan_json["dias_generados"] = dias

        supabase.table("planes").upsert({
            "user_id": user_id,
            "plan_data": plan_json
        }).execute()

        return jsonify({"ok": True, "plan": plan_json})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@web_bp.route('/checkout', methods=['POST'])
def checkout():
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


@web_bp.route('/webhook', methods=['POST'])
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


@web_bp.route('/mi-plan', methods=['GET'])
def mi_plan():
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


def register_routes(flask_app):
    CORS(flask_app, origins=["https://zianutricion.com", "http://localhost:3000"])
    flask_app.register_blueprint(web_bp, url_prefix='/web')


app = Flask(__name__)
register_routes(app)


if __name__ == "__main__":
    app.run(debug=True, port=5001)
