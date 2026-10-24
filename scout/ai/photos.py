"""Photo download + downscale for AI vision calls, with a disk cache.

Split out of scout.ai.assess so a shared, cheaper image pipeline can be reused
across callers. Downloads follow the same rules as the original photo_blocks:
urllib, 8s timeout, <=4MB, image content-type only, skip on any failure. The
processed JPEG is cached on disk keyed by the source URL's sha1, which also
means a photo survives its CDN link expiring after the first successful
fetch."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from scout.config import DATA_DIR

MAX_PHOTOS = 12
MAX_PHOTO_BYTES = 4_000_000
_CACHE_DIR = DATA_DIR / "photo_cache"


# Facebook re-signs the same image with a new expiring query on every page load
# (oe=...): key it by the file name in the path so a re-sync finds the saved copy.
_FB_NAME_RE = re.compile(r"fbcdn\.net/.*/(\d+_\d+_\d+_n)\.(?:jpg|jpeg|png|webp)", re.I)


# CARFAX's image CDN serves only fixed sizes (1024x768 the largest) and pads a smaller
# photo onto a white canvas rather than enlarging it: #516's are 640x360 inside 1024x768
# (2026-10-23; other sizes return 403). Saved copies are cropped to the photo itself.
_CARFAX_CDN_RE = re.compile(r"^https?://carfax-img\.vast\.com/", re.I)
_WHITE_TOLERANCE = 18      # per-channel distance from pure white still counted as padding
_MIN_CONTENT_SHARE = 0.2   # never crop to less than this share of the image


def cache_key(url: str) -> str:
    m = _FB_NAME_RE.search(url or "")
    return f"fb_{m.group(1)}" if m else hashlib.sha1((url or "").encode("utf-8")).hexdigest()


def cached_path(url: str) -> Path | None:
    """The saved copy of this photo, if any (also under the pre-2026-10-04 full-URL key)."""
    for key in {cache_key(url), hashlib.sha1((url or "").encode("utf-8")).hexdigest()}:
        p = _CACHE_DIR / f"{key}.jpg"
        if p.exists():
            return p
    return None


def fetch_cached(url: str, max_edge: int = 1568) -> bytes | None:
    """The photo as a processed JPEG: the saved copy, or downloaded and saved now.
    None on any failure (expired CDN link, hotlink block, huge file, bad data)."""
    import urllib.request
    try:
        p = cached_path(url)
        if p:
            return p.read_bytes()
        if not re.match(r"https?://", url or ""):
            return None
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) hoopty-scout/0.2", "Accept": "image/*"})
        with urllib.request.urlopen(req, timeout=8) as r:  # noqa: S310
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
                return None
            raw = r.read(MAX_PHOTO_BYTES + 1)
        if len(raw) > MAX_PHOTO_BYTES or len(raw) < 2000:
            return None
        data = _process(raw, max_edge, trim=bool(_CARFAX_CDN_RE.match(url)))
        if data is None:
            return None
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / f"{cache_key(url)}.jpg").write_bytes(data)
        return data
    except Exception:
        return None


def thumbnail(url: str, max_edge: int = 640) -> bytes | None:
    """A small JPEG of a saved photo (for the published board's cards), or None."""
    p = cached_path(url)
    return _process(p.read_bytes(), max_edge) if p else None


def photo_blocks(urls: list[str], limit: int = MAX_PHOTOS, max_edge: int = 1568) -> list[dict[str, Any]]:
    """Each photo (saved copy first, so an expired CDN link still works once it has
    been fetched) downscaled to max_edge, as base64 image blocks. Failures are skipped."""
    import base64

    out: list[dict[str, Any]] = []
    for url in urls:
        if len(out) >= limit:
            break
        data = fetch_cached(url, max_edge)
        if data is not None:
            out.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                     "data": base64.b64encode(data).decode("ascii")}})
    return out


def trim_saved(urls: list[str]) -> int:
    """Crop the white padding out of CARFAX photos already saved (before 2026-10-23 they
    were stored padded). Rewrites only copies that have padding; returns how many."""
    done = 0
    for url in urls:
        p = cached_path(url) if _CARFAX_CDN_RE.match(url or "") else None
        if not p:
            continue
        try:
            raw = p.read_bytes()
            data = _process(raw, 10**6, trim=True)
            if data is not None and _size(data) != _size(raw):   # same size: no padding, leave it
                p.write_bytes(data)
                done += 1
        except Exception:
            continue
    return done


def _size(raw: bytes) -> tuple[int, int]:
    import io

    from PIL import Image
    return Image.open(io.BytesIO(raw)).size


def _trim_white(img):
    """The image cropped to its non-white content, or unchanged when there's no white
    border (or what's left would be implausibly small)."""
    from PIL import Image, ImageChops
    diff = ImageChops.difference(img, Image.new("RGB", img.size, (255, 255, 255))).convert("L")
    box = diff.point(lambda v: 255 if v > _WHITE_TOLERANCE else 0).getbbox()
    if not box or box == (0, 0, *img.size):
        return img
    l, t, r, b = box
    if (r - l) * (b - t) < _MIN_CONTENT_SHARE * img.size[0] * img.size[1]:
        return img
    return img.crop(box)


def _process(raw: bytes, max_edge: int, trim: bool = False) -> bytes | None:
    """Downscale to <=max_edge on the long edge (never upscale), convert to
    RGB, and re-encode as JPEG q85. Returns None on any decode failure.
    trim=True first crops a white border (CARFAX's padded photos)."""
    import io

    from PIL import Image  # lazy: heavy dep, only needed here

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        return None
    if img.mode != "RGB":
        img = img.convert("RGB")
    if trim:
        img = _trim_white(img)
    w, h = img.size
    long_edge = max(w, h)
    if long_edge > max_edge:
        scale = max_edge / long_edge
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()
