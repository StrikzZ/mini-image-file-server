import os
import uuid
import asyncio
import json
import mimetypes
import html
import re
from urllib.parse import quote
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path

import filetype
from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse, PlainTextResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

# -----------------
# Konfiguration
# -----------------
DATA_ROOT = Path(os.environ.get("DATA_ROOT", "data"))
IMAGES_DIR = DATA_ROOT / "images"
FILES_DIR = DATA_ROOT / "files"
for d in (IMAGES_DIR, FILES_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Pfad zum Icon (liegt neben diesem Script)
APP_DIR = Path(__file__).parent
ZIP_ICON_PATH = APP_DIR / "zip_icon.png"

TTL_DAYS = int(os.environ.get("TTL_DAYS", "14"))
CLEANUP_INTERVAL_SECONDS = int(os.environ.get("CLEANUP_INTERVAL_SECONDS", str(6 * 60 * 60)))
MAX_FILE_MB = int(os.environ.get("MAX_FILE_MB", "15"))
LANDINGPAGE_TITLE = str(os.environ.get("LANDINGPAGE_TITLE", "Mini image and file server"))
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS","localhost,127.0.0.1").split(",")]

# Erlaubte Typen (nur Magic-Bytes, keine Dateinamen-Heuristik)
IMAGE_MIME = {"image/jpeg", "image/png", "image/gif", "image/webp"}
ARCHIVE_MIME = {
    "application/zip",
    "application/x-zip-compressed",
    "application/x-7z-compressed",
    "application/x-rar-compressed",
    "application/x-tar",
    "application/gzip",      # .gz (inkl. oft .tar.gz)
    "application/x-bzip2",   # .bz2 (inkl. .tar.bz2)
    "application/x-xz",      # .xz  (inkl. .tar.xz)
}

# Kanonische Endungen je MIME
EXT_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",

    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
    "application/x-7z-compressed": ".7z",
    "application/x-rar-compressed": ".rar",
    "application/x-tar": ".tar",
    "application/gzip": ".gz",
    "application/x-bzip2": ".bz2",
    "application/x-xz": ".xz",
}

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _guess(path: Path):
    k = filetype.guess(path)
    if not k:
        return None, None
    return k.mime, k.extension

# --- Security: sicherer Disposition-Dateiname für Header ---
def _safe_disp_name(name: str) -> str:
    # Steuerzeichen entfernen, dann RFC 5987 encoded
    cleaned = re.sub(r'[\r\n\t]', '', name or '')
    return "UTF-8''" + quote(cleaned, safe="!#$&+-.^_`|~ ()[]{}")

# -----------------
# Lifespan + Cleanup
# -----------------
async def cleanup_loop():
    while True:
        try:
            cutoff = _now() - timedelta(days=TTL_DAYS)
            for folder in (IMAGES_DIR, FILES_DIR):
                for p in folder.iterdir():
                    try:
                        if p.is_file() and datetime.fromtimestamp(p.stat().st_mtime, timezone.utc) < cutoff:
                            # passende JSON bei Dateien mitlöschen
                            if folder is FILES_DIR and p.suffix.lower() != ".json":
                                (FILES_DIR / f"{p.stem}.json").unlink(missing_ok=True)
                            p.unlink(missing_ok=True)
                    except FileNotFoundError:
                        pass
            # JSON-Waisen aufräumen
            for meta in FILES_DIR.glob("*.json"):
                fid = meta.stem
                exists = any(q for q in FILES_DIR.iterdir()
                             if q.is_file() and q.stem == fid and q.suffix.lower() != ".json")
                if not exists:
                    meta.unlink(missing_ok=True)
        except Exception:
            pass
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup-Check: jedes erlaubte MIME muss eine Endung haben
    allowed = IMAGE_MIME | ARCHIVE_MIME
    missing = allowed - set(EXT_BY_MIME.keys())
    if missing:
        raise RuntimeError(f"EXT_BY_MIME fehlt für: {', '.join(sorted(missing))}")

    task = asyncio.create_task(cleanup_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="mini-image-file-server", lifespan=lifespan)

app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["10.0.0.0/8","127.0.0.1"])

# --- Security Headers Middleware (Klassenbasiert, wie im GitHub-Beispiel) ---
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)

        # Immer sinnvoll – für alle Responses
        if "X-Content-Type-Options" not in resp.headers:
            resp.headers["X-Content-Type-Options"] = "nosniff"
        if "Referrer-Policy" not in resp.headers:
            resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if "X-Frame-Options" not in resp.headers:
            resp.headers["X-Frame-Options"] = "DENY"

        # HSTS NUR, wenn via HTTPS (oder Proxy terminiert immer HTTPS)
        try:
            if request.url.scheme == "https" and "Strict-Transport-Security" not in resp.headers:
                resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        except Exception:
            pass

        # CSP nur für HTML (für /raw/* irrelevant)
        ctype = resp.headers.get("content-type", "")
        if isinstance(ctype, bytes):
            ctype = ctype.decode("latin-1", "ignore")
        if str(ctype).lower().startswith("text/html"):
            if "Content-Security-Policy" not in resp.headers:
                resp.headers["Content-Security-Policy"] = (
                    "default-src 'none'; "
                    "script-src 'self' 'unsafe-inline'; "
                    "connect-src 'self'; "
                    "img-src 'self' data:; "
                    "style-src 'self' 'unsafe-inline'; "
                    "base-uri 'none'; "
                    "frame-ancestors 'none'; "
                    "object-src 'none'"
                )
            if "Cross-Origin-Opener-Policy" not in resp.headers:
                resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
            if "X-Robots-Tag" not in resp.headers:
                resp.headers["X-Robots-Tag"] = "noindex, nofollow"

        return resp

# Registrieren (nach CORS)
app.add_middleware(SecurityHeadersMiddleware)

# -----------------
# Static Assets
# -----------------
@app.get("/assets/zip_icon.png")
async def static_zip_icon():
    if not ZIP_ICON_PATH.exists():
        raise HTTPException(404, "zip_icon.png not found next to the script")
    resp = FileResponse(ZIP_ICON_PATH, media_type="image/png")
    resp.headers["Cache-Control"] = "public, max-age=604800, immutable"
    return resp


# -----------------
# HTML (Gallery mit Switch)
# -----------------
@app.get("/", response_class=HTMLResponse)
async def root() -> str:
    return f"""
    <html><head><meta name='viewport' content='width=device-width, initial-scale=1'>
    <title>{LANDINGPAGE_TITLE}</title>
    <style>
      :root{{--bg:#fafafa;--fg:#111;--muted:#666;--card:#fff;--br:12px}}
      *{{box-sizing:border-box}} body{{font-family:system-ui;margin:0;background:var(--bg);color:var(--fg)}}
      header{{padding:16px 20px;border-bottom:1px solid #eee;background:#fff;position:sticky;top:0}}
      h1{{font-size:18px;margin:0}}
      main{{max-width:1100px;margin:0 auto;padding:20px}}
      .row{{display:flex;gap:16px;flex-wrap:wrap}}
      .uploader{{flex:1 1 360px;background:var(--card);border:2px dashed #ddd;border-radius:var(--br);padding:18px;min-height:140px;display:flex;flex-direction:column;justify-content:center;align-items:center}}
      .uploader.drag{{border-color:#aaa;background:#f7f7f7}}
      .uploader input[type=file]{{display:none}}
      .btn{{display:inline-block;padding:10px 14px;border:1px solid #ddd;border-radius:10px;text-decoration:none;color:inherit}}
      .muted{{color:var(--muted);font-size:14px}}
      .tabs{{display:flex;gap:8px;margin-top:18px}}
      .tab{{padding:6px 10px;border:1px solid #ddd;border-radius:8px;background:#fff;cursor:pointer}}
      .tab.active{{background:#111;color:#fff;border-color:#111}}
      .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:14px;margin-top:14px}}
      .card{{background:var(--card);border:1px solid #eee;border-radius:var(--br);overflow:hidden}}
      .thumb{{aspect-ratio:1/1;display:block;width:100%;height:auto;object-fit:cover;background:#eee}}
      .caption{{padding:6px 10px;font-size:12px;color:#333;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
      .meta{{padding:8px 10px;display:flex;justify-content:space-between;align-items:center}}
      .meta a{{font-size:12px}}
      .badge{{font-size:12px;color:#555}}
      progress{{width:100%;height:10px;margin-top:8px}}
    </style>
    </head>
    <body>
      <header><h1>{LANDINGPAGE_TITLE}</h1></header>
      <main>
        <div class='row'>
          <div id='drop' class='uploader'>
            <p>Zieh Bild/Datei hierher oder <label for='file' class='btn'>Datei wählen</label></p>
            <p class='muted'>Bilder: JPG/PNG/GIF/WEBP · Archive: ZIP/TAR/RAR/7Z · max. {MAX_FILE_MB} MB</p>
            <input id='file' type='file' />
            <progress id='prog' value='0' max='100' style='display:none'></progress>
          </div>
        </div>

        <div class='tabs'>
          <button id='tab-img' class='tab active'>Bilder</button>
          <button id='tab-files' class='tab'>Dateien</button>
        </div>

        <div id='grid' class='grid'></div>
      </main>

      <script>
        const grid = document.getElementById('grid');
        const drop = document.getElementById('drop');
        const fileInput = document.getElementById('file');
        const prog = document.getElementById('prog');
        const tabImg = document.getElementById('tab-img');
        const tabFiles = document.getElementById('tab-files');
        let current = 'images';

        tabImg.onclick = () => {{ current='images'; tabImg.classList.add('active'); tabFiles.classList.remove('active'); fetchList(); }};
        tabFiles.onclick = () => {{ current='files'; tabFiles.classList.add('active'); tabImg.classList.remove('active'); fetchList(); }};

        async function fetchList() {{
          const r = await fetch(current === 'images' ? '/list/images' : '/list/files');
          const data = await r.json();
          renderGrid(data.items);
        }}

        function renderGrid(items) {{
          grid.innerHTML = '';
          for (const it of items) {{
            const card = document.createElement('div'); card.className = 'card';
            if (current === 'images') {{
              const img = document.createElement('img');
              img.className = 'thumb';
              img.loading = 'lazy';
              img.src = it.raw_url;
              img.alt = it.id;
              card.append(img);
            }} else {{
              const img = document.createElement('img');
              img.className = 'thumb';
              img.loading = 'lazy';
              img.src = '/assets/zip_icon.png';
              img.alt = it.original_name || it.id;
              card.append(img);

              const cap = document.createElement('div');
              cap.className = 'caption';
              cap.title = it.original_name || it.id;
              cap.textContent = it.original_name || it.id;
              card.append(cap);
            }}
            const meta = document.createElement('div'); meta.className = 'meta';
            const left = document.createElement('span'); left.className = 'badge'; left.textContent = timeAgo(new Date(it.created));
            const a = document.createElement('a'); a.href = it.page_url; a.target = '_blank'; a.textContent = 'Öffnen';
            meta.append(left, a); card.append(meta); grid.append(card);
          }}
        }}

        function timeAgo(date) {{
          const s = Math.floor((Date.now() - date.getTime()) / 1000);
          const i = Math.floor(s / 60);
          const h = Math.floor(i / 60);
          const d = Math.floor(h / 24);
          if (s < 60) return s + 's';
          if (i < 60) return i + 'm';
          if (h < 24) return h + 'h';
          return d + 'd';
        }}

        function uploadFile(file) {{
          const fd = new FormData(); fd.append('file', file);
          prog.style.display = 'block'; prog.value = 0;
          return new Promise((resolve, reject) => {{
            const xhr = new XMLHttpRequest(); xhr.open('POST', '/upload');
            xhr.upload.onprogress = (e) => {{ if (e.lengthComputable) prog.value = (e.loaded / e.total) * 100; }};
            xhr.onload = () => {{ prog.style.display = 'none'; prog.value = 0; if (xhr.status >= 200 && xhr.status < 300) resolve(JSON.parse(xhr.responseText)); else reject(xhr.responseText); }};
            xhr.onerror = () => {{ prog.style.display = 'none'; reject('network error'); }};
            xhr.send(fd);
          }});
        }}

        ['dragenter','dragover'].forEach(ev => drop.addEventListener(ev, e => {{ e.preventDefault(); e.stopPropagation(); drop.classList.add('drag'); }}));
        ['dragleave','drop'].forEach(ev => drop.addEventListener(ev, e => {{ e.preventDefault(); e.stopPropagation(); drop.classList.remove('drag'); }}));
        drop.addEventListener('drop', async (e) => {{ const f = e.dataTransfer.files; if (!f || !f.length) return; try {{ await uploadFile(f[0]); await fetchList(); }} catch (err) {{ alert('Upload fehlgeschlagen: ' + err); }} }});
        fileInput.addEventListener('change', async () => {{ if (!fileInput.files || !fileInput.files.length) return; try {{ await uploadFile(fileInput.files[0]); fileInput.value=''; await fetchList(); }} catch (err) {{ alert('Upload fehlgeschlagen: ' + err); }} }});

        fetchList();
      </script>
    </body></html>
    """

# -----------------
# Upload & Klassifikation (Magic-Bytes only)
# -----------------
CHUNK_SIZE = 1024 * 1024  # 1 MiB

@app.post("/upload")
async def upload(request: Request, file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(400, "no filename")

    # Frontdoor-Limit via Content-Length (falls gesetzt)
    cl = request.headers.get("content-length")
    max_bytes = MAX_FILE_MB * 1024 * 1024
    if cl:
        try:
            if int(cl) > max_bytes:
                raise HTTPException(413, "too large")
        except ValueError:
            pass  # ungültiger Header ignorieren; wir prüfen beim Streamen erneut

    # Streaming Upload -> temporäre Datei
    tmp = DATA_ROOT / f"tmp_{uuid.uuid4().hex}"
    size = 0
    try:
        with tmp.open("wb") as out:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(413, f"file too large (> {MAX_FILE_MB} MB)")
                out.write(chunk)
    finally:
        with suppress(Exception):
            await file.close()

    if size == 0:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, "empty upload")

    # Magic-Bytes Klassifikation
    mime, _magic_ext = _guess(tmp)
    if not mime:
        tmp.unlink(missing_ok=True)
        raise HTTPException(415, "unsupported media type (unrecognized)")

    orig_name = Path(file.filename).name  # nur für UI/Download-Header

    if mime in IMAGE_MIME:
        save_ext = EXT_BY_MIME[mime]  # kanonisch
        fid = uuid.uuid4().hex
        dst = IMAGES_DIR / f"{fid}{save_ext}"
        tmp.rename(dst)

        base = str(request.base_url).rstrip("/")
        return JSONResponse({
            "type": "image",
            "id": fid,
            "page_url": f"{base}/i/{fid}",
            "raw_url":  f"{base}/raw/image/{fid}",
        })

    elif mime in ARCHIVE_MIME:
        save_ext = EXT_BY_MIME[mime]  # kanonisch
        fid = uuid.uuid4().hex
        dst = FILES_DIR / f"{fid}{save_ext}"
        tmp.rename(dst)

        # Sidecar-Meta
        meta = {
            "id": fid,
            "original_name": orig_name,
            "saved_name": dst.name,
            "size": dst.stat().st_size,
            "created": datetime.fromtimestamp(dst.stat().st_mtime, timezone.utc).isoformat(),
        }
        (FILES_DIR / f"{fid}.json").write_text(json.dumps(meta), encoding="utf-8")

        base = str(request.base_url).rstrip("/")
        return JSONResponse({
            "type": "file",
            "id": fid,
            "page_url": f"{base}/f/{fid}",
            "raw_url":  f"{base}/raw/file/{fid}",
            "original_name": orig_name,
        })

    # erkannt, aber nicht erlaubt
    tmp.unlink(missing_ok=True)
    raise HTTPException(415, f"media type not allowed: {mime}")

# -----------------
# Seiten
# -----------------
@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots(): return "User-agent: *\nDisallow: /"

@app.get("/i/{fid}", response_class=HTMLResponse)
async def image_page(request: Request, fid: str):
    matches = list(IMAGES_DIR.glob(f"{fid}.*")) + list(IMAGES_DIR.glob(fid))
    if not matches:
        # falls altes /i/ auf ein file zeigt → redirect zur file-Seite
        if (FILES_DIR / f"{fid}.json").exists():
            return RedirectResponse(url=f"/f/{fid}", status_code=302)
        raise HTTPException(404, "not found")
    raw = request.url_for("raw_image", fid=fid)
    created = datetime.fromtimestamp(matches[0].stat().st_mtime, timezone.utc)
    ttl = max(0, TTL_DAYS - (_now() - created).days)
    return f"""
    <html><head><meta name='viewport' content='width=device-width, initial-scale=1'>
    <title>Bild {fid}</title>
    <style>body{{font-family:system-ui;margin:1rem}} img{{max-width:100%;height:auto;display:block;margin:0 auto}} .wrap{{max-width:900px;margin:0 auto}} .meta{{color:#666;font-size:.9em;margin:.5rem 0 1rem}} a.button{{display:inline-block;padding:.5rem .75rem;border:1px solid #ddd;border-radius:8px}}</style>
    </head><body><div class='wrap'>
      <p class='meta'>ID: {fid} · (verbleibend: {ttl} Tage)</p>
      <img src='{raw}' alt='uploaded image'/>
      <p><a class='button' href='{raw}' download>Download</a></p>
    </div></body></html>
    """

@app.get("/f/{fid}", response_class=HTMLResponse)
async def file_page(request: Request, fid: str):
    # Datei + Meta müssen existieren
    meta_path = FILES_DIR / f"{fid}.json"
    if not meta_path.exists():
        # evtl. altes Bild? redirect
        matches = list(IMAGES_DIR.glob(f"{fid}.*"))
        if matches:
            return RedirectResponse(url=f"/i/{fid}", status_code=302)
        raise HTTPException(404, "not found")

    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    raw = request.url_for("raw_file", fid=fid)

    # echte Datei suchen (robust bei .tar.gz etc.)
    real = [p for p in FILES_DIR.iterdir() if p.is_file() and p.stem == fid and p.suffix.lower() != '.json']
    if not real:
        raise HTTPException(404, "not found")
    p = real[0]

    created = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)
    ttl = max(0, TTL_DAYS - (_now() - created).days)
    icon_url = "/assets/zip_icon.png"
    name = html.escape(meta.get('original_name') or fid)
    size_kb = max(1, (p.stat().st_size // 1024))

    return f"""
    <html><head><meta name='viewport' content='width=device-width, initial-scale=1'>
    <title>Datei {fid}</title>
    <style>body{{font-family:system-ui;margin:1rem}} img{{max-width:100%;height:auto;display:block;margin:0 auto}} .wrap{{max-width:900px;margin:0 auto}} .meta{{color:#666;font-size:.9em;margin:.5rem 0 1rem}} a.button{{display:inline-block;padding:.5rem .75rem;border:1px solid #ddd;border-radius:8px}}</style>
    </head><body><div class='wrap'>
      <p class='meta'>ID: {fid} · {name} · {size_kb} kB · (verbleibend: {ttl} Tage)</p>
      <img src='{icon_url}' alt='file icon'/>
      <p><a class='button' href='{raw}' download>Download</a></p>
    </div></body></html>
    """

# -----------------
# Raw-Data
# -----------------
@app.get("/raw/image/{fid}")
async def raw_image(fid: str):
    matches = list(IMAGES_DIR.glob(f"{fid}.*")) + list(IMAGES_DIR.glob(fid))
    if not matches:
        raise HTTPException(404, "not found")
    p = matches[0]
    resp = FileResponse(p)
    resp.headers["Cache-Control"] = "public, max-age=604800, immutable"
    return resp

@app.get("/raw/file/{fid}")
async def raw_file(fid: str):
    meta_path = FILES_DIR / f"{fid}.json"
    if not meta_path.exists():
        raise HTTPException(404, "not found")
    meta = json.loads(meta_path.read_text(encoding='utf-8'))

    # echte Datei (beliebige Extension)
    file_matches = [p for p in FILES_DIR.iterdir() if p.is_file() and p.stem == fid and p.suffix.lower() != '.json']
    if not file_matches:
        raise HTTPException(404, "not found")
    p = file_matches[0]

    # MIME aus echtem Pfad (oder erneut per Magic)
    mime_magic, _ = _guess(p)
    media_type, _ = mimetypes.guess_type(p.name)
    final_mime = mime_magic or media_type or 'application/octet-stream'

    resp = FileResponse(p, media_type=final_mime)
    disp = _safe_disp_name(meta.get('original_name') or p.name)
    resp.headers["Content-Disposition"] = f"attachment; filename*={disp}"
    # nosniff kommt global aus der Middleware
    resp.headers["Cache-Control"] = "public, max-age=604800"
    return resp

# -----------------
# Lists
# -----------------
@app.get("/list/images")
async def list_images():
    items = []
    for p in IMAGES_DIR.iterdir():
        if not p.is_file():
            continue
        items.append({
            'id': p.stem,
            'page_url': f'/i/{p.stem}',
            'raw_url': f'/raw/image/{p.stem}',
            'created': datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(),
        })
    items.sort(key=lambda x: x['created'], reverse=True)
    return {'items': items}

@app.get("/list/files")
async def list_files():
    items = []
    for meta in FILES_DIR.glob('*.json'):
        try:
            m = json.loads(meta.read_text(encoding='utf-8'))
            fid = m.get('id') or meta.stem
            items.append({
                'id': fid,
                'page_url': f'/f/{fid}',
                'raw_url': f'/raw/file/{fid}',
                'created': m.get('created'),
                'size': m.get('size'),
                'original_name': m.get('original_name') or fid,
            })
        except Exception:
            continue
    items.sort(key=lambda x: x.get('created') or '', reverse=True)
    return {'items': items}

@app.get("/health")
async def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
