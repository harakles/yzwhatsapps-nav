"""Uygulama genelinde kullanılan veri modelleri."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Message:
    """Herhangi bir kaynaktan (WhatsApp Web, ekran, dışa aktarım) okunan tek mesaj."""

    chat: str
    msg_id: str
    sender: str = ""
    timestamp: str = ""
    text: str = ""
    image_path: Optional[Path] = None
    source: str = ""

    @property
    def has_image(self) -> bool:
        return self.image_path is not None and Path(self.image_path).exists()


@dataclass
class Match:
    """Kullanıcının kriterine uyan bir mesaj."""

    message: Message
    kind: str  # "metin" | "görsel" | "ekran"
    reason: str = ""
    image_description: str = ""
    ocr_text: str = ""
    extra: dict = field(default_factory=dict)

    def display_text(self) -> str:
        parts = []
        if self.message.text:
            parts.append(self.message.text)
        if self.ocr_text:
            parts.append(f"[Görseldeki yazı] {self.ocr_text}")
        if self.image_description:
            parts.append(f"[Görsel] {self.image_description}")
        return " | ".join(parts)
