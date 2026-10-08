"""Görsel yardımcıları: Claude'a göndermeden önce boyut küçültme ve normalize etme."""
from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageOps

# Claude için önerilen uzun kenar sınırı; daha büyük görseller zaten küçültülür.
MAX_EDGE = 1568
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def is_image_file(name: str) -> bool:
    return Path(name).suffix.lower() in IMAGE_EXTS


def file_hash(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_image(src: Path, dst: Path, max_edge: int = MAX_EDGE) -> Path:
    """Görseli JPEG'e çevirir, gerekirse küçültür ve `dst` yoluna yazar."""
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        if getattr(im, "is_animated", False):
            im.seek(0)
        im = im.convert("RGB")
        im.thumbnail((max_edge, max_edge))
        dst.parent.mkdir(parents=True, exist_ok=True)
        im.save(dst, "JPEG", quality=88)
    return dst
