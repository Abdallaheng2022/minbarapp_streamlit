"""
smartcut.py — القَصّ الذكي بالوصف (Streamlit) — نسخة دقيقة.
وصف نصّي بالعربي → برومت دقيق يوجّه النموذج → نطاقات كلمات للحذف
→ تُطابَق بتوقيت الكلمات → نطاقات زمنية دقيقة (من ث.ث إلى ث.ث) لقصّ الفيديو.
يعمل عبر محرّك التناوب (providers.chat).
"""
import json
import providers

SYS = """You are MINBAR-CUT, a production-grade editing engine for spoken-word lectures (mostly Arabic religious/educational talks). You convert a natural-language Arabic instruction into EXACT word-id spans. Precision is mandatory: a wrong boundary ruins the exported video.

== INPUT ==
A numbered transcript. Every token is `id:word`, ids strictly ascending from 0. You also get the chunk's time span for reference.

== TWO MODES (decide from the instruction) ==
1) CUT mode (default): the user names what to REMOVE. Examples: "احذف"، "شيل"، "امسح"، "اقطع"، "إلغِ".
   -> remove = the spans the user named; everything else is kept.
2) EXTRACT/KEEP mode: the user names what to KEEP/EXTRACT and discard the rest.
   Examples: "استخرج"، "طلّع"، "اعرض فقط"، "ابقِ فقط"، "عايز بس"، "احتفظ بـ".
   -> first find the spans the user WANTS (the "keep" spans), then return remove = EVERYTHING that is NOT a keep span.

== MULTIPLICITY (critical) ==
The instruction may match MANY places, not one. Scan the WHOLE transcript and return EVERY matching span.
- "كل مرة يذكر فيها X" / "كل المواضع" / "أينما" -> return ALL occurrences, each as its own span.
- "أول مرة" -> only the first. "آخر مرة" -> only the last. "أهم مقطع" -> the single best match.
- If the user asks to EXTRACT a topic that appears in several separated places, return each occurrence as a separate keep span (so the export stitches them).

== HOW TO MATCH (by meaning, not keywords) ==
- "المقدمة/الافتتاحية" = greetings, basmala-then-greeting, self-intro, thanks (keep the basmala itself unless told otherwise).
- "الخاتمة" = closing dua, farewell, "والسلام عليكم".
- "الاستطراد/الكلام الجانبي" = digressions, audience interaction, off-topic asides.
- "الحشو/التكرار/التلعثم" = fillers, false starts, repeated phrases.
- "من دقيقة A إلى دقيقة B" = every word whose [start,end] falls inside that time window (use the provided timings).
- A topic/keyword (e.g. "الكلام عن الصبر") = the full contiguous passage discussing it, from its first relevant word to its last.

== BOUNDARY PRECISION (100%) ==
- start_id = the FIRST word that truly belongs to the target span. end_id = the LAST such word.
- Prefer natural sentence boundaries (after . ، ؟ !) UNLESS a time range is specified.
- When unsure whether an edge word belongs, EXCLUDE it (never over-cut).
- Spans must be non-overlapping, ascending, and use ONLY existing ids. Never invent ids or text.

== SAFETY ==
Never remove Quran, hadith, or quoted sacred text unless the user EXPLICITLY instructs it.

== OUTPUT (strict JSON only, no markdown) ==
{"mode":"cut|extract","remove":[[start_id,end_id],...],"segments_found":<int>,"reason":"<short Arabic>"}
- In CUT mode: "remove" = spans to delete.
- In EXTRACT mode: compute keep spans internally, then output "remove" = all NON-kept spans (the engine keeps the rest). Set segments_found = number of keep spans you found.
- If nothing matches: {"mode":"cut","remove":[],"segments_found":0,"reason":"لا يوجد ما يطابق الوصف"}.

== FEW-SHOT ==
Transcript: 0:بسم 1:الله 2:السلام 3:عليكم 4:معكم 5:الشيخ 6:اليوم 7:نتكلم 8:عن 9:الصبر 10:ثم 11:عن 12:الصلاة 13:وأيضا 14:الصبر 15:مهم
- "احذف المقدمة" -> {"mode":"cut","remove":[[2,5]],"segments_found":1,"reason":"حذف التحية والتعريف"}
- "استخرج الكلام عن الصبر" -> keep spans [6,9] and [13,14]; remove the rest:
  {"mode":"extract","remove":[[0,5],[10,12],[15,15]],"segments_found":2,"reason":"استخراج موضعَي الحديث عن الصبر"}
- "كل مرة تُذكر الصلاة" (cut) -> {"mode":"cut","remove":[[11,12]],"segments_found":1,"reason":"حذف ذكر الصلاة"}
"""


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


def _fmt_ts(sec):
    sec = max(0, float(sec))
    m = int(sec // 60)
    s = sec - m * 60
    return f"{m}:{s:04.1f}"


REFINE_SYS = """You are an intent-refiner for a video-editing tool. The user writes a casual Arabic instruction about what to cut/extract from a lecture transcript. Rewrite it into a SINGLE precise, unambiguous English directive for a downstream extraction model. Resolve vague wording, detect whether they mean CUT or EXTRACT/KEEP, and whether they want ALL occurrences or a specific one. Do NOT add anything the user didn't intend. Output ONLY the refined directive, one line, no quotes."""


def refine_intent(instruction):
    """الطبقة ١: نموذج سريع يحسّن نية المستخدم لتوجيه دقيق."""
    try:
        txt, _ = providers.chat_fast(
            [{"role": "system", "content": REFINE_SYS},
             {"role": "user", "content": instruction}],
            temperature=0.1, max_tokens=200)
        txt = (txt or "").strip().strip('"').strip()
        return txt or instruction
    except Exception:
        return instruction  # لو فشل، نستخدم الأصلي (لا نتعطّل)


def plan(words, instruction, gap_merge=0.6, refine=False):
    if not words:
        return {"removed_ids": set(), "ranges": [], "time_ranges": [], "reason": "", "provider": "",
                "mode": "cut", "segments_found": 0, "refined": ""}
    id2 = {w["id"]: w for w in words}

    # الطبقة ١: تحسين النية (سريع)
    refined = refine_intent(instruction) if refine else instruction

    chunks, cur, size = [], [], 0
    for w in words:
        tok = f'{w["id"]}:{w["text"]} '
        if size + len(tok) > 6000 and cur:
            chunks.append(cur); cur, size = [], 0
        cur.append(w); size += len(tok)
    if cur:
        chunks.append(cur)

    all_remove, reasons, provider = [], [], ""
    mode, seg_found = "cut", 0
    for ch in chunks:
        numbered = " ".join(f'{w["id"]}:{w["text"]}' for w in ch)
        t0, t1 = ch[0]["start"], ch[-1]["end"]
        user = (f"USER INSTRUCTION (Arabic, original): {instruction}\n"
                f"REFINED DIRECTIVE (English, authoritative): {refined}\n\n"
                f"This chunk spans {_fmt_ts(t0)} to {_fmt_ts(t1)}.\n"
                f"TRANSCRIPT (id:word):\n{numbered}\n\nReturn the JSON now.")
        txt, provider = providers.chat(
            [{"role": "system", "content": SYS},
             {"role": "user", "content": user}],
            json_mode=True, temperature=0, max_tokens=1600)
        o = _parse(txt)
        if o and isinstance(o.get("remove"), list):
            for pair in o["remove"]:
                if isinstance(pair, list) and len(pair) == 2:
                    try:
                        all_remove.append((int(pair[0]), int(pair[1])))
                    except (ValueError, TypeError):
                        pass
        if o and o.get("reason"):
            reasons.append(str(o["reason"]))
        if o and o.get("mode"):
            mode = str(o["mode"])
        if o and isinstance(o.get("segments_found"), int):
            seg_found += o["segments_found"]

    valid = sorted((min(s, e), max(s, e)) for s, e in all_remove if s in id2 and e in id2)
    merged_ids = []
    for s, e in valid:
        if merged_ids and s <= merged_ids[-1][1] + 1:
            merged_ids[-1] = (merged_ids[-1][0], max(merged_ids[-1][1], e))
        else:
            merged_ids.append((s, e))

    removed = set()
    for s, e in merged_ids:
        removed.update(range(s, e + 1))

    raw_times = []
    for s, e in merged_ids:
        seg_start = id2[s]["start"]; seg_end = id2[e]["end"]
        if seg_end > seg_start:
            raw_times.append([seg_start, seg_end])

    raw_times.sort()
    time_ranges = []
    for st_, en_ in raw_times:
        if time_ranges and st_ - time_ranges[-1]["end"] <= gap_merge:
            time_ranges[-1]["end"] = max(time_ranges[-1]["end"], en_)
            time_ranges[-1]["label"] = f'{_fmt_ts(time_ranges[-1]["start"])} -> {_fmt_ts(time_ranges[-1]["end"])}'
        else:
            time_ranges.append({"start": round(st_, 2), "end": round(en_, 2),
                                "label": f"{_fmt_ts(st_)} -> {_fmt_ts(en_)}"})

    return {"removed_ids": removed, "ranges": merged_ids, "time_ranges": time_ranges,
            "mode": mode, "segments_found": seg_found, "refined": refined,
            "reason": " · ".join(reasons)[:300], "provider": provider}


def _parse_time(tok):
    """يحوّل '2:30' أو '90' أو '1:05.5' إلى ثوانٍ."""
    tok = tok.strip().replace("،", ":").replace("٫", ".")
    if ":" in tok:
        parts = tok.split(":")
        try:
            parts = [float(p) for p in parts]
        except ValueError:
            return None
        sec = 0.0
        for p in parts:
            sec = sec * 60 + p
        return sec
    try:
        return float(tok)
    except ValueError:
        return None


def parse_time_ranges(text):
    """يستخرج نطاقات زمنية من نص حرّ مثل 'من 2:30 إلى 4:00, و 5:00-6:10'."""
    import re
    out = []
    # ابحث عن أزواج رقمية مفصولة بـ إلى / - / to / حتى
    pat = re.compile(r'(\d{1,3}(?::\d{1,2}(?:\.\d+)?)?)\s*(?:إلى|الى|-|–|—|to|حتى|→)\s*(\d{1,3}(?::\d{1,2}(?:\.\d+)?)?)')
    for m in pat.finditer(text):
        a, b = _parse_time(m.group(1)), _parse_time(m.group(2))
        if a is not None and b is not None and b > a:
            out.append((a, b))
    return out


def from_time_ranges(words, ranges):
    """قصّ يدوي دقيق: يحذف كل كلمة يقع توقيتها داخل النطاقات المعطاة (بالثواني)."""
    removed = set()
    for w in words:
        for s, e in ranges:
            if w["end"] > s and w["start"] < e:  # تداخل
                removed.add(w["id"])
                break
    time_ranges = [{"start": round(s, 2), "end": round(e, 2),
                    "label": f"{_fmt_ts(s)} -> {_fmt_ts(e)}"} for s, e in sorted(ranges)]
    return {"removed_ids": removed, "ranges": [], "time_ranges": time_ranges,
            "mode": "manual", "segments_found": len(ranges),
            "reason": "قصّ يدوي بالتوقيت الدقيق", "provider": "manual"}
