from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import os

app = Flask(__name__)

@app.route("/", methods=["GET"])
@app.route("/webhook", methods=["GET"])
def health():
    return "ZIA OK", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    msg = request.form.get("Body", "")
    resp = MessagingResponse()
    resp.message(f"ZIA recibio: {msg}")
    return str(resp)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
