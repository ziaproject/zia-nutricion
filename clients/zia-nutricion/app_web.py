# -*- coding: utf-8 -*-
import os, json, stripe, sys, logging
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
load_dotenv()
from supabase import create_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
)
log = logging.getLogger("zia_nutricion_web")

app = Flask(__name__)
CORS(app, origins="*")
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))


def _perfil_desde_json_body(body):
    if not isinstance(body, dict):
        body = {}
    intol = body.get("intolerancias")
    if isinstance(intol, list):
        intol = ", ".join(str(x) for x in intol)
    return {
        "objetivo": body.get("objetivo") or "",
        "peso": body.get("peso") or "",
        "altura": body.get("altura") or "",
        "intolerancias": intol or "ninguna",
        "supermercado": body.get("supermercado") or "",
        "presupuesto": body.get("presupuesto") or "",
        "nombre": body.get("nombre") or "",
        "edad": body.get("edad") or "",
        "sexo": body.get("sexo") or "",
        "ejercicio": body.get("ejercicio") or "",
        "cocina": body.get("cocina") or "",
        "comidas_dia": body.get("comidas_dia") or "",
    }


def _desanidar_plan_bruto(raw):
    if not isinstance(raw, dict):
        return {}
    inner = raw.get("plan")
    if isinstance(inner, dict) and isinstance(inner.get("dias"), list):
        return inner
    inner2 = raw.get("plan_data")
    if isinstance(inner2, dict) and isinstance(inner2.get("dias"), list):
        return inner2
    return raw


def _normalizar_lista_compra(lc):
    if not isinstance(lc, dict):
        lc = {}
    out = dict(lc)
    cats = out.get("categorias")
    out["categorias"] = cats if isinstance(cats, dict) else {}
    return out


def _plan_dias_lista(plan_dict):
    """Normaliza la respuesta del LLM a {dias, lista_compra}."""
    raw = _desanidar_plan_bruto(plan_dict if isinstance(plan_dict, dict) else {})
    dias = raw.get("dias")
    if not isinstance(dias, list):
        dias = []
    lista = _normalizar_lista_compra(raw.get("lista_compra"))
    return {"dias": dias, "lista_compra": lista}


@app.route("/web/health")
def health(): return jsonify({"ok":True})


@app.route("/web/registro", methods=["POST"])
def registro():
    try:
        data=request.json
        res=supabase.auth.sign_up({"email":data["email"],"password":data["password"]})
        if not res.user: return jsonify({"ok":False,"error":"Error al crear cuenta"}),400
        uid=res.user.id
        supabase.table("perfiles").upsert({"user_id":uid,"nombre":data.get("nombre",""),"plan":"free","chat_count":0}).execute()
        return jsonify({"ok":True})
    except Exception as e:
        err=str(e)
        if "already" in err.lower(): return jsonify({"ok":False,"error":"Email ya registrado"}),400
        return jsonify({"ok":False,"error":err}),400

@app.route("/web/login", methods=["POST"])
def login():
    try:
        data=request.json
        res=supabase.auth.sign_in_with_password({"email":data["email"],"password":data["password"]})
        return jsonify({"ok":True,"token":res.session.access_token})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),401

@app.route("/web/perfil", methods=["POST"])
def perfil():
    try:
        token=request.headers.get("Authorization","").replace("Bearer ","")
        uid = "anonimo" if token in ("anonimo", "", None) else supabase.auth.get_user(token).user.id
        data=request.json
        supabase.table("perfiles").upsert({"user_id":uid,"objetivo":data.get("objetivo"),"intolerancias":data.get("intolerancias","ninguna"),"supermercado":data.get("supermercado"),"presupuesto":data.get("presupuesto",60),"peso":data.get("peso",70),"altura":data.get("altura",170)}).execute()
        return jsonify({"ok":True})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),400

@app.route("/web/generar-plan", methods=["POST"])
def generar_plan():
    try:
        token = (request.headers.get("Authorization") or "").replace("Bearer ", "").strip()
        body = request.get_json(force=True, silent=True) or {}
        anonimo = token.lower() in ("anonimo", "anonymous", "")
        uid = None

        import openai
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        if anonimo:
            log.info("generar-plan: token anonimo — sin Supabase")
            p = _perfil_desde_json_body(body)
            try:
                dias = int(body.get("dias") or 7)
            except (TypeError, ValueError):
                dias = 7
            dias = max(1, min(dias, 7))
        else:
            u = supabase.auth.get_user(token).user
            uid = u.id
            try:
                pf = (
                    supabase.table("perfiles")
                    .select("*")
                    .eq("user_id", uid)
                    .single()
                    .execute()
                    .data
                    or {}
                )
            except Exception:
                pf = {}
            merged = {**body, **{k: v for k, v in pf.items() if v is not None}}
            p = _perfil_desde_json_body(merged)
            plan_usuario = pf.get("plan", "free")
            dias = 7 if plan_usuario != "free" else 3

        schema = (
            '{"dias":[{"dia":"Lunes","comidas":[{"tipo":"Desayuno","nombre":"","descripcion_breve":"",'
            '"ingredientes_texto":"","kcal":0,"proteinas_g":0,"carbos_g":0,"grasas_g":0}]}],'
            '"lista_compra":{"categorias":{"Fruta y Verdura":[{"producto":"","cantidad":"","peso_o_unidad":"",'
            '"precio_estimado_eur":0}]},"total_estimado_eur":0}}'
        )
        prompt = f"""Eres ZIA, nutricionista. Crea un plan de alimentacion de {dias} dias para:
- Nombre: {p.get("nombre","")}, edad {p.get("edad","")}, sexo {p.get("sexo","")}, {p.get("peso","")} kg, {p.get("altura","")} cm
- Objetivo: {p.get("objetivo","comer sano")}
- Ejercicio: {p.get("ejercicio","")}
- Cocina: {p.get("cocina","")}
- Comidas al dia: {p.get("comidas_dia","3")}
- Intolerancias: {p.get("intolerancias","ninguna")}
- Presupuesto: {p.get("presupuesto",60)} eu/semana
- Supermercado: {p.get("supermercado","Mercadona")}

En cada comida incluye "ingredientes_texto" con cantidades en g, ml o ud.
Responde SOLO JSON valido, sin markdown. Estructura obligatoria (mismas claves):
{schema}
Exactamente {dias} elementos en "dias". lista_compra con categorias y total_estimado_eur."""

        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=4000,
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


@app.route("/web/mi-plan")
def mi_plan():
    try:
        token=request.headers.get("Authorization","").replace("Bearer ","")
        user=supabase.auth.get_user(token).user
        uid=user.id
        try: plan=supabase.table("planes").select("*").eq("user_id",uid).order("created_at",desc=True).limit(1).single().execute().data.get("plan_data")
        except: plan=None
        try: pf=supabase.table("perfiles").select("nombre,plan").eq("user_id",uid).single().execute().data
        except: pf={}
        return jsonify({"ok":True,"plan":plan,"plan_usuario":pf.get("plan","free"),"nombre":pf.get("nombre",""),"email":user.email})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),404

@app.route("/web/chat", methods=["POST"])
def chat():
    try:
        token=request.headers.get("Authorization","").replace("Bearer ","")
        uid = "anonimo" if token in ("anonimo", "", None) else supabase.auth.get_user(token).user.id
        data=request.json
        pf=supabase.table("perfiles").select("*").eq("user_id",uid).single().execute().data or {}
        plan=pf.get("plan","free")
        cnt=pf.get("chat_count",0) or 0
        if plan=="free" and cnt>=3:
            return jsonify({"ok":True,"respuesta":"Has usado tus mensajes gratuitos. Elige un plan para seguir chateando con ZIA sin límites 👇","paywall":True})
        import openai
        client=openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        msgs=[{"role":"system","content":f"Eres ZIA, nutricionista cercanaspañol, sin markdown, máximo 3 párrafos. Perfil: objetivo={pf.get('objetivo','')}, intolerancias={pf.get('intolerancias','')}, supermercado={pf.get('supermercado','Mercadona')}."}]
        for h in data.get("historial",[])[-6:]: msgs.append({"role":h["role"],"content":h["content"]})
        msgs.append({"role":"user","content":data.get("mensaje","")})
        res=client.chat.completions.create(model="gpt-4o-mini",messages=msgs,max_tokens=500)
        supabase.table("perfiles").update({"chat_count":cnt+1}).eq("user_id",uid).execute()
        return jsonify({"ok":True,"respuesta":res.choices[0].message.content,"paywall":False})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.route("/web/checkout", methods=["POST"])
def checkout():
    try:
        token=request.headers.get("Authorization","").replace("Bearer ","")
        uid = "anonimo" if token in ("anonimo", "", None) else supabase.auth.get_user(token).user.id
        plan=request.json.get("plan","individual")
        precios={"individual":os.getenv("STRIPE_PRICE_INDIVID"),"dos_personas":os.getenv("STRIPE_PRICE_DOS_PERSONAS"),"familiar":os.getenv("STRIPE_PRICE_FAMILIAR")}
        s=stripe.checkout.Session.create(payment_method_types=["card"],mode="subscription",line_items=[{"price":precios.get(plan),"quantity":1}],success_url="https://zianutricion.com/?pago=ok",cancel_url="https://zianutricion.com/?pago=cancelado",metadata={"user_id":uid,"plan":plan})
        return jsonify({"ok":True,"url":s.url})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.route("/web/webhook", methods=["POST"])
def webhook():
    try:
        event=stripe.Webhook.construct_event(request.data,request.headers.get("Stripe-Signature"),os.getenv("STRIPE_WEBHOOK_SECRET"))
        if event["type"]=="checkout.session.completed":
            m=event["data"]["object"]["metadata"]
            supabase.table("perfiles").update({"plan":m["plan"]}).eq("user_id",m["user_id"]).execute()
        return jsonify({"ok":True})
    except Exception as e: return jsonify({"error":str(e)}),400


@app.after_request
def add_cors(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
    return response


if __name__ == "__main__":
    app.run(debug=True, port=5001)
