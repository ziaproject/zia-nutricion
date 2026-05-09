import os, json, stripe
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
load_dotenv()
from supabase import create_client
app = Flask(__name__)
CORS(app, origins=["https://zianutricion.com","https://www.zianutricion.com","http://localhost:5001"])
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

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
        uid=supabase.auth.get_user(token).user.id
        data=request.json
        supabase.table("perfiles").upsert({"user_id":uid,"objetivo":data.get("objetivo"),"intolerancias":data.get("intolerancias","ninguna"),"supermercado":data.get("supermercado"),"presupuesto":data.get("presupuesto",60),"peso":data.get("peso",70),"altura":data.get("altura",170)}).execute()
        return jsonify({"ok":True})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),400

@app.route("/web/generar-plan", methods=["POST"])
def generar_plan():
    try:
        token=request.headers.get("Authorization","").replace("Bearer ","")
        uid=supabase.auth.get_user(token).user.id
        p=supabase.table("perfiles").select("*").eq("user_id",uid).single().execute().data or {}
        plan_usuario=p.get("plan","free")
        dias=7 if plan_usuario!="free" else 1
        import openai
        client=openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        prompt=f"""Eres ZIA nutricionista. Crea plan de {dias} día(s).
Objetivo:{p.get('objetivo','comer sano')}, Intolerancias:{p.get('intolerancias','ninguna')}, Supermercado:{p.get('supermercado','Mercadona')}
Devuelve SOLO JSON: {{"dias":[{{"dia":"Lunes","desayuno":{{"nombre":"","calorias":0,"proteinas":0,"carbos":0,"grasas":0}},"comida":{{"nombre":"","calorias":0,"proteinas":0,"carbos":0,grasas":0}},"cena":{{"nombre":"","calorias":0,"proteinas":0,"carbos":0,"grasas":0}}}}],"lista_compra":null}}"""
        res=client.chat.completions.create(model="gpt-4o-mini",messages=[{"role":"user","content":prompt}],response_format={"type":"json_object"},max_tokens=4000)
        plan=json.loads(res.choices[0].message.content)
        plan["plan_usuario"]=plan_usuario
        supabase.table("planes").upsert({"user_id":uid,"plan_data":plan}).execute()
        return jsonify({"ok":True,"plan":plan})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

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
        uid=supabase.auth.get_user(token).user.id
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
        uid=supabase.auth.get_user(token).user.id
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

if __name__=="__main__": app.run(debug=True,port=5001)
