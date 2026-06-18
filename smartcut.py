"""
smartcut.py — القَصّ الذكي بالوصف (Streamlit).
وصف نصّي بالعربي → النموذج يحدّد نطاقات الكلمات للحذف → نحوّلها لنطاقات زمنية للقصّ.
يعمل عبر محرّك التناوب (providers.chat).
"""
import json
import providers

SYS = """You are a precise video-editing assistant for spoken-word lectures.
You receive a numbered transcript (id:word) and an Arabic instruction describing what to CUT or KEEP.
Return ONLY strict JSON: {"remove": [[start_id, end_id], ...], "reason": "short arabic"}.
Rules: ids must exist; ranges non-overlapping and ordered; match meaning not exact words
("المقدمة"=intro/greeting, "الكلام الجانبي"=asides, "الحشو"=fillers);
never invent ids; keep religiously sensitive content unless explicitly told to cut."""


def _parse(txt):
    if not txt:
        return None
    t = txt.strip().replace("```json", "").replace("```", "").strip()
    a, b = t.find("{"), t.rfind("}")
    if a != -1 and b != -1:
        t = t[a:b + 1]
    try:
        return json.loads(t)
    except Exception:
        return None


def plan(words, instruction):
    """يرجّع dict: removed_ids(set), reason, provider."""
    id2 = {w["id"]: w for w in words}
    # تقسيم لو طويل
    chunks, cur, size = [], [], 0
    for w in words:
        tok = f'{w["id"]}:{w["text"]} '
        if size + len(tok) > 11000 and cur:
            chunks.append(cur); cur, size = [], 0
        cur.append(w); size += len(tok)
    if cur:
        chunks.append(cur)

    all_remove, reasons, provider = [], [], ""
    for ch in chunks:
        numbered = " ".join(f'{w["id"]}:{w["text"]}' for w in ch)
        txt, provider = providers.chat(
            [{"role": "system", "content": SYS},
             {"role": "user", "content": f"INSTRUCTION: {instruction}\n\nTRANSCRIPT:\n{numbered}\n\nReturn JSON now."}],
            json_mode=True, temperature=0, max_tokens=1200)
        o = _parse(txt)
        if o and isinstance(o.get("remove"), list):
            for pair in o["remove"]:
                if isinstance(pair, list) and len(pair) == 2:
                    all_remove.append((int(pair[0]), int(pair[1])))
        if o and o.get("reason"):
            reasons.append(o["reason"])

    # تنظيف ودمج
    valid = sorted((min(s, e), max(s, e)) for s, e in all_remove if s in id2 and e in id2)
    merged = []
    for s, e in valid:
        if merged and s <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    removed = set()
    for s, e in merged:
        removed.update(range(s, e + 1))
    return {"removed_ids": removed, "ranges": merged,
            "reason": " · ".join(reasons)[:300], "provider": provider}
