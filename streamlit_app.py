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
    st.markdown(
        f"<style>.stApp{{direction:{'rtl' if rtl else 'ltr'};"
        f"text-align:{'right' if rtl else 'left'}}}</style>",
        unsafe_allow_html=True)


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
    top = st.columns([3, 1])
    top[0].title("مِنبَر · Minbar")
    top[0].caption(L("tagline"))
    language_selector(top[1], key="_uilang_auth")
    apply_direction()

    tab1, tab2 = st.tabs([L("tab_login"), L("tab_signup")])
    with tab1:
        e = st.text_input(L("email"), key="li_e")
        p = st.text_input(L("password"), type="password", key="li_p")
        if st.button(L("signin"), key="li_b"):
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
        if st.button(L("signup"), key="su_b"):
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
            st.divider(); st.subheader(L("users"))
            for u in supa.list_users():
                c = st.columns([3, 1])
                c[0].write(u.get("email") or u["id"][:8])
                if u.get("role") != "admin" and c[1].button(L("activate"), key="a"+u["id"]):
                    supa.set_premium(u["id"], 365); st.rerun()
            # توليد رموز التفعيل
            with st.expander("🎟️ " + L("codes_title")):
                cc = st.columns(2)
                n = cc[0].number_input(L("codes_count"), 1, 100, 1)
                days = cc[1].number_input(L("codes_days"), 1, 3650, 365)
                if st.button(L("codes_gen")):
                    new = payments.generate_codes(int(n), int(days))
                    st.success(" / ".join(new))
                rows = payments.list_codes(50)
                if rows:
                    st.caption(L("codes_list"))
                    for r in rows[:20]:
                        used = "✅" if r.get("used_by") else "⬜"
                        st.text(f"{used} {r['code']} · {r['days']}d")


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
        st.video(st.session_state.vpath)
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
        df = pd.DataFrame([{L("col_remove"): False, L("col_text"): w["text"],
                            "start": w["start"], "end": w["end"]} for w in st.session_state.words])
        edited = st.data_editor(df, use_container_width=True, height=360, hide_index=True,
                                column_config={"start": st.column_config.NumberColumn(format="%.2f", disabled=True),
                                               "end": st.column_config.NumberColumn(format="%.2f", disabled=True)})
        st.session_state.words = [{"id": i, "text": r[L("col_text")], "start": r["start"], "end": r["end"]}
                                  for i, r in edited.iterrows()]

    segs = kept_segments(edited); kept = sum(e - s for s, e in segs)
    m = st.columns(3)
    m[0].metric(L("m_orig"), fmt(st.session_state.duration))
    m[1].metric(L("m_after"), fmt(kept)); m[2].metric(L("m_cuts"), len(segs))

    # ── القَصّ الذكي بالوصف ──
    st.divider(); st.subheader("✂️ " + L("smartcut_title"))
    sc = st.columns([4, 1])
    instr = sc[0].text_input(L("smartcut_ph"), key="smartcut_in", label_visibility="collapsed",
                             placeholder=L("smartcut_ph"))
    if sc[1].button(L("smartcut_btn")) and instr.strip():
        with st.spinner("✂️ …"):
            try:
                res = smartcut.plan(st.session_state.words, instr.strip())
                st.session_state["_smart_removed"] = list(res["removed_ids"])
                st.session_state["_smart_reason"] = res.get("reason", "")
                st.session_state["_smart_prov"] = res.get("provider", "")
            except Exception as ex:
                st.error(str(ex))
    if st.session_state.get("_smart_removed"):
        rem = set(st.session_state["_smart_removed"])
        preview = " ".join(("❌" + w["text"]) if w["id"] in rem else w["text"] for w in st.session_state.words)
        st.caption(L("smartcut_preview").format(n=len(rem), prov=st.session_state.get("_smart_prov", "")))
        if st.session_state.get("_smart_reason"):
            st.caption("💡 " + st.session_state["_smart_reason"])
        st.markdown(f"<div style='max-height:160px;overflow:auto;background:#fff7e6;padding:10px;border-radius:8px'>{preview}</div>", unsafe_allow_html=True)
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
