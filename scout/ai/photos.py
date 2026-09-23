"""Photo download + downscale for AI vision calls, with a disk cache.

Split out of scout.ai.assess so a shared, cheaper image pipeline can be reused
across callers. Downloads follow the same rules as the original photo_blocks:
urllib, 8s timeout, <=4MB, image content-type only, skip on any failure. The
processed JPEG is cached on disk keyed by the source URL's sha1, which also
means a photo survives its CDN link expiring after the first successful
fetch."""
from __future__ import annotations

import hashlib
from typing import Any

from scout.config import DATA_DIR

MAX_PHOTOS = 12
MAX_PHOTO_BYTES = 4_000_000
_CACHE_DIR = DATA_DIR / "photo_cache"


def photo_blocks(urls: list[str], limit: int = MAX_PHOTOS, max_edge: int = 1568) -> list[dict[str, Any]]:
    """Download (or reuse a cached copy of) each photo, downscale so the long
    edge is at most max_edge (never upscale), and return base64 image blocks.
    Any failure (expired CDN link, hotlink block, huge file, bad image data)
    just skips that photo."""
    import base64
    import urllib.request

    out: list[dict[str, Any]] = []
    for url in urls:
        if len(out) >= limit:
            break
        try:
            cache_path = _CACHE_DIR / f"{hashlib.sha1(url.encode('utf-8')).hexdigest()}.jpg"
            if cache_path.exists():
                data = cache_path.read_bytes()
            else:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) hoopty-scout/0.2", "Accept": "image/*"})
                with urllib.request.urlopen(req, timeout=8) as r:  # noqa: S310
                    ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                    if ctype not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
                        continue
                    raw = r.read(MAX_PHOTO_BYTES + 1)
                if len(raw) > MAX_PHOTO_BYTES or len(raw) < 2000:
                    continue
                data = _process(raw, max_edge)
                if data is None:
                    continue
                _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(data)
            out.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                     "data": base64.b64encode(data).decode("ascii")}})
        except Exception:
            continue
    return out


def _process(raw: bytes, max_edge: int) -> bytes | None:
    """Downscale to <=max_edge on the long edge (never upscale), convert to
    RGB, and re-encode as JPEG q85. Returns None on any decode failure."""
    import io

    from PIL import Image  # lazy: heavy dep, only needed here

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        return None
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    long_edge = max(w, h)
    if long_edge > max_edge:
        scale = max_edge / long_edge
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()
