import json
import zipfile
from pathlib import Path

from PIL import Image

from wpfilter.claude_backend import ClaudeBackend, extract_json
from wpfilter.matcher import MatchOptions, MessageMatcher, keyword_hit, normalize_tr
from wpfilter.report import save_csv, save_html
from wpfilter.sources.export_zip import parse_export
from wpfilter.sources.whatsapp_web import PRE_RE, parse_date

CHAT_ANDROID = """08.10.2026 14:05 - Mesajlar ve aramalar uçtan uca şifrelidir.
08.10.2026 14:05 - Ali Veli: Kira ödemesini yaptım
08.10.2026 14:06 - Ayşe: tamam, dekontu atar mısın
ikinci satır
08.10.2026 14:07 - Ali Veli: IMG-20261008-WA0001.jpg (dosya ekli)
dekont burada
08.10.2026 14:08 - Ayşe: <Medya dahil edilmedi>
"""

CHAT_IOS = """[08.10.2026 14:05:22] Grup: Mesajlar şifrelidir.
[08.10.2026 14:05:30] Mehmet: FATURA geldi mi?
‎[08.10.2026 14:06:01] Mehmet: ‎<ekli: 00000012-PHOTO-2026-10-08-14-06-01.jpg>
"""


def _make_zip(tmp: Path, name: str, chat: str, images: list[str]) -> Path:
    z = tmp / name
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("_chat.txt", chat)
        for img in images:
            p = tmp / img
            Image.new("RGB", (200, 120), "white").save(p)
            zf.write(p, img)
    return z


def test_parse_android(tmp_path):
    z = _make_zip(tmp_path, "WhatsApp Sohbeti - Ev.zip", CHAT_ANDROID, ["IMG-20261008-WA0001.jpg"])
    msgs = parse_export(z)
    assert [m.sender for m in msgs] == ["Ali Veli", "Ayşe", "Ali Veli"]
    assert msgs[0].chat == "Ev"
    assert msgs[1].text == "tamam, dekontu atar mısın\nikinci satır"
    assert msgs[2].has_image and msgs[2].text == "dekont burada"


def test_parse_ios(tmp_path):
    z = _make_zip(tmp_path, "WhatsApp Chat - İş.zip", CHAT_IOS, ["00000012-PHOTO-2026-10-08-14-06-01.jpg"])
    msgs = parse_export(z)
    # iOS'ta sistem satırı grup adıyla gelir, normal mesajdan ayırt edilemez (zararsız).
    msgs = msgs[-2:]
    assert msgs[0].sender == "Mehmet" and msgs[0].timestamp == "08.10.2026 14:05:30"
    assert msgs[1].has_image and msgs[1].text == ""


def test_keyword_turkish():
    assert normalize_tr("ÖDEME İŞLEMİ") == "odeme islemi"
    assert keyword_hit("Kira odemesini yaptim", "ödeme, fatura")
    assert not keyword_hit("selam", "ödeme")


class FakeBackend(ClaudeBackend):
    def __init__(self):
        self.calls = []

    def ask(self, prompt, images=(), workdir=None):
        self.calls.append((prompt, [Path(i) for i in images]))
        if images:
            for i in images:
                assert Path(i).exists()
            return json.dumps({"images": [
                {"file": Path(i).name, "match": True, "reason": "dekont", "description": "banka dekontu",
                 "ocr_text": "KIRA 15.000 TL"} for i in images]})
        ids = [json.loads(line)["id"] for line in prompt.splitlines() if line.startswith('{"id"')]
        hits = [i for i in ids if "kira" in prompt.split(f'"id": "{i}"')[1].split("\n")[0].lower()]
        return "```json\n" + json.dumps({"matches": [{"id": i, "reason": "kira"} for i in hits]}) + "\n```"


def test_matcher_semantic_with_images(tmp_path):
    z = _make_zip(tmp_path, "Ev.zip", CHAT_ANDROID, ["IMG-20261008-WA0001.jpg"])
    msgs = parse_export(z)
    found = []
    backend = FakeBackend()
    MessageMatcher(backend, MatchOptions("kira ödemesi"), on_match=found.append, workdir=tmp_path / "ai").run(msgs)
    kinds = sorted(m.kind for m in found)
    assert kinds == ["görsel", "metin"]
    img = next(m for m in found if m.kind == "görsel")
    assert img.ocr_text == "KIRA 15.000 TL"
    # Görsel normalleştirilmiş JPEG olarak gönderilmeli
    assert backend.calls[-1][1][0].suffix == ".jpg"

    save_csv(found, tmp_path / "r.csv")
    save_html(found, tmp_path / "r.html", "kira")
    assert "data:image/jpeg;base64" in (tmp_path / "r.html").read_text(encoding="utf-8")
    assert (tmp_path / "r.csv").read_text(encoding="utf-8-sig").startswith("Sohbet;")


def test_matcher_keyword_no_backend(tmp_path):
    z = _make_zip(tmp_path, "Ev.zip", CHAT_ANDROID, ["IMG-20261008-WA0001.jpg"])
    found = []
    MessageMatcher(None, MatchOptions("dekont", mode="keyword", scan_images=False),
                   on_match=found.append, workdir=tmp_path / "ai").run(parse_export(z))
    assert len(found) == 2  # metin + görsel altyazısı


def test_extract_json():
    assert extract_json('Sonuç:\n{"matches": []}\nbitti') == {"matches": []}


def test_web_pre_text():
    m = PRE_RE.match("[14:05, 08.10.2026] Ali Veli: ")
    assert m and m.group("sender") == "Ali Veli" and parse_date(m.group("date")).day == 8
    m = PRE_RE.match("[2:05 PM, 10/8/2026] Bob: ")
    assert m and m.group("time") == "2:05 PM"
