from whatsapp import app

@app.route("/web/health", methods=["GET"])
def health():
    from flask import jsonify
    return jsonify({"status": "ok", "service": "zia-nutricion-web"})
