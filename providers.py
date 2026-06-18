"""
providers.py — محرّك التناوب (٥ مزوّدين للتفريغ + ٦ للذكاء).
كلها بصيغة OpenAI. لو نفد رصيد مزوّد (429) أو تعطّل، يسدّ التالي تلقائيًا.
المفاتيح تُقرأ من st.secrets.
"""
import json
import urllib.request
import urllib.error
import streamlit as st


def _secret(k, d=""):
    try:
        return st.secrets.get(k, d)
    except Exception:
        return d


def transcribe_chain():
    """٥ مزوّدي تفريغ. املأ المفاتيح في secrets؛ الفارغ يُتخطّى."""
    acct = _secret("CF_ACCOUNT_ID", "")
    chain = [
        ("Groq", "https://api.groq.com/openai/v1/audio/transcriptions", "whisper-large-v3-turbo", _secret("GROQ_API_KEY") or _secret("LLM_API_KEY")),
        ("Cloudflare", f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/v1/audio/transcriptions", "@cf/openai/whisper-large-v3-turbo", _secret("CF_AI_TOKEN")),
    ]
    return [(n, u, m, k) for (n, u, m, k) in chain if k and "ACCOUNT_ID" not in u and (acct or "cloudflare" not in u.lower())]


def llm_chain():
    """٦ مزوّدي ذكاء."""
    acct = _secret("CF_ACCOUNT_ID", "")
    chain = [
        ("Cerebras", "https://api.cerebras.ai/v1/chat/completions", _secret("CEREBRAS_MODEL", "gpt-oss-120b"), _secret("CEREBRAS_API_KEY")),
        ("Groq", "https://api.groq.com/openai/v1/chat/completions", _secret("GROQ_MODEL", "llama-3.3-70b-versatile"), _secret("GROQ_API_KEY") or _secret("LLM_API_KEY")),
        ("NVIDIA", "https://integrate.api.nvidia.com/v1/chat/completions", "meta/llama-3.3-70b-instruct", _secret("NVIDIA_API_KEY")),
        ("Mistral", "https://api.mistral.ai/v1/chat/completions", "mistral-small-latest", _secret("MISTRAL_API_KEY")),
        ("OpenRouter", "https://openrouter.ai/api/v1/chat/completions", "meta-llama/llama-3.3-70b-instruct:free", _secret("OPENROUTER_API_KEY")),
        ("Cloudflare", f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/v1/chat/completions", "@cf/meta/llama-3.3-70b-instruct-fp8-fast", _secret("CF_AI_TOKEN")),
    ]
    # دعم الإعداد القديم: LLM_BASE_URL/LLM_MODEL كمزوّد أساسي
    legacy_key = _secret("LLM_API_KEY")
    legacy_url = _secret("LLM_BASE_URL")
    if legacy_key and legacy_url and not _secret("CEREBRAS_API_KEY"):
        chain.insert(0, ("Custom", legacy_url, _secret("LLM_MODEL", "qwen-3-32b"), legacy_key))
    return [(n, u, m, k) for (n, u, m, k) in chain if k and "ACCOUNT_ID" not in u]


_RETRY = {429, 401, 403, 500, 502, 503, 504}


def chat(messages, json_mode=False, temperature=0.3, max_tokens=1400):
    """ينادي مزوّدي الذكاء بالتناوب. يرجّع (نص، اسم_المزوّد)."""
    chain = llm_chain()
    if not chain:
        raise RuntimeError("لا يوجد مفتاح ذكاء في secrets (مثل CEREBRAS_API_KEY).")
    last = "no provider"
    for name, url, model, key in chain:
        try:
            body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                         headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
                txt = (data["choices"][0]["message"]["content"] or "").strip()
                if txt:
                    return txt, name
                last = f"{name}: empty"
        except urllib.error.HTTPError as e:
            last = f"{name}: {e.code}"
            if json_mode and e.code in (400, 422):
                # أعد بدون json_mode
                try:
                    body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
                    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
                    with urllib.request.urlopen(req, timeout=120) as r:
                        data = json.loads(r.read())
                        txt = (data["choices"][0]["message"]["content"] or "").strip()
                        if txt:
                            return txt, name
                except Exception as e2:
                    last = f"{name}: {e2}"
            if e.code not in _RETRY:
                continue
        except Exception as e:
            last = f"{name}: {e}"
    raise RuntimeError("فشل الذكاء من كل المزوّدين — " + last)


def transcribe(audio_path, language="ar"):
    """ينادي مزوّدي التفريغ بالتناوب. يرجّع (كلمات_بتوقيت، اسم_المزوّد)."""
    chain = transcribe_chain()
    if not chain:
        raise RuntimeError("لا يوجد مفتاح تفريغ في secrets (مثل GROQ_API_KEY).")
    last = "no provider"
    audio_bytes = open(audio_path, "rb").read()
    for name, url, model, key in chain:
        try:
            data = _multipart_post(url, key, model, language, audio_bytes)
            words = _extract_words(data, language)
            if words:
                return words, name
            last = f"{name}: empty"
        except urllib.error.HTTPError as e:
            last = f"{name}: {e.code}"
            if e.code not in _RETRY:
                continue
        except Exception as e:
            last = f"{name}: {e}"
    raise RuntimeError("فشل التفريغ من كل المزوّدين — " + last)


def _multipart_post(url, key, model, language, audio_bytes):
    if not audio_bytes:
        raise RuntimeError("الملف الصوتي فارغ — تأكّد من نجاح استخراج الصوت.")
    import uuid
    boundary = "----minbar" + uuid.uuid4().hex
    CRLF = b"\r\n"
    pre = b"--" + boundary.encode()
    out = []

    def text_field(name, value):
        out.append(pre + CRLF)
        out.append(f'Content-Disposition: form-data; name="{name}"'.encode() + CRLF + CRLF)
        out.append(str(value).encode() + CRLF)

    text_field("model", model)
    if language:
        text_field("language", language)
    text_field("response_format", "verbose_json")
    text_field("timestamp_granularities[]", "word")

    # حقل الملف
    out.append(pre + CRLF)
    out.append(b'Content-Disposition: form-data; name="file"; filename="audio.wav"' + CRLF)
    out.append(b"Content-Type: audio/wav" + CRLF + CRLF)
    out.append(audio_bytes)
    out.append(CRLF)
    out.append(pre + b"--" + CRLF)

    body = b"".join(out)
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
        "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


FILLERS = {
    "ar": {"يعني","آآ","اه","اها","امم","ايه","إيه","همم","اممم","آه","ها"},
    "en": {"um","uh","erm","hmm","uhh","umm","ahh","mmm"},
    "tr": {"ee","eee","ışey","ııh","hmm"},
}


def _extract_words(data, language):
    fill = FILLERS.get(language, set())
    out = []; gi = [0]
    def push(text, start, end):
        text = (text or "").strip()
        if not text:
            return
        clean = "".join(c for c in text if c not in "،.؟!…,.?!").strip().lower()
        out.append({"id": gi[0], "text": text, "start": round(float(start), 3),
                    "end": round(float(end), 3), "kind": "filler" if clean in fill else "speech"})
        gi[0] += 1
    if isinstance(data.get("words"), list) and data["words"]:
        for w in data["words"]:
            push(w.get("word", w.get("text")), w.get("start", 0), w.get("end", 0))
        return out
    if isinstance(data.get("segments"), list):
        for s in data["segments"]:
            if isinstance(s.get("words"), list) and s["words"]:
                for w in s["words"]:
                    push(w.get("word", w.get("text")), w.get("start", 0), w.get("end", 0))
            else:
                push(s.get("text"), s.get("start", 0), s.get("end", 0))
        if out:
            return out
    if data.get("text"):
        toks = data["text"].split()
        dur = data.get("duration", len(toks) * 0.4)
        step = dur / max(1, len(toks))
        for i, t in enumerate(toks):
            push(t, i * step, (i + 1) * step)
    return out
