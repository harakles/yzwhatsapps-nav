"""WhatsApp oturumundan (WhatsApp Web – bağlı cihaz) mesaj ve görsel okuma.

Windows'taki yeni WhatsApp Desktop uygulaması da WhatsApp Web altyapısını kullanır ve yerel
veritabanını şifreler; bu yüzden en güvenilir yol, aynı hesabı uygulamanın kendi tarayıcı
penceresine "bağlı cihaz" olarak eklemektir. İlk açılışta bir kez QR kod okutulur, oturum
`%LOCALAPPDATA%\\WhatsAppTabirTarayici\\wa_profile` içinde saklanır.

Tarayıcı olarak Windows'ta zaten kurulu olan Microsoft Edge kullanılır (Playwright).
Tüm Playwright çağrıları tek bir iş parçacığında yürütülür.
"""
from __future__ import annotations

import base64
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

from ..models import Message

ProgressFn = Callable[[str], None]

WA_URL = "https://web.whatsapp.com/"


def app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    d = Path(base) / "WhatsAppTabirTarayici"
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_name(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", s).strip("_")[:60] or "sohbet"


_DATE_FORMATS = ("%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d", "%d.%m.%y", "%d/%m/%y", "%m/%d/%y", "%Y/%m/%d")


def parse_date(s: str) -> Optional[date]:
    s = s.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


PRE_RE = re.compile(r"^\[(?P<time>[^,\]]+),\s*(?P<date>[^\]]+)\]\s*(?P<sender>.*?):\s*$")

# Ekranda yüklü mesajları sırasıyla döndüren JS.
SNAPSHOT_JS = r"""
() => {
  const main = document.querySelector('#main');
  if (!main) return [];
  const out = [];
  main.querySelectorAll('div[data-id]').forEach(el => {
    if (el.parentElement && el.parentElement.closest('div[data-id]')) return;
    const id = el.getAttribute('data-id');
    const preEl = el.querySelector('[data-pre-plain-text]');
    const pre = preEl ? preEl.getAttribute('data-pre-plain-text') : '';
    const spans = el.querySelectorAll('span.selectable-text');
    const text = Array.from(spans).map(s => s.innerText).join('\n');
    let img = null, best = 0;
    el.querySelectorAll('img').forEach(i => {
      const src = i.getAttribute('src') || '';
      if (!(src.startsWith('blob:') || src.startsWith('data:image'))) return;
      const w = i.naturalWidth || 0, h = i.naturalHeight || 0;
      if (w < 60 || h < 60) return;            // emoji / ikonları atla
      if (w * h > best) { best = w * h; img = src; }
    });
    const tm = (el.innerText || '').match(/(\d{1,2}:\d{2}(?:\s?[APap][Mm])?)\s*$/m);
    const hasDl = !!el.querySelector('[data-icon="media-download"], [data-icon="download"]');
    out.push({id, pre, text, img, time: tm ? tm[1] : '', hasDl});
  });
  return out;
}
"""

SCROLL_TOP_JS = r"""
() => {
  let el = document.querySelector('#main div[data-id]');
  while (el && el !== document.body) {
    const s = getComputedStyle(el);
    if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && el.scrollHeight > el.clientHeight) {
      el.scrollTop = 0;
      const r = el.getBoundingClientRect();
      return [r.left + r.width / 2, r.top + Math.min(r.height / 2, 200)];
    }
    el = el.parentElement;
  }
  return null;
}
"""

FETCH_BLOB_JS = r"""
async (src) => {
  const r = await fetch(src);
  const b = await r.blob();
  return await new Promise((res, rej) => {
    const fr = new FileReader();
    fr.onload = () => res(fr.result);
    fr.onerror = rej;
    fr.readAsDataURL(b);
  });
}
"""

CHAT_NAMES_JS = r"""
() => {
  const pane = document.querySelector('#pane-side');
  if (!pane) return [];
  const names = [];
  pane.querySelectorAll('[role="listitem"], [role="row"]').forEach(r => {
    const s = r.querySelector('span[title]');
    if (s && s.getAttribute('title')) names.push(s.getAttribute('title'));
  });
  return names;
}
"""

SCROLL_PANE_JS = r"""
(top) => {
  const pane = document.querySelector('#pane-side');
  if (!pane) return [0, 0];
  if (top === 0) pane.scrollTop = 0; else pane.scrollTop = pane.scrollTop + pane.clientHeight * 0.8;
  return [pane.scrollTop + pane.clientHeight, pane.scrollHeight];
}
"""


class WhatsAppWebSession:
    def __init__(self, profile_dir: Optional[Path] = None, log: ProgressFn = print):
        self.profile_dir = Path(profile_dir or app_data_dir() / "wa_profile")
        self.log = log
        self._exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="playwright")
        self._pw = None
        self._ctx = None
        self._page = None

    # Tüm Playwright işlemleri aynı iş parçacığında çalışmalı.
    def _call(self, fn, *args, **kwargs):
        return self._exec.submit(fn, *args, **kwargs).result()

    # ------------------------------------------------------------------ yaşam döngüsü
    def start(self, login_timeout: int = 300) -> None:
        self._call(self._start, login_timeout)

    def _start(self, login_timeout: int) -> None:
        if self._page is not None:
            return
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        last_err = None
        for channel in ("msedge", "chrome", None):
            try:
                kwargs = dict(user_data_dir=str(self.profile_dir), headless=False, viewport=None,
                              args=["--start-maximized"])
                if channel:
                    kwargs["channel"] = channel
                self._ctx = self._pw.chromium.launch_persistent_context(**kwargs)
                self.log(f"Tarayıcı açıldı ({channel or 'chromium'})")
                break
            except Exception as e:  # tarayıcı kurulu değilse sıradakini dene
                last_err = e
        if self._ctx is None:
            raise RuntimeError(
                "Edge/Chrome başlatılamadı. 'python -m playwright install chromium' çalıştırmayı deneyin.\n"
                f"{last_err}"
            )
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self._page.goto(WA_URL)
        self.log("WhatsApp açılıyor… İlk kullanımda telefondan QR kodu okutun "
                 "(WhatsApp → Ayarlar → Bağlı cihazlar → Cihaz bağla).")
        self._page.wait_for_selector("#pane-side", timeout=login_timeout * 1000)
        self.log("WhatsApp oturumu hazır.")

    def close(self) -> None:
        def _close():
            try:
                if self._ctx:
                    self._ctx.close()
                if self._pw:
                    self._pw.stop()
            finally:
                self._ctx = self._page = self._pw = None
        try:
            self._call(_close)
        finally:
            self._exec.shutdown(wait=False)

    @property
    def page(self):
        if self._page is None:
            raise RuntimeError("WhatsApp oturumu başlatılmadı.")
        return self._page

    # ------------------------------------------------------------------ sohbet listesi
    def list_chats(self, limit: int = 1000) -> list[str]:
        return self._call(self._list_chats, limit)

    def _list_chats(self, limit: int) -> list[str]:
        page = self.page
        seen: list[str] = []
        page.evaluate(SCROLL_PANE_JS, 0)
        page.wait_for_timeout(600)
        stale = 0
        while len(seen) < limit and stale < 3:
            before = len(seen)
            for n in page.evaluate(CHAT_NAMES_JS):
                if n not in seen:
                    seen.append(n)
            bottom, height = page.evaluate(SCROLL_PANE_JS, 1)
            page.wait_for_timeout(500)
            stale = stale + 1 if (len(seen) == before or bottom >= height) else 0
        page.evaluate(SCROLL_PANE_JS, 0)
        return seen[:limit]

    # ------------------------------------------------------------------ sohbet açma
    def _open_chat(self, name: str) -> None:
        page = self.page
        css_name = name.replace("\\", "\\\\").replace('"', '\\"')
        target = page.locator(f'#pane-side span[title="{css_name}"]').first
        if target.count() == 0:
            box = page.locator('#side [contenteditable="true"], #side input[type="text"]').first
            box.click()
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            page.keyboard.type(name, delay=20)
            page.wait_for_timeout(1500)
            target = page.locator(f'#pane-side span[title="{css_name}"]').first
        if target.count() == 0:
            raise RuntimeError(f"Sohbet bulunamadı: {name}")
        target.click()
        page.wait_for_selector("#main", timeout=15000)
        page.wait_for_timeout(1200)
        # Arama kutusunu temizle
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass

    # ------------------------------------------------------------------ mesaj toplama
    def collect(
        self,
        chat: str,
        media_dir: Path,
        max_messages: int = 500,
        since: Optional[date] = None,
        download_images: bool = True,
        cancel: Optional[threading.Event] = None,
    ) -> list[Message]:
        return self._call(self._collect, chat, Path(media_dir), max_messages, since, download_images,
                          cancel or threading.Event())

    def _collect(self, chat, media_dir: Path, max_messages, since, download_images, cancel) -> list[Message]:
        page = self.page
        self._open_chat(chat)
        chat_dir = media_dir / safe_name(chat)
        chat_dir.mkdir(parents=True, exist_ok=True)

        order: list[str] = []
        items: dict[str, dict] = {}
        stale_rounds = 0
        while not cancel.is_set():
            snap = page.evaluate(SNAPSHOT_JS)
            new_ids = []
            for it in snap:
                if it["id"] in items:
                    # görsel sonradan yüklendiyse güncelle
                    if it.get("img") and not items[it["id"]].get("img_path") and download_images:
                        self._save_image(it, items[it["id"]], chat_dir)
                    continue
                items[it["id"]] = it
                new_ids.append(it["id"])
                if download_images and it.get("img"):
                    self._save_image(it, it, chat_dir)
            order = new_ids + order  # yukarı kaydırdıkça daha eski mesajlar gelir

            if len(order) >= max_messages:
                break
            oldest = self._first_date(order, items)
            if since and oldest and oldest < since:
                break
            stale_rounds = stale_rounds + 1 if not new_ids else 0
            if stale_rounds >= 3:
                break
            self._click_load_older(page)
            pos = page.evaluate(SCROLL_TOP_JS)
            if not pos:
                break
            # Kaydırma zaten en üstteyse WhatsApp yeni yükleme yapmaz; fare tekerleğiyle tetikle.
            page.mouse.move(pos[0], pos[1])
            if stale_rounds:
                page.mouse.wheel(0, 600)
                page.wait_for_timeout(300)
            page.mouse.wheel(0, -2500)
            page.wait_for_timeout(1500 if stale_rounds == 0 else 2500)
            self.log(f"{chat}: {len(order)} mesaj yüklendi…")

        return self._to_messages(chat, order[-max_messages:], items, since)

    @staticmethod
    def _click_load_older(page) -> None:
        for pattern in ("eski mesaj", "older messages", "Daha eski", "Load earlier"):
            btn = page.locator("#main button, #main div[role='button']").filter(has_text=re.compile(pattern, re.I))
            try:
                if btn.count():
                    btn.first.click(timeout=1000)
                    page.wait_for_timeout(1500)
                    return
            except Exception:
                pass

    def _save_image(self, it: dict, target: dict, chat_dir: Path) -> None:
        src = it.get("img") or ""
        try:
            if src.startswith("blob:"):
                data_url = self.page.evaluate(FETCH_BLOB_JS, src)
            else:
                data_url = src
            header, b64 = data_url.split(",", 1)
            ext = ".png" if "png" in header else ".webp" if "webp" in header else ".jpg"
            path = chat_dir / f"{safe_name(it['id'])}{ext}"
            path.write_bytes(base64.b64decode(b64))
            target["img_path"] = str(path)
            target["img_lowres"] = not src.startswith("blob:")
        except Exception as e:
            self.log(f"Görsel indirilemedi ({it.get('id')}): {e}")

    @staticmethod
    def _first_date(order: list[str], items: dict) -> Optional[date]:
        for mid in order:
            m = PRE_RE.match(items[mid].get("pre") or "")
            if m:
                return parse_date(m.group("date"))
        return None

    @staticmethod
    def _to_messages(chat: str, order: list[str], items: dict, since: Optional[date]) -> list[Message]:
        out: list[Message] = []
        last_date = ""
        for mid in order:
            it = items[mid]
            m = PRE_RE.match(it.get("pre") or "")
            if m:
                last_date = m.group("date").strip()
                ts = f"{last_date} {m.group('time').strip()}"
                sender = m.group("sender").strip()
            else:
                ts = f"{last_date} {it.get('time', '')}".strip()
                sender = "Ben" if mid.startswith("true_") else ""
            d = parse_date(last_date) if last_date else None
            if since and d and d < since:
                continue
            img = it.get("img_path")
            if not it.get("text") and not img:
                continue
            out.append(
                Message(
                    chat=chat,
                    msg_id=mid,
                    sender=sender,
                    timestamp=ts,
                    text=it.get("text") or "",
                    image_path=Path(img) if img else None,
                    source="whatsapp",
                )
            )
        return out
