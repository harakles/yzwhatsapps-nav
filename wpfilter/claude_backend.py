"""Claude ile konuşan arka uçlar.

İki seçenek vardır:

* ``ClaudeCodeBackend`` – **Claude aboneliği (Pro / Max)** ile çalışır. Bilgisayarda
  kurulu Claude Code CLI'ını (``claude``) başsız modda (``claude -p``) çağırır. Bir kez
  ``claude`` komutuyla abonelik hesabınıza giriş yapmanız yeterlidir; API anahtarı
  gerekmez, kullanım aboneliğinizin limitlerinden düşer.
* ``AnthropicApiBackend`` – İsteğe bağlı; Anthropic API anahtarı ile çalışır.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

DEFAULT_MODEL = "claude-opus-5-5"
MODEL_CHOICES = [
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
    "claude-fable-5-1",
]
# Sunucu taraflı "refusal fallback" desteği olan modeller (yalnızca API arka ucu).
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-fable-5-1", "claude-opus-5", "claude-sonnet-5-5"}


class ClaudeError(RuntimeError):
    pass


def extract_json(text: str) -> dict:
    """Model cevabından ilk geçerli JSON nesnesini çıkarır (```json bloklarını da tolere eder)."""
    if not text:
        raise ClaudeError("Claude boş cevap döndürdü.")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = []
    if fenced:
        candidates.append(fenced.group(1))
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            continue
    raise ClaudeError(f"Claude cevabı JSON olarak çözülemedi: {text[:300]}")


class ClaudeBackend:
    name = "base"

    def ask(self, prompt: str, images: Sequence[Path] = (), workdir: Optional[Path] = None) -> str:
        raise NotImplementedError

    def ask_json(self, prompt: str, images: Sequence[Path] = (), workdir: Optional[Path] = None) -> dict:
        last_err: Exception | None = None
        for _ in range(2):  # bir kez yeniden dene
            try:
                return extract_json(self.ask(prompt, images, workdir))
            except ClaudeError as e:
                last_err = e
        raise last_err  # type: ignore[misc]

    def check(self) -> str:
        """Arka ucun çalıştığını doğrular; kullanıcıya gösterilecek kısa durum metni döner."""
        raise NotImplementedError


# --------------------------------------------------------------------------- CLI (abonelik)


def find_claude_cli() -> Optional[str]:
    found = shutil.which("claude")
    if found:
        return found
    home = Path.home()
    candidates = [
        home / ".local" / "bin" / "claude.exe",
        home / ".local" / "bin" / "claude",
        Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "claude" / "claude.exe",
    ]
    for c in candidates:
        if str(c) and c.exists():
            return str(c)
    return None


class ClaudeCodeBackend(ClaudeBackend):
    """Claude Code CLI'ı üzerinden abonelikle çalışan arka uç."""

    name = "Claude aboneliği (Claude Code)"

    def __init__(self, model: str = DEFAULT_MODEL, cli_path: Optional[str] = None, timeout: int = 600):
        self.model = model
        self.cli_path = cli_path or find_claude_cli()
        self.timeout = timeout

    def _env(self) -> dict:
        env = dict(os.environ)
        # API anahtarı ortamda varsa Claude Code onu kullanır ve API'den ücretlendirir.
        # Abonelik kullanılsın diye alt sürece aktarmıyoruz.
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
        return env

    def _run(self, args: list[str], stdin: str, cwd: Optional[Path]) -> subprocess.CompletedProcess:
        if not self.cli_path:
            raise ClaudeError(
                "Claude Code (claude) bulunamadı. Kurulum: PowerShell'de\n"
                "  irm https://claude.ai/install.ps1 | iex\n"
                "ardından bir kez 'claude' yazıp abonelik hesabınızla giriş yapın."
            )
        flags = 0
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            return subprocess.run(
                [self.cli_path, *args],
                input=stdin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(cwd) if cwd else None,
                env=self._env(),
                timeout=self.timeout,
                creationflags=flags,
            )
        except subprocess.TimeoutExpired as e:
            raise ClaudeError(f"Claude {self.timeout} sn içinde cevap vermedi.") from e

    def ask(self, prompt: str, images: Sequence[Path] = (), workdir: Optional[Path] = None) -> str:
        images = [Path(p) for p in images]
        if images:
            workdir = workdir or images[0].parent
            listing = "\n".join(f"- {p.name}" for p in images)
            prompt = (
                "Önce Read aracıyla çalışma klasöründeki şu görsel dosyalarının HER BİRİNİ aç ve incele "
                "(başka dosya okuma, başka araç kullanma):\n"
                f"{listing}\n\n{prompt}"
            )
        args = [
            "-p",
            "--output-format", "json",
            "--model", self.model,
            "--allowedTools", "Read",
            "--max-turns", str(len(images) + 4),
        ]
        proc = self._run(args, prompt, workdir)
        out = (proc.stdout or "").strip()
        if not out:
            raise ClaudeError(f"Claude Code hata verdi (kod {proc.returncode}): {(proc.stderr or '')[:500]}")
        try:
            data = json.loads(out.splitlines()[-1]) if not out.startswith("{") else json.loads(out)
        except json.JSONDecodeError:
            return out  # düz metin döndüyse olduğu gibi kullan
        if data.get("is_error"):
            raise ClaudeError(f"Claude Code hatası: {data.get('result') or data}")
        return str(data.get("result", ""))

    def check(self) -> str:
        if not self.cli_path:
            self._run([], "", None)  # anlaşılır hata fırlatır
        proc = self._run(["--version"], "", None)
        version = (proc.stdout or proc.stderr).strip()
        reply = self.ask('Sadece şu JSON\'u döndür: {"ok": true}')
        extract_json(reply)
        return f"Claude Code hazır ({version}), model: {self.model}"


# --------------------------------------------------------------------------- API anahtarı


def _media_type(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(ext, "image/jpeg")


class AnthropicApiBackend(ClaudeBackend):
    """Anthropic API anahtarı ile çalışan arka uç (isteğe bağlı)."""

    name = "Anthropic API anahtarı"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        import anthropic  # yalnızca bu arka uç seçilirse gerekir

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key or None, max_retries=3)
        self.model = model

    def ask(self, prompt: str, images: Sequence[Path] = (), workdir: Optional[Path] = None) -> str:
        content: list[dict] = []
        for p in images:
            p = Path(p)
            content.append({"type": "text", "text": f"Dosya adı: {p.name}"})
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": _media_type(p),
                        "data": base64.standard_b64encode(p.read_bytes()).decode("ascii"),
                    },
                }
            )
        content.append({"type": "text", "text": prompt})

        kwargs: dict = dict(
            model=self.model,
            max_tokens=16000,
            messages=[{"role": "user", "content": content}],
        )
        extra_body: dict = {"output_config": {"effort": "medium"}}
        if self.model in _FALLBACK_MODELS:
            # Güvenlik sınıflandırıcısı reddederse sunucu otomatik olarak uygun modele düşer.
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            extra_body["fallbacks"] = "default"
        kwargs["extra_body"] = extra_body

        try:
            resp = self.client.beta.messages.create(**kwargs)
        except self._anthropic.AuthenticationError as e:
            raise ClaudeError("API anahtarı geçersiz.") from e
        except self._anthropic.RateLimitError as e:
            raise ClaudeError("API hız sınırına takıldı, biraz sonra tekrar deneyin.") from e
        except self._anthropic.APIStatusError as e:
            raise ClaudeError(f"API hatası ({e.status_code}): {e.message}") from e
        except self._anthropic.APIConnectionError as e:
            raise ClaudeError("Anthropic API'ye bağlanılamadı.") from e

        if resp.stop_reason == "refusal":
            raise ClaudeError("Claude bu isteği yanıtlamayı reddetti.")
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")

    def check(self) -> str:
        extract_json(self.ask('Sadece şu JSON\'u döndür: {"ok": true}'))
        return f"Anthropic API hazır, model: {self.model}"


def make_backend(kind: str, model: str, api_key: str = "") -> ClaudeBackend:
    if kind == "api":
        return AnthropicApiBackend(api_key=api_key, model=model)
    return ClaudeCodeBackend(model=model)
