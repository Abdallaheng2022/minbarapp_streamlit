"""
smartcut.py — القَصّ الذكي بالوصف (Streamlit) — نسخة دقيقة.
وصف نصّي بالعربي → برومت دقيق يوجّه النموذج → نطاقات كلمات للحذف
→ تُطابَق بتوقيت الكلمات → نطاقات زمنية دقيقة (من ث.ث إلى ث.ث) لقصّ الفيديو.
يعمل عبر محرّك التناوب (providers.chat).
"""
import json
import providers

SYS = """You are a meticulous video-editing assistant for spoken-word lectures (often Arabic religious/educational talks).

INPUT: a numbered transcript where every token is `id:word` with ids in strict ascending order.
The user gives an instruction in Arabic describing exactly which parts to CUT (remove) from the video.

YOUR JOB — follow these steps precisely:
1. Read the FULL transcript and understand its flow (intro -> body -> closing).
2. Identify the EXACT contiguous spans the instruction refers to. Match by MEANING, not keywords:
   - "almuqaddima" = opening greetings, basmala, self-introduction, thanks.
   - "alkhatima" = closing duas, farewells, "wassalamu alaykum".
   - "alkalam aljanibi" / asides = digressions, off-topic, audience interaction.
   - "alhashw" / fillers = filler words, repeated phrases, false starts, stutters.
   - "min daqiqa kaza ila daqiqa kaza" = a time range -> include every word whose timing falls in that range.
3. For each span to remove, return the [start_id, end_id] of the FIRST and LAST word of that span.
   - Be precise at boundaries: do NOT include a word that belongs to content the user wants to keep.
   - Prefer cutting at natural sentence boundaries (after punctuation) unless a time range is given.
4. NEVER remove Quran, hadith, or quoted sacred text unless the user EXPLICITLY says to.
5. If the instruction is unclear or matches nothing, return an empty remove list.

OUTPUT: return ONLY strict JSON, no markdown, no commentary:
{"remove": [[start_id, end_id], ...], "reason": "<short Arabic explanation>"}
Rules: ids MUST exist; ranges non-overlapping and ascending; never invent ids.

EXAMPLES (illustrative):
- Instruction "احذف المقدمة": transcript starts `0:بسم 1:الله 2:السلام 3:عليكم 4:معكم 5:الشيخ 6:اليوم 7:نتحدث ...`
  -> {"remove": [[2,5]], "reason": "حذف التحية والتعريف بالنفس مع إبقاء البسملة وبداية الموضوع"}
- Instruction "اقطع من الدقيقة 1 إلى 1:30": include only words whose timing is within 60s-90s.
  -> {"remove": [[<first id at 60s>, <last id before 90s>]], "reason": "حذف المقطع الزمني المطلوب"}
- Instruction "شيل الحشو والتكرار": remove only filler/stutter spans, keep all meaningful content.
Be conservative: when unsure whether a boundary word belongs to kept content, EXCLUDE it from removal."""


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


def plan(words, instruction, gap_merge=0.6):
    if not words:
        return {"removed_ids": set(), "ranges": [], "time_ranges": [], "reason": "", "provider": ""}
    id2 = {w["id"]: w for w in words}

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
        t0, t1 = ch[0]["start"], ch[-1]["end"]
        user = (f"INSTRUCTION (Arabic): {instruction}\n\n"
                f"This chunk spans {_fmt_ts(t0)} to {_fmt_ts(t1)}.\n"
                f"TRANSCRIPT (id:word):\n{numbered}\n\nReturn the JSON now.")
        txt, provider = providers.chat(
            [{"role": "system", "content": SYS},
             {"role": "user", "content": user}],
            json_mode=True, temperature=0, max_tokens=1400)
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
            "reason": " · ".join(reasons)[:300], "provider": provider}
