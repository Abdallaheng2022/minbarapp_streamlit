"""
payments.py — الدفع والاشتراك لتطبيق منبر على Streamlit.

نظام عملي لا يحتاج خادم webhook (مناسب لـ Streamlit):
  1) رابط دفع مستضاف (LemonSqueezy / Paddle / Gumroad / Paymob) — المستخدم يدفع.
  2) بعد الدفع يحصل على «رمز تفعيل» (license code) — يلصقه في التطبيق فيُفعّل اشتراكه.
  3) الأدمن يولّد رموزًا (أو تُولّد من منتج المتجر) ويتابع المدفوعات.

جدول codes في القاعدة: code (نص), days (مدة), used_by, used_at, created_at.
"""
import datetime as dt
import secrets as _secrets
import streamlit as st
from supa import admin


def _now():
    return dt.datetime.now(dt.timezone.utc)


# ── توليد رموز التفعيل (أدمن) ──
def generate_codes(n=1, days=365, prefix="MINBAR"):
    rows = []
    for _ in range(n):
        code = f"{prefix}-{_secrets.token_hex(4).upper()}"
        rows.append({"code": code, "days": days})
    admin().table("codes").insert(rows).execute()
    return [r["code"] for r in rows]


def list_codes(limit=200):
    return admin().table("codes").select("*").order("created_at", desc=True).limit(limit).execute().data or []


# ── استبدال رمز تفعيل (مستخدم) ──
def redeem(user_id, code):
    code = (code or "").strip().upper()
    if not code:
        return False, "أدخل رمزًا."
    res = admin().table("codes").select("*").eq("code", code).limit(1).execute().data
    if not res:
        return False, "رمز غير صحيح."
    row = res[0]
    if row.get("used_by"):
        return False, "هذا الرمز مُستخدَم من قبل."
    days = int(row.get("days", 365))
    # فعّل الاشتراك
    until = (_now() + dt.timedelta(days=days)).isoformat()
    admin().table("profiles").update({"premium_until": until}).eq("id", user_id).execute()
    admin().table("codes").update({"used_by": user_id, "used_at": _now().isoformat()}).eq("code", code).execute()
    # سجّل الدفعة
    try:
        admin().table("payments").insert({
            "user_id": user_id, "provider": "code", "amount_cents": 0,
            "status": "paid", "external_id": code}).execute()
    except Exception:
        pass
    return True, f"تم تفعيل اشتراكك لمدة {days} يومًا. 🎉"


# ── سجلّ المدفوعات (أدمن) ──
def list_payments(limit=200):
    try:
        return admin().table("payments").select("*").order("created_at", desc=True).limit(limit).execute().data or []
    except Exception:
        return []


def checkout_url():
    try:
        return st.secrets.get("CHECKOUT_URL", "")
    except Exception:
        return ""


def price():
    try:
        return st.secrets.get("PRICE", "10")
    except Exception:
        return "10"
