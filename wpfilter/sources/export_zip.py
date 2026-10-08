"""WhatsApp "Sohbeti dışa aktar" (medya dahil) çıktısını okur.

Desteklenen girdiler:
* ``.zip``  – telefondan "Sohbeti dışa aktar → Medya dahil" ile alınan arşiv
* ``.txt``  – yalnız metin dışa aktarımı (aynı klasördeki görseller de bulunur)
* klasör   – zip'in açılmış hâli

Android (TR/EN) ve iOS satır biçimleri desteklenir.
"""
from __future__ import annotations

import re
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from ..imageutil import is_image_file
from ..models import Message

# 08.10.2026 14:05 - Ali: ...      | [08.10.2026 14:05:22] Ali: ...
# 10/8/26, 2:05 PM - Ali: ...      | 8.10.2026, 14:05 - Ali: ...
HEADER_RE = re.compile(
    r"^[‎‏]?\[?"
    r"(?P<date>\d{1,4}[./-]\d{1,2}[./-]\d{1,4})"
    r",?\s+"
    r"(?P<time>\d{1,2}[:.]\d{2}(?:[:.]\d{2})?(?:\s?[APap]\.?\s?[Mm]\.?)?)"
    r"\]?\s*(?:-|–)?\s*"
    r"(?P<rest>.*)$"
)
SENDER_RE = re.compile(r"^(?P<sender>[^:]{1,80}?):\s(?P<text>.*)$", re.S)
ATTACH_HINTS = re.compile(
    r"\((?:dosya ekli|file attached)\)|<(?:ekli|attached):\s*[^>]+>|‎", re.I
)


def _resolve_input(path: Path) -> tuple[Path, Path]:
    """(sohbet .txt dosyası, medya klasörü) döndürür; zip'i geçici klasöre açar."""
    if path.is_dir():
        txts = sorted(path.glob("*.txt"))
        if not txts:
            raise FileNotFoundError(f"{path} içinde .txt sohbet dosyası yok")
        return txts[0], path
    if path.suffix.lower() == ".zip":
        out = Path(tempfile.mkdtemp(prefix="wpfilter_zip_"))
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                target = (out / info.filename).resolve()
                if out.resolve() not in target.parents and target != out.resolve():
                    continue  # zip-slip koruması
                z.extract(info, out)
        txts = sorted(out.rglob("*.txt"), key=lambda p: (p.name != "_chat.txt", len(str(p))))
        if not txts:
            raise FileNotFoundError("Zip içinde sohbet .txt dosyası bulunamadı")
        return txts[0], txts[0].parent
    return path, path.parent


def _chat_name_from(path: Path, txt: Path) -> str:
    name = path.stem if path.suffix.lower() in (".zip", ".txt") else path.name
    for prefix in ("WhatsApp Chat with ", "WhatsApp Chat - ", "WhatsApp Sohbeti - ", "WhatsApp Sohbeti: "):
        if name.startswith(prefix):
            return name[len(prefix):]
    if name == "_chat":
        return txt.parent.name
    return name


def parse_export(path: str | Path, chat_name: Optional[str] = None) -> list[Message]:
    path = Path(path)
    txt, media_dir = _resolve_input(path)
    chat = chat_name or _chat_name_from(path, txt)

    media_files = {p.name: p for p in media_dir.rglob("*") if p.is_file() and is_image_file(p.name)}
    raw = txt.read_text(encoding="utf-8-sig", errors="replace")

    entries: list[dict] = []
    for line in raw.splitlines():
        m = HEADER_RE.match(line)
        if m:
            rest = m.group("rest")
            sm = SENDER_RE.match(rest)
            entries.append(
                {
                    "ts": f"{m.group('date')} {m.group('time')}",
                    "sender": sm.group("sender").strip() if sm else "",
                    "text": sm.group("text") if sm else rest,
                    "system": sm is None,
                }
            )
        elif entries:
            entries[-1]["text"] += "\n" + line

    messages: list[Message] = []
    for i, e in enumerate(entries):
        if e["system"]:
            continue  # "X gruba katıldı" gibi sistem mesajları
        text: str = e["text"]
        image: Optional[Path] = None
        for name, p in media_files.items():
            if name in text:
                image = p
                text = text.replace(name, "")
                break
        text = ATTACH_HINTS.sub("", text).strip()
        if text in ("<Medya dahil edilmedi>", "<Media omitted>", "görüntü dahil edilmedi", "image omitted"):
            text = ""
        if not text and image is None:
            continue
        messages.append(
            Message(
                chat=chat,
                msg_id=f"export-{i}",
                sender=e["sender"],
                timestamp=e["ts"],
                text=text,
                image_path=image,
                source="dışa aktarım",
            )
        )
    return messages
