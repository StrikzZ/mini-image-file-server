import os
import uuid
import asyncio
import json
import mimetypes
import re
import math
from urllib.parse import quote
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path

import filetype
from fastapi import FastAPI, File, UploadFile, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
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

# Projektpfade (Standard-Layout: static/ und templates/ neben main.py)
APP_DIR = Path(__file__).parent
STATIC_DIR = APP_DIR / "static"
TEMPLATES_DIR = APP_DIR / "templates"

# -----------------
# Template-Engine
# -----------------
# Jinja2 ueber FastAPIs Jinja2Templates. Auto-Escaping ist standardmaessig aktiv,
# daher muessen Werte NICHT mehr manuell mit html.escape() behandelt werden.
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

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
    "application/gzip",
    "application/x-bzip2",
    "application/x-xz",
}

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

def _now() -> datetime: return datetime.now(timezone.utc)

def _guess(path: Path):
    k = filetype.guess(path)
    if not k: return None, None
    return k.mime, k.extension

def _safe_disp_name(name: str) -> str:
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
                            if folder is FILES_DIR and p.suffix.lower() != ".json":
                                (FILES_DIR / f"{p.stem}.json").unlink(missing_ok=True)
                            p.unlink(missing_ok=True)
                    except FileNotFoundError:
                        pass
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
    allowed = IMAGE_MIME | ARCHIVE_MIME
    missing = allowed - set(EXT_BY_MIME.keys())
    if missing:
        raise RuntimeError(f"EXT_BY_MIME fehlt für: {', '.join(sorted(missing))}")
    task = asyncio.create_task(cleanup_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError): await task


app = FastAPI(title="mini-image-file-server", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["10.0.0.0/8", "127.0.0.1", "172.16.0.0/12", "192.168.0.0/16"])

# --- Security Headers Middleware ---
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        if "X-Content-Type-Options" not in resp.headers:
            resp.headers["X-Content-Type-Options"] = "nosniff"
        if "Referrer-Policy" not in resp.headers:
            resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if "X-Frame-Options" not in resp.headers:
            resp.headers["X-Frame-Options"] = "DENY"
        try:
            if request.url.scheme == "https" and "Strict-Transport-Security" not in resp.headers:
                resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        except Exception: pass
        ctype = str(resp.headers.get("content-type","")).lower()
        if ctype.startswith("text/html"):
            if "Content-Security-Policy" not in resp.headers:
                resp.headers["Content-Security-Policy"] = (
                    "default-src 'none'; script-src 'self' 'unsafe-inline'; "
                    "connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                    "base-uri 'none'; frame-ancestors 'none'; object-src 'none'"
                )
            resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
            resp.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        return resp

app.add_middleware(SecurityHeadersMiddleware)

# -----------------
# Static Assets
# -----------------
# Alles unter static/ (css, js, img) wird direkt von StaticFiles ausgeliefert.
# Erreichbar unter /static/... und in Templates via url_for('static', path='...').
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# -----------------
# Landingpage mit Copy & Pagination (+ Paste + Clientgröße-Check)
# -----------------
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"title": LANDINGPAGE_TITLE, "max_file_mb": MAX_FILE_MB},
    )


# -----------------
# Upload & Klassifikation
# -----------------
CHUNK_SIZE = 1024 * 1024  # 1 MiB

@app.post("/upload")
async def upload(request: Request, file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(400, "no filename")

    # Frühe Abweisung per Content-Length (falls vorhanden)
    cl = request.headers.get("content-length")
    max_bytes = MAX_FILE_MB * 1024 * 1024
    if cl:
        try:
            if int(cl) > max_bytes:
                raise HTTPException(413, "too large")
        except ValueError:
            pass

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
                    # Sofort abbrechen, Temp-Datei wieder löschen
                    raise HTTPException(413, f"file too large (> {MAX_FILE_MB} MB)")
                out.write(chunk)
    except HTTPException:
        tmp.unlink(missing_ok=True)
        with suppress(Exception): await file.close()
        raise
    except Exception:
        tmp.unlink(missing_ok=True)
        with suppress(Exception): await file.close()
        raise
    finally:
        with suppress(Exception): await file.close()

    if size == 0:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, "empty upload")

    mime, _magic_ext = _guess(tmp)
    if not mime:
        tmp.unlink(missing_ok=True)
        raise HTTPException(415, "unsupported media type")

    orig_name = Path(file.filename).name
    if mime in IMAGE_MIME:
        fid = uuid.uuid4().hex
        dst = IMAGES_DIR / f"{fid}{EXT_BY_MIME[mime]}"
        tmp.rename(dst)
        base = str(request.base_url).rstrip("/")
        return JSONResponse({
            "type":"image","id":fid,
            "page_url":f"{base}/i/{fid}",
            "raw_url":f"{base}/raw/image/{fid}",
        })
    elif mime in ARCHIVE_MIME:
        fid = uuid.uuid4().hex
        dst = FILES_DIR / f"{fid}{EXT_BY_MIME[mime]}"
        tmp.rename(dst)
        meta = {"id":fid,"original_name":orig_name,"saved_name":dst.name,
                "size":dst.stat().st_size,
                "created":datetime.fromtimestamp(dst.stat().st_mtime,timezone.utc).isoformat()}
        (FILES_DIR/f"{fid}.json").write_text(json.dumps(meta),encoding="utf-8")
        base = str(request.base_url).rstrip("/")
        return JSONResponse({
            "type":"file","id":fid,
            "page_url":f"{base}/f/{fid}",
            "raw_url":f"{base}/raw/file/{fid}",
            "original_name":orig_name,
        })

    tmp.unlink(missing_ok=True)
    raise HTTPException(415,f"media type not allowed: {mime}")

# -----------------
# Einzelansichten (/i und /f)
# -----------------
@app.get("/i/{fid}", response_class=HTMLResponse)
async def image_page(request: Request, fid: str):
    matches = list(IMAGES_DIR.glob(f"{fid}.*")) + list(IMAGES_DIR.glob(fid))
    if not matches:
        if (FILES_DIR / f"{fid}.json").exists():
            return RedirectResponse(url=f"/f/{fid}", status_code=302)
        raise HTTPException(404, "not found")
    raw_path = request.app.url_path_for("raw_image", fid=fid)
    raw_abs  = str(request.base_url).rstrip("/") + raw_path #Gives full URL (placeholder)
    created = datetime.fromtimestamp(matches[0].stat().st_mtime, timezone.utc)
    ttl = max(0, TTL_DAYS - (_now() - created).days)
    return templates.TemplateResponse(
        request,
        "image.html",
        {
            "title": LANDINGPAGE_TITLE,
            "fid": fid,
            "ttl": ttl,
            "raw_path": str(raw_path),
        },
    )

@app.get("/f/{fid}", response_class=HTMLResponse)
async def file_page(request: Request, fid: str):
    meta_path = FILES_DIR / f"{fid}.json"
    if not meta_path.exists():
        matches = list(IMAGES_DIR.glob(f"{fid}.*"))
        if matches:
            return RedirectResponse(url=f"/i/{fid}", status_code=302)
        raise HTTPException(404, "not found")
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    raw_path = request.app.url_path_for("raw_file", fid=fid)
    real = [p for p in FILES_DIR.iterdir() if p.is_file() and p.stem == fid and p.suffix.lower() != '.json']
    if not real: raise HTTPException(404, "not found")
    p = real[0]
    created = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)
    ttl = max(0, TTL_DAYS - (_now() - created).days)
    icon_url = "/static/img/zip_icon.png"
    name = meta.get('original_name') or fid
    size_kb = max(1, (p.stat().st_size // 1024))
    return templates.TemplateResponse(
        request,
        "file.html",
        {
            "title": LANDINGPAGE_TITLE,
            "fid": fid,
            "name": name,
            "size_kb": size_kb,
            "ttl": ttl,
            "icon_url": icon_url,
            "raw_path": str(raw_path),
        },
    )

# -----------------
# Raw Data
# -----------------
@app.get("/raw/image/{fid}")
async def raw_image(fid: str):
    matches = list(IMAGES_DIR.glob(f"{fid}.*")) + list(IMAGES_DIR.glob(fid))
    if not matches: raise HTTPException(404, "not found")
    p = matches[0]
    ext = p.suffix.lower().lstrip(".")
    media = {"jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png","gif":"image/gif","webp":"image/webp"}.get(ext,"application/octet-stream")
    resp = FileResponse(p, media_type=media)
    resp.headers["Cache-Control"]="public, max-age=604800, immutable"
    return resp

@app.get("/raw/file/{fid}")
async def raw_file(fid: str):
    meta_path = FILES_DIR / f"{fid}.json"
    if not meta_path.exists(): raise HTTPException(404,"not found")
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    file_matches=[p for p in FILES_DIR.iterdir() if p.is_file() and p.stem==fid and p.suffix.lower()!='.json']
    if not file_matches: raise HTTPException(404,"not found")
    p=file_matches[0]
    mime_magic,_=_guess(p)
    media_type,_=mimetypes.guess_type(p.name)
    final_mime=mime_magic or media_type or 'application/octet-stream'
    resp=FileResponse(p,media_type=final_mime)
    disp=_safe_disp_name(meta.get('original_name') or p.name)
    resp.headers["Content-Disposition"]=f"attachment; filename*={disp}"
    resp.headers["Cache-Control"]="public, max-age=604800"
    return resp

# -----------------
# Listen mit Pagination
# -----------------
def _paginate(items,page:int,limit:int):
    total=len(items)
    total_pages=max(1,math.ceil(total/limit)) if limit>0 else 1
    page=max(1,min(page,total_pages))
    start=(page-1)*limit; end=start+limit
    return items[start:end],dict(page=page,per_page=limit,total=total,total_pages=total_pages)

@app.get("/list/images")
async def list_images(page:int=Query(1,ge=1),limit:int=Query(15,ge=1,le=100)):
    items=[{'id':p.stem,'page_url':f'/i/{p.stem}','raw_url':f'/raw/image/{p.stem}',
            'created':datetime.fromtimestamp(p.stat().st_mtime,timezone.utc).isoformat()}
            for p in IMAGES_DIR.iterdir() if p.is_file()]
    items.sort(key=lambda x:x['created'],reverse=True)
    sliced,meta=_paginate(items,page,limit)
    return {'items':sliced,**meta}

@app.get("/list/files")
async def list_files(page:int=Query(1,ge=1),limit:int=Query(15,ge=1,le=100)):
    items=[]
    for meta in FILES_DIR.glob('*.json'):
        try:
            m=json.loads(meta.read_text(encoding='utf-8'))
            fid=m.get('id') or meta.stem
            items.append({'id':fid,'page_url':f'/f/{fid}','raw_url':f'/raw/file/{fid}',
                          'created':m.get('created'),'size':m.get('size'),
                          'original_name':m.get('original_name') or fid})
        except Exception: continue
    items.sort(key=lambda x:x.get('created') or '',reverse=True)
    sliced,meta=_paginate(items,page,limit)
    return {'items':sliced,**meta}

@app.get("/health")
async def health(): return {"status":"ok"}

if __name__=="__main__":
    import uvicorn
    uvicorn.run(app,host="0.0.0.0",port=int(os.environ.get("PORT","8080")))
