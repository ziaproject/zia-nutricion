import os
import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates"
}

def upsert_user(user_id, data):
    try:
        url = f"{SUPABASE_URL}/rest/v1/usuarios"
        payload = {"id": str(user_id), **data}
        r = requests.post(url, json=payload, headers=HEADERS, timeout=10)
        return r.status_code < 300
    except Exception as e:
        print(f"Supabase error: {e}")
        return False

def get_user(user_id):
    try:
        url = f"{SUPABASE_URL}/rest/v1/usuarios"
        params = {"id": f"eq.{user_id}", "select": "*"}
        r = requests.get(url, params=params, headers=HEADERS, timeout=10)
        rows = r.json()
        return rows[0] if rows else None
    except Exception as e:
        print(f"Supabase error: {e}")
        return None
