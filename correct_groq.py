"""
correct_groq.py — تصحيح لغوي عبر أي مزوّد متوافق مع OpenAI (8 لغات) + glossary،
مع الحفاظ على التوقيتات. اسم الملف للتوافق فقط؛ يدعم Groq/Google/Cerebras/OpenRouter/Ollama محلي.
"""
import json, re, difflib, urllib.request

DEFAULT_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "qwen-3-32b"  # مفتوح المصدر (Apache 2.0) — الأقوى متعدد اللغات
LANG_NAMES = {"ar":"Arabic","en":"English","tr":"Turkish","hi":"Hindi","es":"Spanish","de":"German","fr":"French","ru":"Russian"}


def _prompt(language, glossary=""):
    name = LANG_NAMES.get(language, "the same")
    p = (f"You are a professional {name} proofreader. Fix ONLY automatic transcription "
         f"errors in {name} talks (spelling, homophones, punctuation, wrongly split/merged "
         "words) without rephrasing, reordering, or changing meaning, and without translating. "
         "Keep the speaker's wording and dialect. Do not alter sacred or quoted religious "
         "wording unless it is a clear error. Return ONLY the corrected text.")
    if glossary:
        p += "\nPreferred spellings — keep these exactly: " + glossary
    return p


def _call_llm(text, language, api_key, glossary="", base_url=DEFAULT_URL, model=DEFAULT_MODEL):
    body = {"model": model, "temperature": 0,
            "messages": [{"role": "system", "content": _prompt(language, glossary)},
                         {"role": "user", "content": (text + " /no_think") if "qwen3" in (model or "") else text}]}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(base_url, data=json.dumps(body).encode("utf-8"), headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"].strip()


def _tok(t):
    return [x for x in re.split(r"\s+", t.strip()) if x]


def realign(words, toks):
    orig = [w["text"] for w in words]
    sm = difflib.SequenceMatcher(a=orig, b=toks, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for oi in range(i1, i2):
                out.append({**words[oi], "edited": False})
            continue
        ob, cb = words[i1:i2], toks[j1:j2]
        if not cb:
            for oi in range(i1, i2):
                out.append({**words[oi], "edited": False})
            continue
        if not ob:
            tt = out[-1]["end"] if out else 0
            for ct in cb:
                out.append({"text": ct, "start": tt, "end": tt, "edited": True, "orig": ""})
            continue
        s, e = ob[0]["start"], ob[-1]["end"]
        total = sum(max(1, len(x)) for x in cb)
        cur = s
        ot = " ".join(w["text"] for w in ob)
        for ct in cb:
            seg = (e - s) * (max(1, len(ct)) / total)
            out.append({"text": ct, "start": round(cur, 3), "end": round(cur + seg, 3),
                        "edited": (ct != ot), "orig": ot})
            cur += seg
    for i, w in enumerate(out):
        w["id"] = i
        w.setdefault("edited", False)
    return out


def correct(words, language, api_key, glossary="", batch=80,
            base_url=DEFAULT_URL, model=DEFAULT_MODEL):
    out = []
    for b in range(0, len(words), batch):
        chunk = words[b:b + batch]
        src = " ".join(w["text"] for w in chunk)
        try:
            toks = _tok(_call_llm(src, language, api_key, glossary, base_url, model))
            ratio = len(toks) / max(1, len(chunk))
            if not toks or ratio < 0.5 or ratio > 1.8:
                raise ValueError("len")
            out += realign(chunk, toks)
        except Exception:
            out += [{**w, "edited": False} for w in chunk]
    for i, w in enumerate(out):
        w["id"] = i
    return out
