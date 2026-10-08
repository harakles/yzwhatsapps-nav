"""Sonuçları CSV (Excel uyumlu) ve görselli HTML rapor olarak kaydetme."""
from __future__ import annotations

import base64
import csv
import html
import io
from datetime import datetime
from pathlib import Path

from PIL import Image

from .models import Match

COLUMNS = ["Sohbet", "Tarih", "Gönderen", "Tür", "Mesaj / Görsel içeriği", "Gerekçe", "Görsel dosyası"]


def match_row(m: Match) -> list[str]:
    msg = m.message
    return [
        msg.chat,
        msg.timestamp,
        msg.sender,
        m.kind,
        m.display_text(),
        m.reason,
        str(msg.image_path) if msg.image_path else "",
    ]


def save_csv(matches: list[Match], path: Path) -> None:
    # utf-8-sig + ';' => Türkçe Excel'de doğrudan açılır
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(COLUMNS)
        for m in matches:
            w.writerow(match_row(m))


def _thumb_data_uri(path: Path, edge: int = 320) -> str:
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((edge, edge))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=80)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return ""


def save_html(matches: list[Match], path: Path, criterion: str) -> None:
    rows = []
    for m in matches:
        msg = m.message
        img = ""
        if msg.image_path and Path(msg.image_path).exists():
            uri = _thumb_data_uri(Path(msg.image_path))
            if uri:
                img = f'<img src="{uri}" alt="görsel">'
        rows.append(
            "<tr>"
            f"<td>{html.escape(msg.chat)}</td>"
            f"<td class=nowrap>{html.escape(msg.timestamp)}</td>"
            f"<td>{html.escape(msg.sender)}</td>"
            f"<td>{html.escape(m.kind)}</td>"
            f"<td>{html.escape(m.display_text()).replace(chr(10), '<br>')}</td>"
            f"<td>{html.escape(m.reason)}</td>"
            f"<td>{img}</td>"
            "</tr>"
        )
    doc = f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8">
<title>WhatsApp Tabir Raporu</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#111;background:#fff}}
h1{{font-size:20px;margin:0 0 4px}} .meta{{color:#555;margin-bottom:16px}}
table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid #ddd;padding:6px 8px;vertical-align:top;font-size:14px}}
th{{background:#075e54;color:#fff;text-align:left;position:sticky;top:0}}
tr:nth-child(even){{background:#f6f6f6}} img{{max-width:240px;border-radius:4px}} .nowrap{{white-space:nowrap}}
</style></head><body>
<h1>WhatsApp Tabir Raporu</h1>
<div class=meta>Aranan: <b>{html.escape(criterion)}</b> · {len(matches)} sonuç ·
{datetime.now().strftime('%d.%m.%Y %H:%M')}</div>
<table><thead><tr>{''.join(f'<th>{c}</th>' for c in COLUMNS[:-1])}<th>Görsel</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
</body></html>"""
    Path(path).write_text(doc, encoding="utf-8")
