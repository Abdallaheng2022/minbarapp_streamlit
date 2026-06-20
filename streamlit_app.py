"""
streamlit_app.py — منبر على Streamlit مع واجهة متعددة اللغات (8 لغات) + RTL/LTR.
المستخدم يختار لغة الواجهة، الموقع يترجم ويعدّل الاتجاه، والاختيار يُحفظ في Supabase.
"""
import os
import json
import tempfile
import subprocess
import sys

import streamlit as st
import pandas as pd

import supa
import editing
import correct_groq
import providers
import smartcut
import payments
import karaoke
from i18n import TR, LANG_NAMES, t

st.set_page_config(page_title="مِنبَر · Minbar", page_icon="🎙️", layout="wide")

FREE_SECONDS = int(st.secrets.get("FREE_SECONDS", 300))
PRICE = st.secrets.get("PRICE", "10")
CHECKOUT_URL = st.secrets.get("CHECKOUT_URL", "")
HF_SPACE = st.secrets.get("HF_SPACE", "")
GROQ_KEY = st.secrets.get("LLM_API_KEY", "") or st.secrets.get("GROQ_API_KEY", "")
LLM_BASE = st.secrets.get("LLM_BASE_URL", "https://api.groq.com/openai/v1/chat/completions")
LLM_MODEL = st.secrets.get("LLM_MODEL", "qwen-3-32b")  # Qwen3 — مفتوح (Apache 2.0)
LECT_LANGS = {"العربية":"ar","English":"en","Türkçe":"tr","हिन्दी":"hi",
              "Español":"es","Deutsch":"de","Français":"fr","Русский":"ru"}

if "ui_lang" not in st.session_state:
    st.session_state.ui_lang = "ar"


def L(key):
    return t(key, st.session_state.ui_lang)


def fmt(s):
    s = max(0, int(s or 0)); return f"{s//60:02d}:{s%60:02d}"


def apply_direction():
    rtl = st.session_state.ui_lang == "ar"
    st.markdown(f"""<style>
    @import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;800&display=swap');
    .stApp {{
        direction: {'rtl' if rtl else 'ltr'};
        text-align: {'right' if rtl else 'left'};
        font-family: 'Tajawal', sans-serif;
        background: #f7f9fc;
        color: #1e293b;
    }}
    h1, h2, h3, h4, .stMarkdown, p, label, .stTabs {{ font-family: 'Tajawal', sans-serif; color:#1e293b; }}
    #MainMenu, footer, header {{ visibility: hidden; }}

    /* الأزرار — أزرق مخضرّ هادئ (هوية مِنبَر) */
    .stButton > button {{
        background: linear-gradient(135deg, #0d9488 0%, #0f766e 100%);
        color: #fff; font-weight: 700; border: none;
        border-radius: 12px; padding: 0.6rem 1.5rem;
        transition: all .2s ease; box-shadow: 0 2px 8px rgba(13,148,136,.25);
    }}
    .stButton > button:hover {{
        transform: translateY(-1px); box-shadow: 0 6px 18px rgba(13,148,136,.35);
        background: linear-gradient(135deg, #0f766e 0%, #115e59 100%);
    }}

    /* الحقول */
    .stTextInput > div > div > input, .stNumberInput input {{
        background:#fff; color:#1e293b; border:1.5px solid #e2e8f0;
        border-radius:10px; padding:.55rem .8rem;
    }}
    .stTextInput > div > div > input:focus {{ border-color:#0d9488; box-shadow:0 0 0 3px rgba(13,148,136,.12); }}

    /* التبويبات */
    .stTabs [data-baseweb="tab-list"] {{ gap:.4rem; }}
    .stTabs [data-baseweb="tab"] {{ border-radius:10px; padding:.4rem 1rem; }}
    .stTabs [aria-selected="true"] {{ background:#e6fffa; color:#0f766e; }}

    /* الشريط الجانبي */
    section[data-testid="stSidebar"] {{ background:#ffffff;
        border-{'left' if rtl else 'right'}:1px solid #e8edf3; }}

    /* بطاقات الميزات */
    .feat {{ background:#fff; border:1px solid #e8edf3; border-radius:16px;
        padding:1.3rem 1rem; text-align:center; transition:.2s; height:100%; }}
    .feat:hover {{ transform:translateY(-3px); box-shadow:0 12px 28px rgba(15,23,42,.08); border-color:#0d9488; }}
    .feat .ic {{ font-size:2rem; display:block; margin-bottom:.5rem; }}
    .feat .t {{ font-weight:700; color:#0f172a; font-size:1.05rem; }}
    .feat .d {{ color:#64748b; font-size:.85rem; margin-top:.2rem; }}

    /* شارات الحالة */
    .badge {{ display:inline-block; padding:.25rem .9rem; border-radius:20px; font-weight:700; font-size:.82rem; }}
    .badge-premium {{ background:#fef3c7; color:#92400e; }}
    .badge-admin {{ background:#ede9fe; color:#5b21b6; }}
    .badge-free {{ background:#f1f5f9; color:#475569; }}
    [data-testid="stMetricValue"] {{ color:#0d9488; font-weight:800; }}
    .stApp [data-testid="stMetric"] {{ background:#fff; border:1px solid #e8edf3; border-radius:14px; padding:.8rem 1rem; }}
    </style>""", unsafe_allow_html=True)


def _on_lang_change(key):
    st.session_state.ui_lang = LANG_NAMES[st.session_state[key]]
    u = st.session_state.get("user")
    if u:
        try: supa.set_ui_language(u["id"], st.session_state.ui_lang)
        except Exception: pass


def language_selector(container, key="_uilang_sel"):
    names = list(LANG_NAMES.keys())
    cur_name = next(n for n, c in LANG_NAMES.items() if c == st.session_state.ui_lang)
    container.selectbox(L("ui_language"), names, index=names.index(cur_name),
                        key=key, on_change=_on_lang_change, args=(key,))


def _proofread(words, language, glossary=""):
    """تصحيح لغوي عبر التناوب (٦ مزوّدين) + محاذاة تحافظ على التوقيت."""
    LN = {"ar":"Arabic","en":"English","tr":"Turkish","hi":"Hindi","es":"Spanish","de":"German","fr":"French","ru":"Russian"}
    name = LN.get(language, "the same")
    gl = f"\nKnown correct terms (keep spelling): {glossary}" if glossary else ""
    sys = (f"You are a professional {name} proofreader. Fix ONLY transcription errors "
           f"(misheard words, homophones, spelling, splits/merges, punctuation). Do NOT rephrase, "
           f"reorder, translate, or change style/dialect. Keep sacred/quoted wording.{gl}\n"
           f"Return ONLY the corrected text.")
    out = list(words)
    B = 80
    for i in range(0, len(words), B):
        batch = words[i:i+B]
        src = " ".join(w["text"] for w in batch)
        try:
            fixed, _ = providers.chat(
                [{"role": "system", "content": sys}, {"role": "user", "content": src}],
                temperature=0, max_tokens=2000)
            toks = correct_groq._tok(fixed)
            aligned = correct_groq.realign(batch, toks)
            out[i:i+B] = aligned
        except Exception:
            pass
    for k, w in enumerate(out):
        w["id"] = k
    return out


def transcribe_via_space(audio_path, language):
    # التفريغ عبر التناوب (٥ مزوّدين مجانيين). أول من ينجح يربح.
    words, prov = providers.transcribe(audio_path, language)
    st.session_state["_asr_provider"] = prov
    return words


def kept_segments_from_words(words, gap=0.8):
    """يبني المقاطع المُبقاة من الكلمات المتبقّية؛ الفجوة الزمنية الكبيرة = مكان قُصّ."""
    if not words:
        return []
    ws = sorted(words, key=lambda w: w["start"])
    segs, cs, ce = [], ws[0]["start"], ws[0]["end"]
    for w in ws[1:]:
        if w["start"] - ce > gap:
            segs.append((round(cs, 3), round(ce, 3)))
            cs = w["start"]
        ce = max(ce, w["end"])
    segs.append((round(cs, 3), round(ce, 3)))
    return segs


def kept_segments(df):
    segs, cur = [], None
    for _, r in df.iterrows():
        if not r[L("col_remove")]:
            cur = [r["start"], r["end"]] if cur is None else [cur[0], r["end"]]
        elif cur:
            segs.append(tuple(cur)); cur = None
    if cur: segs.append(tuple(cur))
    return segs


def _download_url(url, tmp, premium):
    import importlib.util
    if importlib.util.find_spec("yt_dlp") is None:
        raise RuntimeError("yt-dlp غير مثبّت. نفّذ: pip install yt-dlp")
    out = os.path.join(tmp, "src.%(ext)s")
    cmd = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--remux-video", "mp4", "-f", "bv*+ba/b", "-o", out]
    if not premium:
        cmd += ["--download-sections", "*0-300"]
    cmd += [url]
    subprocess.run(cmd, check=True, timeout=900)
    import glob
    files = glob.glob(os.path.join(tmp, "src.*"))
    if not files:
        raise RuntimeError("تعذّر تنزيل الفيديو من الرابط")
    return files[0]


def auth_screen():
    if "ui_lang" not in st.session_state:
        st.session_state.ui_lang = "ar"
    apply_direction()
    top = st.columns([3, 1])
    language_selector(top[1], key="_uilang_auth")

    st.markdown("""
    <div style="text-align:center; padding:2.2rem 0 1rem;">
        <div style="font-size:3.6rem;">🎙️</div>
        <h1 style="font-size:3rem; margin:.3rem 0; font-weight:800;
            background:linear-gradient(135deg,#0d9488,#14b8a6);
            -webkit-background-clip:text; -webkit-text-fill-color:transparent;">مِنبَر</h1>
        <p style="font-size:1.35rem; color:#0f172a; font-weight:700;">
            حوّل محاضراتك إلى محتوى احترافي… بالكلمة لا بالمونتاج</p>
        <p style="font-size:1.05rem; color:#64748b; max-width:560px; margin:.5rem auto;">
            فرّغ، صحّح، واقصص فيديوهاتك بمجرد وصف ما تريد — يفهمك الذكاء الاصطناعي وينفّذ.</p>
    </div>""", unsafe_allow_html=True)

    fc = st.columns(3)
    cards = [("⚡", "تفريغ فوري دقيق", "نص كامل بتوقيت كل كلمة"),
             ("✂️", "قصّ ذكي بالوصف", "اكتب ما تريد حذفه فيُنفَّذ"),
             ("🌍", "٨ لغات بالكامل", "واجهة عربية وعالمية")]
    for col, (ic, t, d) in zip(fc, cards):
        col.markdown(f"<div class='feat'><span class='ic'>{ic}</span><div class='t'>{t}</div><div class='d'>{d}</div></div>", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        tab1, tab2 = st.tabs(["🔑 " + L("tab_login"), "✨ " + L("tab_signup")])
        with tab1:
            e = st.text_input(L("email"), key="li_e")
            p = st.text_input(L("password"), type="password", key="li_p")
            if st.button(L("signin"), key="li_b", use_container_width=True):
                try:
                    r = supa.sign_in(e, p)
                    st.session_state.user = {"id": r.user.id, "email": r.user.email}
                    prof = supa.get_profile(r.user.id) or {}
                    if prof.get("ui_language"):
                        st.session_state.ui_lang = prof["ui_language"]
                    st.rerun()
                except Exception as ex:
                    st.error(str(ex))
        with tab2:
            e2 = st.text_input(L("email"), key="su_e")
            p2 = st.text_input(L("password6"), type="password", key="su_p")
            if st.button(L("signup"), key="su_b", use_container_width=True):
                try:
                    supa.sign_up(e2, p2); st.success(L("signup_done"))
                except Exception as ex:
                    st.error(str(ex))


def sidebar(profile, premium):
    with st.sidebar:
        language_selector(st, key="_uilang_side")
        st.write(f"👤 {st.session_state.user['email']}")
        status = L("st_admin") if profile.get("role") == "admin" else (L("st_premium") if premium else L("st_free"))
        st.write(f"{L('status')}: {status}")
        if not premium and profile.get("role") != "admin":
            if CHECKOUT_URL:
                st.link_button(f"{L('subscribe')} — {PRICE}$", CHECKOUT_URL)
            else:
                st.info(L("subscribe_note"))
            # استبدال رمز التفعيل
            with st.expander("🎟️ " + L("redeem_title")):
                code = st.text_input(L("redeem_ph"), key="redeem_code")
                if st.button(L("redeem_btn")):
                    ok, msg = payments.redeem(st.session_state.user["id"], code)
                    (st.success if ok else st.error)(msg)
                    if ok:
                        st.rerun()
        if st.button(L("logout")):
            keep = st.session_state.ui_lang
            st.session_state.clear(); st.session_state.ui_lang = keep; st.rerun()
        if profile.get("role") == "admin":
            st.divider()
            st.markdown("<div class='badge badge-admin'>👑 لوحة التحكّم</div>", unsafe_allow_html=True)
            users = supa.list_users()
            mc = st.columns(2)
            mc[0].metric("👥 المستخدمون", len(users))
            mc[1].metric("⭐ المشتركون", sum(1 for u in users if supa.is_premium(u)))

            with st.expander("👥 إدارة المستخدمين"):
                for u in users:
                    c = st.columns([3, 1])
                    bd = "admin" if u.get("role") == "admin" else ("premium" if supa.is_premium(u) else "free")
                    c[0].markdown(f"<span class='badge badge-{bd}'>●</span> {u.get('email') or u['id'][:8]}", unsafe_allow_html=True)
                    if u.get("role") != "admin" and c[1].button(L("activate"), key="a"+u["id"]):
                        supa.set_premium(u["id"], 365); st.rerun()

            with st.expander("🎟️ " + L("codes_title")):
                cc = st.columns(2)
                n = cc[0].number_input(L("codes_count"), 1, 100, 1)
                days = cc[1].number_input(L("codes_days"), 1, 3650, 365)
                if st.button(L("codes_gen")):
                    new = payments.generate_codes(int(n), int(days))
                    st.success(" · ".join(new))
                rows = payments.list_codes(50)
                if rows:
                    st.caption(L("codes_list"))
                    for r in rows[:20]:
                        used = "✅" if r.get("used_by") else "🟡"
                        st.markdown(f"{used} `{r['code']}` · {r['days']}d")


def editor(profile, premium):
    st.title("مِنبَر · Minbar")
    language = LECT_LANGS[st.selectbox(L("lecture_language"), list(LECT_LANGS.keys()))]
    up = st.file_uploader(L("uploader"), type=["mp4","mov","mkv","m4a","mp3","wav"])
    st.session_state["gloss"] = st.text_input(L("gloss_label"), value=st.session_state.get("gloss",""))
    url = st.text_input(L("url_ph"))
    if url and st.button(L("url_btn")):
        tmp = tempfile.mkdtemp()
        try:
            with st.spinner(L("downloading")):
                vpath = _download_url(url, tmp, premium)
        except Exception as ex:
            st.error(str(ex)); st.stop()
        audio = os.path.join(tmp, "a.wav")
        cmd = ["ffmpeg","-y","-i",vpath,"-ac","1","-ar","16000","-vn"]
        if not premium: cmd += ["-t", str(FREE_SECONDS)]
        cmd += [audio,"-loglevel","error"]; subprocess.run(cmd, check=True)
        with st.spinner(L("transcribing")):
            words = transcribe_via_space(audio, language)
        st.session_state.update(words=words, vpath=vpath, language=language,
                                duration=editing.get_duration(vpath), limited=not premium)
        st.session_state.video_bytes = open(vpath, "rb").read()
        st.rerun()

    if up and st.button(L("start")):
        tmp = tempfile.mkdtemp(); vpath = os.path.join(tmp, up.name)
        open(vpath, "wb").write(up.read())
        limited = not premium; audio = os.path.join(tmp, "a.wav")
        cmd = ["ffmpeg","-y","-i",vpath,"-ac","1","-ar","16000","-vn"]
        if limited: cmd += ["-t", str(FREE_SECONDS)]
        cmd += [audio,"-loglevel","error"]; subprocess.run(cmd, check=True)
        with st.spinner(L("transcribing")):
            try:
                words = transcribe_via_space(audio, language)
            except Exception as ex:
                st.error(str(ex)); return
        st.session_state.update(words=words, vpath=vpath, language=language,
                                duration=editing.get_duration(vpath), limited=limited)
        st.session_state.video_bytes = open(vpath, "rb").read()
        try:
            proj = supa.create_project(st.session_state.user["id"], up.name, language,
                                       st.session_state.duration, limited, words)
            st.session_state.project_id = proj["id"]
        except Exception:
            st.session_state.project_id = None
        st.rerun()

    if "words" not in st.session_state:
        st.info(L("info_upload")); return
    if st.session_state.get("limited"):
        st.warning(L("limited_warn").format(m=FREE_SECONDS // 60))

    c1, c2 = st.columns(2)
    with c1:
        sync_on = st.checkbox("🎬 النص المتزامن (للفيديوهات الصغيرة فقط)", value=False, key="sync_on")
        ok = False
        if sync_on:
            ok = karaoke.render(st.session_state.vpath, st.session_state.words, height=460)
            if not ok:
                st.caption("ℹ️ الفيديو كبير على المشغّل المتزامن — يُعرض عاديًا.")
        if not ok:
            st.video(st.session_state.get("video_bytes") or st.session_state.vpath)
        if providers.llm_chain() and st.button(L("proofread")):
            with st.spinner("…"):
                try:
                    st.session_state.words = _proofread(
                        st.session_state.words, st.session_state.language,
                        st.session_state.get("gloss", ""))
                    if st.session_state.get("project_id"):
                        supa.update_transcript(st.session_state.project_id, st.session_state.words)
                except Exception as ex:
                    st.error(str(ex))
            st.rerun()
    with c2:
        st.subheader(L("edit_title"))
        full_text = " ".join(w["text"] for w in st.session_state.words)
        tab_tbl, tab_txt = st.tabs(["📝 " + L("edit_title"), "📄 النص الكامل"])
        with tab_txt:
            st.caption("اقرأ النص كاملًا أو عدّله مباشرة ثم احفظ.")
            new_txt = st.text_area("النص", value=full_text, height=300,
                                   label_visibility="collapsed", key="full_txt")
            if st.button("💾 احفظ تعديل النص"):
                toks = new_txt.split()
                old = st.session_state.words
                if toks:
                    t0 = old[0]["start"] if old else 0.0
                    t1 = old[-1]["end"] if old else len(toks) * 0.4
                    step = (t1 - t0) / max(1, len(toks))
                    st.session_state.words = [{"id": i, "text": tk,
                                               "start": round(t0 + i * step, 3),
                                               "end": round(t0 + (i + 1) * step, 3),
                                               "kind": "speech"} for i, tk in enumerate(toks)]
                    if st.session_state.get("project_id"):
                        supa.update_transcript(st.session_state.project_id, st.session_state.words)
                    st.rerun()
        with tab_tbl:
            st.caption("النص للقراءة. للحذف استخدم: القصّ الذكي بالوصف، أو القصّ بالتوقيت، أو حرّر النص الكامل.")
            # عرض خفيف للكلمات المحذوفة حاليًا (إن وُجدت)
            rem_now = set(st.session_state.get("_applied_removed", []))
            chips = " ".join(
                (f"<span style='background:#fee2e2;color:#991b1b;padding:1px 5px;border-radius:5px;text-decoration:line-through'>{w['text']}</span>"
                 if w["id"] in rem_now else w["text"]) for w in st.session_state.words[:600])
            st.markdown(f"<div style='max-height:300px;overflow:auto;background:#fff;border:1px solid #e8edf3;"
                        f"border-radius:10px;padding:12px;line-height:2.1;font-size:1.05rem'>{chips}</div>",
                        unsafe_allow_html=True)

    # المقاطع المُبقاة تُحسب من الكلمات المتبقّية (الفجوات = أماكن القصّ)
    segs = kept_segments_from_words(st.session_state.words)
    kept = sum(e - s for s, e in segs)
    m = st.columns(3)
    m[0].metric(L("m_orig"), fmt(st.session_state.duration))
    m[1].metric(L("m_after"), fmt(kept)); m[2].metric(L("m_cuts"), len(segs))

    # ── القَصّ الذكي بالوصف ──
    st.divider(); st.subheader("✂️ " + L("smartcut_title"))
    mode_tab = st.radio("طريقة القصّ", ["🤖 بالوصف (ذكي)", "⏱️ بالتوقيت (دقّة 100%)"],
                        horizontal=True, label_visibility="collapsed")
    if mode_tab.startswith("⏱️"):
        st.caption("اكتب النطاقات الزمنية مثل: من 2:30 إلى 4:00 — أو: 1:05-1:40، 5:00-6:10")
        tcol = st.columns([4, 1])
        tin = tcol[0].text_input("النطاقات", key="time_in", label_visibility="collapsed",
                                 placeholder="من 2:30 إلى 4:00")
        if tcol[1].button("⏱️ اقطع") and tin.strip():
            ranges = smartcut.parse_time_ranges(tin)
            if not ranges:
                st.error("لم أفهم التوقيت. اكتب مثل: من 2:30 إلى 4:00")
            else:
                res = smartcut.from_time_ranges(st.session_state.words, ranges)
                st.session_state["_smart_removed"] = list(res["removed_ids"])
                st.session_state["_smart_reason"] = res["reason"]
                st.session_state["_smart_prov"] = "manual"
                st.session_state["_smart_times"] = res["time_ranges"]
                st.session_state["_smart_ranges"] = res.get("ranges", [])
                st.session_state["_smart_mode"] = "manual"
                st.session_state["_smart_nseg"] = res["segments_found"]
                st.rerun()
    else:
        sc = st.columns([4, 1])
        instr = sc[0].text_input(L("smartcut_ph"), key="smartcut_in", label_visibility="collapsed",
                                 placeholder=L("smartcut_ph"))
        hi_acc = st.checkbox("🎯 دقّة أعلى (أبطأ قليلًا — يحسّن فهم نيّتك)", value=False, key="hi_acc")
        if sc[1].button(L("smartcut_btn")) and instr.strip():
            prog = st.progress(0, text="🧠 أحدّد المقاطع…")
            try:
                if hi_acc:
                    prog.progress(30, text="🔍 أحلّل نيّتك بدقّة…")
                res = smartcut.plan(st.session_state.words, instr.strip(), refine=hi_acc)
                prog.progress(90, text="✓ أطابق بالتوقيت الدقيق…")
                st.session_state["_smart_removed"] = list(res["removed_ids"])
                st.session_state["_smart_reason"] = res.get("reason", "")
                st.session_state["_smart_prov"] = res.get("provider", "")
                st.session_state["_smart_times"] = res.get("time_ranges", [])
                st.session_state["_smart_ranges"] = res.get("ranges", [])
                st.session_state["_smart_mode"] = res.get("mode", "cut")
                st.session_state["_smart_nseg"] = res.get("segments_found", 0)
                st.session_state["_smart_refined"] = res.get("refined", "")
                prog.progress(100, text="تم ✓")
            except Exception as ex:
                st.error(str(ex))
            finally:
                prog.empty()
    if st.session_state.get("_smart_removed"):
        rem = set(st.session_state["_smart_removed"])
        # معاينة خفيفة: نعرض المقاطع المحذوفة فقط (لا كامل النص) لتفادي ثقل الرسم
        wb = {w["id"]: w["text"] for w in st.session_state.words}
        preview = "، ".join("…".join(wb[i] for i in range(s, e + 1) if i in wb)
                            for s, e in (st.session_state.get("_smart_ranges") or []))
        if not preview:
            preview = " ".join(wb[i] for i in sorted(rem) if i in wb)[:1000]
        st.caption(L("smartcut_preview").format(n=len(rem), prov=st.session_state.get("_smart_prov", "")))
        if st.session_state.get("_smart_reason"):
            st.caption("💡 " + st.session_state["_smart_reason"])
        _mode = st.session_state.get("_smart_mode", "cut")
        _nseg = st.session_state.get("_smart_nseg", 0)
        if _mode == "extract" and _nseg:
            st.info(f"🎯 وضع الاستخراج: عُثر على {_nseg} مقطعًا مطابقًا — سيُبقى المطابق ويُحذف الباقي.")
        elif _nseg > 1:
            st.info(f"✂️ عُثر على {_nseg} مواضع مطابقة للحذف.")
        # عرض النطاقات الزمنية الدقيقة التي ستُقصّ
        tr = st.session_state.get("_smart_times") or []
        if tr:
            st.markdown("**المقاطع التي ستُحذف (بالتوقيت الدقيق):**")
            for seg in tr:
                dur = seg["end"] - seg["start"]
                st.markdown(f"<div style='background:#fef2f2;border:1px solid #fecaca;border-radius:8px;"
                            f"padding:6px 12px;margin:3px 0;color:#991b1b'>✂️ {seg['label']} "
                            f"<span style='color:#64748b'>({dur:.1f}s)</span></div>", unsafe_allow_html=True)
        st.markdown(f"<div style='max-height:160px;overflow:auto;background:#f0fdfa;padding:12px;border-radius:10px;border:1px solid #ccfbf1;line-height:2'>{preview}</div>", unsafe_allow_html=True)
        ca = st.columns(2)
        if ca[0].button("✓ " + L("smartcut_apply")):
            st.session_state.words = [w for w in st.session_state.words if w["id"] not in rem]
            for i, w in enumerate(st.session_state.words):
                w["id"] = i
            st.session_state.pop("_smart_removed", None)
            if st.session_state.get("project_id"):
                supa.update_transcript(st.session_state.project_id, st.session_state.words)
            st.rerun()
        if ca[1].button("✕ " + L("cancel")):
            st.session_state.pop("_smart_removed", None); st.rerun()


    st.download_button(L("dl_txt"), " ".join(w["text"] for w in st.session_state.words),
                       file_name="transcript.txt")
    if st.button(L("export")) and segs:
        out = os.path.join(tempfile.mkdtemp(), "edit.mp4")
        with st.spinner("FFmpeg…"): editing.export_segments(st.session_state.vpath, segs, out)
        st.session_state.export_path = out
    if st.session_state.get("export_path"):
        st.download_button(L("dl_export"), open(st.session_state.export_path, "rb"), file_name="minbar_edit.mp4")

    st.divider(); st.subheader(L("multi_title"))
    cc = st.columns(3)
    cs = cc[0].number_input(L("c_start"), 0.0, float(st.session_state.duration), 0.0, 0.5)
    ce = cc[1].number_input(L("c_end"), 0.0, float(st.session_state.duration), 5.0, 0.5)
    if cc[2].button(L("c_cut")):
        if ce > cs:
            out = os.path.join(tempfile.mkdtemp(), "clip.mp4")
            with st.spinner("…"): editing.export_clip(st.session_state.vpath, cs, ce, out)
            st.download_button(f"⬇ {fmt(cs)}–{fmt(ce)}", open(out, "rb"),
                               file_name=f"clip_{int(cs)}_{int(ce)}.mp4", key=f"d{cs}{ce}")
        else:
            st.error(L("err_end"))


def main():
    if "user" not in st.session_state:
        auth_screen(); return
    apply_direction()
    try: profile = supa.get_profile(st.session_state.user["id"]) or {}
    except Exception: profile = {}
    premium = supa.is_premium(profile)
    sidebar(profile, premium)
    editor(profile, premium)


if __name__ == "__main__":
    main()
