"""
karaoke.py — مكوّن HTML يزامن الفيديو مع النص.
- النص يتحرّك مع الفيديو (تمييز الكلمة المنطوقة لحظيًا).
- الضغط على أي كلمة يقفز بالفيديو إلى توقيتها.
يُضمّن الفيديو كـ base64 داخل iframe المكوّن.
"""
import base64
import json
import os
import streamlit as st
import streamlit.components.v1 as components

MAX_MB = 60  # حد حجم الفيديو للتضمين المباشر


def render(video_path, words, height=440):
    """يعرض مشغّلًا متزامنًا. يرجّع True لو نجح، False لو تخطّى (فيديو كبير)."""
    try:
        size_mb = os.path.getsize(video_path) / (1024 * 1024)
    except OSError:
        return False
    if size_mb > MAX_MB:
        return False

    ext = os.path.splitext(video_path)[1].lstrip(".").lower() or "mp4"
    mime = {"mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime",
            "mkv": "video/x-matroska", "m4v": "video/mp4"}.get(ext, "video/mp4")
    with open(video_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    items = [{"i": w["id"], "t": w["text"], "s": float(w["start"]), "e": float(w["end"]),
              "k": w.get("kind", "speech")} for w in words]
    data = json.dumps(items, ensure_ascii=False)

    html = """
<div id="mb-wrap" style="font-family:'Tajawal',sans-serif;direction:rtl">
  <video id="mb-vid" controls style="width:100%;max-width:420px;display:block;margin:0 auto;border-radius:14px;background:#000"></video>
  <div style="display:flex;justify-content:space-between;align-items:center;margin:.6rem 0 .3rem">
    <span style="font-weight:700;color:#0f172a">📝 النص المتزامن — اضغط أي كلمة للقفز إليها</span>
    <label style="font-size:.85rem;color:#475569;cursor:pointer">
      <input type="checkbox" id="mb-follow" checked> تتبّع تلقائي
    </label>
  </div>
  <div id="mb-script" style="max-height:240px;overflow:auto;background:#fff;border:1px solid #e8edf3;
       border-radius:12px;padding:14px;line-height:2.2;font-size:1.08rem;color:#1e293b"></div>
</div>
<script>
const VID_SRC = "data:__MIME__;base64,__B64__";
const WORDS = __DATA__;
const vid = document.getElementById('mb-vid');
const box = document.getElementById('mb-script');
const follow = document.getElementById('mb-follow');
vid.src = VID_SRC;

// بناء الكلمات
WORDS.forEach(w => {
  const sp = document.createElement('span');
  sp.textContent = w.t + ' ';
  sp.dataset.s = w.s; sp.dataset.e = w.e; sp.id = 'w'+w.i;
  sp.style.cursor = 'pointer';
  sp.style.padding = '1px 3px';
  sp.style.borderRadius = '5px';
  sp.style.transition = 'background .15s';
  if (w.k === 'filler') sp.style.color = '#94a3b8';
  sp.onclick = () => { vid.currentTime = w.s; vid.play(); };
  sp.onmouseenter = () => { if(!sp.classList.contains('on')) sp.style.background='#f1f5f9'; };
  sp.onmouseleave = () => { if(!sp.classList.contains('on')) sp.style.background='transparent'; };
  box.appendChild(sp);
});

let cur = null;
vid.addEventListener('timeupdate', () => {
  const t = vid.currentTime;
  let active = null;
  for (const w of WORDS) { if (t >= w.s && t < w.e) { active = w.i; break; } }
  if (active === cur) return;
  if (cur !== null) { const o = document.getElementById('w'+cur); if(o){o.classList.remove('on'); o.style.background='transparent'; o.style.color = o.dataset.f||'';} }
  cur = active;
  if (cur !== null) {
    const el = document.getElementById('w'+cur);
    if (el) {
      el.classList.add('on');
      el.style.background = 'linear-gradient(135deg,#0d9488,#14b8a6)';
      el.dataset.f = el.style.color; el.style.color = '#fff';
      if (follow.checked) el.scrollIntoView({block:'center', behavior:'smooth'});
    }
  }
});
</script>
""".replace("__MIME__", mime).replace("__B64__", b64).replace("__DATA__", data)

    components.html(html, height=height, scrolling=False)
    return True
