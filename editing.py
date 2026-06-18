"""
editing.py — كل عمليات القص والدمج والتصدير على الفيديو الحقيقي عبر FFmpeg.

الفكرة: الواجهة بتبعت "المقاطع المُبقاة" (kept segments) كقائمة [(start, end), ...]
وإحنا بنقصّها بدقة (إعادة ترميز عشان القص يبقى مضبوط على الإطار) وندمجها في فيديو واحد.
"""

import subprocess
import os
import math


def _run(cmd):
    """تشغيل أمر FFmpeg وإظهار الخطأ كامل لو فشل."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg failed:\n{proc.stderr[-2000:]}")
    return proc


def has_audio(path: str) -> bool:
    """هل الفيديو فيه مسار صوتي؟"""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    return bool(r.stdout.strip())


def get_duration(path: str) -> float:
    """مدة الفيديو بالثواني."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except (ValueError, TypeError):
        return 0.0


def _build_concat_filter(segments, audio: bool) -> str:
    """يبني filter_complex لقص كل مقطع ثم دمجهم في تيار واحد."""
    parts = []
    labels = ""
    for i, (s, e) in enumerate(segments):
        parts.append(
            f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}]"
        )
        if audio:
            parts.append(
                f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}]"
            )
        labels += f"[v{i}]" + (f"[a{i}]" if audio else "")
    n = len(segments)
    concat = labels + f"concat=n={n}:v=1:a={1 if audio else 0}"
    concat += "[outv][outa]" if audio else "[outv]"
    return ";".join(parts) + ";" + concat


def export_segments(input_path: str, segments, output_path: str) -> str:
    """
    يقص المقاطع المُبقاة ويدمجها في فيديو واحد.
    segments: قائمة [(start, end), ...] بالثواني (مرتّبة).
    """
    segments = [(round(float(s), 3), round(float(e), 3))
                for s, e in segments if float(e) - float(s) > 0.04]
    if not segments:
        raise ValueError("لا توجد مقاطع للتصدير")

    audio = has_audio(input_path)
    filt = _build_concat_filter(segments, audio)

    cmd = ["ffmpeg", "-y", "-i", input_path, "-filter_complex", filt,
           "-map", "[outv]"]
    if audio:
        cmd += ["-map", "[outa]"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += [output_path]

    _run(cmd)
    return output_path


def export_clip(input_path: str, start: float, end: float, output_path: str) -> str:
    """يصدّر مقطعًا واحدًا (للسوشيال مثلاً) بدقة على الإطار."""
    start, end = float(start), float(end)
    if end - start < 0.1:
        raise ValueError("المقطع قصير جدًا")
    audio = has_audio(input_path)
    cmd = ["ffmpeg", "-y", "-i", input_path,
           "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += [output_path]
    _run(cmd)
    return output_path


def _fmt_ts(t: float) -> str:
    """تنسيق توقيت SRT: HH:MM:SS,mmm"""
    if t < 0:
        t = 0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - math.floor(t)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt_from_kept(kept_words, segments, srt_path: str,
                        max_words=7) -> str:
    """
    يبني ملف ترجمة (SRT) موقّت على *التيار الجديد* بعد القص.
    kept_words: [{text,start,end}] بالتوقيت الأصلي (المُبقاة فقط).
    segments: المقاطع المُبقاة [(start,end)] لحساب الإزاحة الزمنية الجديدة.
    """
    # خريطة: الزمن الأصلي -> الزمن الجديد (بعد حذف الفراغات)
    def to_new(t):
        new = 0.0
        for s, e in segments:
            if t >= e:
                new += (e - s)
            elif t >= s:
                new += (t - s)
                return new
        return new

    lines = []
    idx = 1
    i = 0
    n = len(kept_words)
    while i < n:
        chunk = kept_words[i:i + max_words]
        text = " ".join(w["text"] for w in chunk).strip()
        start_new = to_new(chunk[0]["start"])
        end_new = to_new(chunk[-1]["end"])
        if end_new <= start_new:
            end_new = start_new + 0.6
        lines.append(f"{idx}\n{_fmt_ts(start_new)} --> {_fmt_ts(end_new)}\n{text}\n")
        idx += 1
        i += max_words

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return srt_path


def burn_subtitles(input_path: str, srt_path: str, output_path: str,
                   font="Arial", font_size=22) -> str:
    """يحرق الترجمة العربية على الفيديو (libass يتولّى تشكيل العربية)."""
    style = (f"FontName={font},FontSize={font_size},"
             "PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,"
             "BorderStyle=3,Outline=2,Shadow=0,Alignment=2,MarginV=28")
    safe = srt_path.replace("\\", "/").replace(":", "\\:")
    cmd = ["ffmpeg", "-y", "-i", input_path,
           "-vf", f"subtitles='{safe}':force_style='{style}'",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-pix_fmt", "yuv420p", "-c:a", "copy", output_path]
    _run(cmd)
    return output_path
