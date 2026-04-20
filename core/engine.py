import json
import os
import re
from openai import OpenAI

def load_client_config(client_id):
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "clients", client_id, "config.json")
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def get_engine(client_id=None):
    if client_id is None:
        client_id = os.environ.get("CLIENT_ID", "zia-nutricion")
    if client_id not in _cache:
        _cache[client_id] = ZiaEngine(client_id)
    return _cache[client_id]

_cache = {}

class ZiaEngine:
    def __init__(self, client_id):
        self.client_id = client_id
        self.config = load_client_config(client_id)
        self.openai = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        self._users = {}
    def process_message(self, user_id, message, plan_type="pro"):
        return "Hola! Soy ZIA. Escribe hola para empezar."
    def get_welcome_message(self):
        return self.config["bot"]["welcome_message"]
    def reset_user(self, user_id):
        self._users.pop(user_id, None)
