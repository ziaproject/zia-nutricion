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
        }).execute()
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
        user = supabase.auth.get_user(token)
        user_id = user.user.id
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
        token = (request.headers.get("Authorization", "") or "").replace("Bearer ", "").strip()
        body = request.get_json(force=True, silent=True) or {}
        anonimo = token.lower() in ("anonimo", "anonymous", "")
        uid = None

        import openai
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        if anonimo:
            p = _perfil_desde_json_body(body)
        else:
            u = supabase.auth.get_user(token).user
            uid = u.
            try:
                pf = supabase.table("perfiles").select("*").eq("user_id", uid).single().execute().data or {}
            except Exception:
                pf = {}
            merged = {**body, **{k: v for k, v in pf.items() if v is not None}}
            p = _perfil_desde_json_body(merged)

        dias = 7

        # PROMPT SOLO DÍAS (sin lista de la compra) — más rápido
        schema_dias = {"dias": [{"dia": "Lunes", "comidas": [{"tipo": "Desayuno", "nombre": "", "descripcion_breve": "", "ingredientes_texto": "", "kcal": 0, "proteinas_g": 0, "carbos_g": 0, "grasas_g": 0}]}]}

        prompt = f"""Eres ZIA, nutricionista. Crea un plan de alimentación de {dias} días para:
- Nombre: {p.get("nombre","")}
- Objetivo: {p.get("objetivo","comer sano")}
- Ejercicio: {p.get("ejercicio","")}
- Intolerancias: {p.get("intolerancias","ninguna")}
- Supermercado: {p.get("supermercado","Mercadona")}

Responde SOLO JSON sin markdown. Exactamente {dias} elementos en "dias", cada uno con 3 comidas (o, Almuerzo, Cena).
{schema_dias}"""

        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=3500,
        )
        plan_raw = json.loads(res.choices[0].message.content or "{}")
        plan_out = _plan_dias_lista(plan_raw)

        if not anonimo and uid is not None:
            try:
                supabase.table("planes").upsert({"user_id": uid, "plan_data": plan_out}).execute()
            except Exception as ex:
                log.warning("generar-plan: no se guardo en Supabase: %s", ex)

        return jsonify({"ok": True, "plan": plan_out})
    except Exception as e:
        log.exception("generar-plan: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/web/generar-lista", methods=["POST"])
def generar_lista():
    try:
        token = (request.headers.get("Authorization", "") or "").replace("Bearer ", "").strip()
        body = request.get_json(force=True, silent=True) or {}
        anonimo = token.lower() in ("anonimo", "anonymous", "")

        import openai
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        if anonimo:
            p = _perfil_desde_json_body(body)
        else:
            u = supabase.auth.get_user(token).user
            uid = u.id
            try:
                pf = supabase.table("perfiles").select("*").eq("user_id", uid).single().execute().data or {}
            except Exception:
                pf = {}
            merged = {**body, **{k: v for k, v in pf.items() if v is not None}}
            p = _perfil_desde_json_body(merged)

        plan_dias = body.get("plan_dias", [])
        supermercado = p.get("supermercado", "Mercadona")

        prompt = f"""Basándote en este plan semanal, genera la lista de la compra completa para {supermercado}.
Plan: {json.dumps(plan_dias, ensure_ascii=False)[:2000]}

Responde SOLO JSON sin markdown:
{{"categorias": {{"Frutas yVerduras": [{{"nombre": "Tomates", "cantidad": "500g", "precio": 1.20}}]}}}}

Incluye precios reales de {supermercado}. Agrupa en categorías: Frutas y Verduras, Carnes y Pescados, Lácteos y Huevos, Cereales y Legumbres, Otros."""

        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=2000,
        )
        lista_raw = json.loads(res.choices[0].message.content or "{}")
        return jsonify({"ok": True, "lista_compra": lista_raw})
    except Exception as e:
        log.exception("generar-lista: %s", e)
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
        token = (request.headers.get("Authorization") or "").replace("Bearer ", "").strip()
        data = request.json or {}
        mensaje = data.get("mensaje", "") or data.get("message", "")
        historial = data.get("historial", [])
        body_perfil = data.get("perfil")
        if not isinstance(body_perfil, dict):
            body_perfil = {}

        user_id = None
        # Sin token, token explícito anónimo, o usuario/perfil no en Supabase → perfil del body
        if not token or token.lower() == "anonimo":
            p = body_perfil
        else:
            try:
                user = supabase.auth.get_user(token)
                user_id = user.user.id
            except Exception:
                user_id = None
                p = body_perfil
            else:
                try:
                    perfil_res = (
                        supabase.table("perfiles")
                        .select("*")
                        .eq("user_id", user_id)
                        .single()
                        .execute()
                    )
                    p = perfil_res.data or {}
                except Exception:
                    p = {}
                if not p:
                    p = body_perfil

        import openai
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        genero_o_sexo = p.get("genero", "") or p.get("sexo", "")
        system = SYSTEM_PROMPT_ELITE + f"""

PERFIL DEL USUARIO:
- Nombre: {p.get('nombre', '')}
- Género: {genero_o_sexo} | Edad: {p.get('edad', '')} | Peso: {p.get('peso', '')}kg | Altura: {p.get('altura', '')}cm
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
        if user_id is not None:
            try:
                row = (
                    supabase.table("perfiles")
                    .select("chat_count")
                    .eq("user_id", user_id)
                    .single()
                    .execute()
                )
                n = (row.data or {}).get("chat_count") or 0
                supabase.table("perfiles").update({"chat_count": int(n) + 1}).eq(
                    "user_id", user_id
                ).execute()
            except Exception:
                pass
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
            line_items=[{"price": price_id, "quantity": 1}],
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
