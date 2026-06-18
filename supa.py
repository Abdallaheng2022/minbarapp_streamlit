"""supa.py — Supabase (auth + data) لتطبيق منبر على Streamlit."""
import streamlit as st
from supabase import create_client, Client


@st.cache_resource
def _client(service: bool = False) -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_SERVICE_KEY"] if service else st.secrets["SUPABASE_ANON_KEY"]
    return create_client(url, key)


def anon(): return _client(False)
def admin(): return _client(True)


def sign_up(email, password):
    return anon().auth.sign_up({"email": email, "password": password})

def sign_in(email, password):
    return anon().auth.sign_in_with_password({"email": email, "password": password})

def get_profile(user_id):
    return admin().table("profiles").select("*").eq("id", user_id).single().execute().data

def is_premium(p):
    if not p: return False
    if p.get("role") == "admin": return True
    import datetime as dt
    pu = p.get("premium_until")
    if not pu: return False
    try:
        return dt.datetime.fromisoformat(pu.replace("Z", "+00:00")) > dt.datetime.now(dt.timezone.utc)
    except Exception:
        return False

def set_premium(user_id, days=365):
    import datetime as dt
    until = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days)).isoformat()
    admin().table("profiles").update({"premium_until": until}).eq("id", user_id).execute()

def set_ui_language(user_id, lang):
    if lang not in ("ar","en","tr","hi","es","de","fr","ru"): lang = "ar"
    admin().table("profiles").update({"ui_language": lang}).eq("id", user_id).execute()

def list_users():
    return admin().table("profiles").select("*").order("created_at").execute().data

def create_project(owner_id, title, language, duration, is_limited, transcript):
    return admin().table("projects").insert({
        "owner_id": owner_id, "title": title, "language": language,
        "duration": duration, "is_limited": is_limited,
        "transcript": transcript, "status": "done"}).execute().data[0]

def update_transcript(pid, transcript):
    admin().table("projects").update({"transcript": transcript}).eq("id", pid).execute()
