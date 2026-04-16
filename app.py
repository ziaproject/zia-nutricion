from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    msg = request.form.get("Body", "")
    resp = MessagingResponse()
    resp.message(f"ZIA recibio: {msg}")
    return str(resp)

if __name__ == "__main__":
    app.run(port=5000)
