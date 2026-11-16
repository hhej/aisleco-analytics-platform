"""Chat gateway: the one HTTP endpoint the chat platforms push their events to (Spec 01 §3).

LINE, Meta (Messenger and Instagram) and the shared inbox's send log POST here. The gateway only checks that a request
is genuine (HMAC signature or API key) and appends the raw body, unparsed, as one JSON line to an hourly file in
lake/landing/chat/. Cleaning, de-duplication and validation happen later in the pipeline (Silver, rule R20).
Requests that fail the check get 401 and are kept in lake/landing/chat/_rejected/ for inspection.
In GATEWAY_MODE=sim the X-Sim-Clock header sets the receive time, so a simulated backfill lands in the right partitions.
"""
import base64
import hashlib
import hmac
import json
import os
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

from sources import settings

BKK = ZoneInfo("Asia/Bangkok")
app = FastAPI(title="Aisle & Co. chat gateway")
_lock = threading.Lock()  # one writer at a time, so concurrent appends never interleave
_counts = {"accepted": 0, "rejected": 0}


def _received_at(request: Request) -> datetime:
    sim = request.headers.get("x-sim-clock")
    if settings.GATEWAY_MODE == "sim" and sim:
        try:
            t = datetime.fromisoformat(sim)
        except ValueError:
            raise HTTPException(400, "bad X-Sim-Clock")
        return t.replace(tzinfo=BKK) if t.tzinfo is None else t.astimezone(BKK)
    return datetime.now(BKK)


def _append(path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with _lock, open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def _land(request: Request, platform: str, body: bytes) -> dict:
    t = _received_at(request)
    path = (settings.LAKE_DIR / "landing/chat" / f"platform={platform}" / f"received_date={t:%Y-%m-%d}"
            / f"hour={t:%H}" / "events.jsonl")
    text = body.decode("utf-8", errors="replace")
    _append(path, {"received_at": t.isoformat(timespec="milliseconds"), "platform": platform, "body": text,
                   "body_sha256": hashlib.sha256(body).hexdigest()})
    _counts["accepted"] += 1
    return {"ok": True}


def _reject(request: Request, body: bytes):
    t = _received_at(request)
    keep = ("x-line-signature", "x-hub-signature-256", "content-type")  # never log the API key itself
    headers = {k: v for k, v in request.headers.items() if k in keep}
    path = settings.LAKE_DIR / "landing/chat/_rejected" / f"received_date={t:%Y-%m-%d}" / "requests.jsonl"
    _append(path, {"received_at": t.isoformat(timespec="milliseconds"), "path": request.url.path,
                   "headers": headers, "body": body.decode("utf-8", errors="replace")})
    _counts["rejected"] += 1
    raise HTTPException(401, "signature check failed")


def _hmac(secret: str, body: bytes) -> bytes:
    return hmac.new(secret.encode(), body, hashlib.sha256).digest()


@app.post("/webhook/line")
async def line(request: Request):
    body = await request.body()
    expected = base64.b64encode(_hmac(settings.LINE_CHANNEL_SECRET, body)).decode()
    if not hmac.compare_digest(expected, request.headers.get("x-line-signature", "")):
        _reject(request, body)
    return _land(request, "line", body)


@app.get("/webhook/meta")
async def meta_verify(request: Request):
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and hmac.compare_digest(q.get("hub.verify_token", ""),
                                                                settings.META_VERIFY_TOKEN):
        return PlainTextResponse(q.get("hub.challenge", ""))
    raise HTTPException(403, "verification failed")


@app.post("/webhook/meta")
async def meta(request: Request):
    body = await request.body()
    expected = "sha256=" + _hmac(settings.META_APP_SECRET, body).hex()
    if not hmac.compare_digest(expected, request.headers.get("x-hub-signature-256", "")):
        _reject(request, body)
    try:
        obj = json.loads(body).get("object")
    except (ValueError, AttributeError):
        obj = None  # unparseable but genuine: land it anyway, Silver quarantines it
    return _land(request, "instagram" if obj == "instagram" else "messenger", body)


@app.post("/outbound")
async def outbound(request: Request):
    body = await request.body()
    if not hmac.compare_digest(request.headers.get("x-api-key", ""), settings.INBOX_API_KEY):
        _reject(request, body)
    return _land(request, "outbound", body)


@app.get("/health")
def health():
    return {"ok": True, "mode": settings.GATEWAY_MODE, **_counts}
