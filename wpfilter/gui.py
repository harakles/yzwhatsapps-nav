"""Modern Windows masaüstü arayüzü (Tkinter + Sun Valley / Windows 11 teması)."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from . import APP_NAME, __version__
from .claude_backend import DEFAULT_MODEL, MODEL_CHOICES, make_backend
from .matcher import MatchOptions, MessageMatcher, normalize_tr
from .models import Match
from .report import save_csv, save_html
from .sources.export_zip import parse_export
from .sources.whatsapp_web import WhatsAppWebSession, app_data_dir, parse_date, safe_name

try:  # Windows 11 görünümlü tema (açık / koyu)
    import sv_ttk
except ImportError:  # tema yoksa standart ttk ile çalışmaya devam et
    sv_ttk = None

BACKENDS = {
    "Claude aboneliği (Claude Code)": "cli",
    "Anthropic API anahtarı": "api",
}
SOURCES = [("web", "WhatsApp"), ("screen", "Desktop ekranı"), ("export", "Dışa aktarım")]
EXAMPLES = ["fatura", "kira ödemesi ve dekontlar", "plaka görünen fotoğraflar", "toplantı saati değişikliği"]
SETTINGS_FILE = app_data_dir() / "ayarlar.json"
SIDEBAR_W = 470
WRAP = SIDEBAR_W - 90

BRAND = "#25d366"  # WhatsApp yeşili (yalnızca vurgu olarak)
PALETTE = {
    "dark": {
        "bg": "#1c1c1c", "surface": "#272727", "fg": "#fafafa", "muted": "#a3a3a3",
        "border": "#3a3a3a", "accent": "#57c8ff", "odd": "#232323", "select": "#2f60d8",
        "ok": "#6ccb5f", "warn": "#fce100", "err": "#ff99a4", "chip_text": "#7ee2a8", "chip_img": "#9fc9ff",
    },
    "light": {
        "bg": "#fafafa", "surface": "#ffffff", "fg": "#1c1c1c", "muted": "#5f5f5f",
        "border": "#e0e0e0", "accent": "#005fb8", "odd": "#f3f3f3", "select": "#2f60d8",
        "ok": "#0f7b0f", "warn": "#9d5d00", "err": "#c42b1c", "chip_text": "#0f7b45", "chip_img": "#005fb8",
    },
}


def open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _font(name: str, fallback: tuple) -> object:
    """sv-ttk'nın Segoe UI Variable fontlarını, yoksa yedek fontu döndürür."""
    return name if sv_ttk is not None else fallback


# ====================================================================== küçük bileşenler
class PlaceholderEntry(ttk.Entry):
    """İçi boşken gri ipucu metni gösteren giriş kutusu."""

    def __init__(self, master, placeholder: str, textvariable: tk.StringVar, **kw):
        super().__init__(master, textvariable=textvariable, **kw)
        self._ph = placeholder
        self._var = textvariable
        self._showing = False
        self.bind("<FocusIn>", self._hide)
        self.bind("<FocusOut>", self._show)
        self._show()

    def _show(self, _e=None):
        if not self._var.get():
            self._showing = True
            self.configure(foreground="gray")
            self.insert(0, self._ph)

    def _hide(self, _e=None):
        if self._showing:
            self.delete(0, "end")
            self.configure(foreground="")
            self._showing = False

    def value(self) -> str:
        return "" if self._showing else self._var.get()


class ScrollFrame(ttk.Frame):
    """Dikey kaydırılabilir kapsayıcı (küçük ekranlarda yan panel için)."""

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, width=kw.get("width", 400))
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vbar.pack(side="right", fill="y")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.inner.bind("<Enter>", lambda e: self.bind_all("<MouseWheel>", self._wheel))
        self.inner.bind("<Leave>", lambda e: self.unbind_all("<MouseWheel>"))

    def _wheel(self, e):
        if self.inner.winfo_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-e.delta / 120), "units")


# ====================================================================== uygulama
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1440x900")
        self.minsize(1120, 720)

        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.cancel = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.session: Optional[WhatsAppWebSession] = None
        self.matches: list[Match] = []
        self.all_chats: list[str] = []
        self.selected_chats: set[str] = set()
        self.export_files: list[Path] = []
        self._thumb = None
        self._sort_rev = False

        self.settings = self._read_settings()
        self.theme = self.settings.get("theme", "dark")
        self._apply_theme(self.theme, initial=True)
        self._set_icon()
        self._build()
        self._load_settings()
        self._apply_widget_colors()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)

    # ================================================================== tema
    @property
    def c(self) -> dict:
        return PALETTE[self.theme]

    def _apply_theme(self, theme: str, initial: bool = False) -> None:
        self.theme = theme
        style = ttk.Style(self)
        if sv_ttk is not None:
            sv_ttk.set_theme(theme, self)
        else:
            style.theme_use("clam")
            style.configure(".", background=self.c["bg"], foreground=self.c["fg"])
        c = self.c
        self.configure(background=c["bg"])
        style.configure("Treeview", rowheight=32)
        style.configure("Chats.Treeview", rowheight=28)
        style.configure("Muted.TLabel", foreground=c["muted"])
        style.configure("Caption.TLabel", foreground=c["muted"], font=_font("SunValleyCaptionFont", ("Segoe UI", 9)))
        style.configure("Title.TLabel", font=_font("SunValleySubtitleFont", ("Segoe UI Semibold", 15)))
        style.configure("Section.TLabel", font=_font("SunValleyBodyStrongFont", ("Segoe UI Semibold", 11)))
        style.configure("StatNum.TLabel", font=_font("SunValleyTitleFont", ("Segoe UI Semibold", 20)))
        style.configure("Big.Accent.TButton", padding=(12, 9),
                        font=_font("SunValleyBodyStrongFont", ("Segoe UI Semibold", 11)))
        style.configure("Chip.TButton", padding=(8, 2), font=_font("SunValleyCaptionFont", ("Segoe UI", 9)))
        style.configure("Thin.Horizontal.TProgressbar", thickness=4)
        for tv in ("Treeview", "Chats.Treeview"):
            style.map(tv, background=[("selected", c["select"])], foreground=[("selected", "#ffffff")])
        if not initial:
            self._apply_widget_colors()
        self._dark_titlebar(theme == "dark")

    def _apply_widget_colors(self) -> None:
        """ttk dışı (tk) bileşenleri ve tablo satır renklerini temaya uydurur."""
        c = self.c
        for t in (self.criterion, self.screen_chats, self.detail_text, self.log_text):
            t.configure(background=c["surface"], foreground=c["fg"], insertbackground=c["fg"],
                        selectbackground=c["select"], selectforeground="#ffffff", relief="flat",
                        highlightthickness=1, highlightbackground=c["border"], highlightcolor=c["accent"],
                        padx=8, pady=6)
        self.sidebar.canvas.configure(background=c["bg"])
        self.empty_label.configure(background=c["surface"] if sv_ttk else c["bg"])
        self.tree.tag_configure("odd", background=c["odd"])
        self.tree.tag_configure("even", background=c["surface"])
        self.tree.tag_configure("görsel", foreground=c["chip_img"])
        self.log_text.tag_configure("err", foreground=c["err"])
        self.log_text.tag_configure("time", foreground=c["muted"])
        self._set_claude_status(*getattr(self, "_claude_state", ("idle", "Claude: test edilmedi")))

    def _toggle_theme(self) -> None:
        self._apply_theme("dark" if self.dark_var.get() else "light")

    def _dark_titlebar(self, dark: bool) -> None:
        if sys.platform != "win32":
            return
        try:
            import ctypes

            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            value = ctypes.c_int(1 if dark else 0)
            for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (yeni / eski Windows 10)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), 4) == 0:
                    break
        except Exception:
            pass

    def _set_icon(self) -> None:
        try:
            from PIL import Image, ImageDraw, ImageTk

            im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            d.rounded_rectangle((2, 2, 62, 62), radius=16, fill=BRAND)
            d.ellipse((14, 12, 42, 40), outline="white", width=5)
            d.line((38, 36, 52, 50), fill="white", width=7)
            self._icon = ImageTk.PhotoImage(im)
            self._icon_small = ImageTk.PhotoImage(im.resize((36, 36), Image.LANCZOS))
            self.iconphoto(True, self._icon)
        except Exception:
            pass

    # ================================================================== yerleşim
    def _build(self) -> None:
        # ---- Üst çubuk
        header = ttk.Frame(self, padding=(20, 14, 20, 10))
        header.pack(fill="x")
        brand = ttk.Frame(header)
        brand.pack(side="left")
        ttk.Label(brand, image=getattr(self, "_icon_small", None)).pack(side="left", padx=(0, 12))
        titles = ttk.Frame(brand)
        titles.pack(side="left")
        ttk.Label(titles, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(titles, text="WhatsApp sohbetlerinizde mesaj ve görselleri Claude ile tarayın",
                  style="Caption.TLabel").pack(anchor="w")

        right = ttk.Frame(header)
        right.pack(side="right")
        self.dark_var = tk.BooleanVar(value=self.theme == "dark")
        ttk.Checkbutton(right, text="Koyu tema", style="Switch.TCheckbutton", variable=self.dark_var,
                        command=self._toggle_theme).pack(side="right", padx=(16, 0))
        self.claude_status = ttk.Label(right, text="", style="Muted.TLabel")
        self.claude_status.pack(side="right")
        ttk.Separator(self).pack(fill="x")

        # ---- Alt çubuk (ilerleme + durum)
        footer = ttk.Frame(self, padding=(20, 6, 20, 10))
        footer.pack(side="bottom", fill="x")
        self.progress = ttk.Progressbar(footer, mode="determinate", maximum=1.0,
                                        style="Thin.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(0, 6))
        self.status = tk.StringVar(value="Hazır.")
        ttk.Label(footer, textvariable=self.status, style="Caption.TLabel").pack(side="left")
        ttk.Label(footer, text=f"v{__version__}", style="Caption.TLabel").pack(side="right")
        ttk.Separator(self).pack(side="bottom", fill="x")

        # ---- Gövde: sol panel + sonuç alanı
        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True)
        self.sidebar = ScrollFrame(body, width=SIDEBAR_W)
        body.add(self.sidebar, weight=0)
        main = ttk.Frame(body, padding=(16, 14, 20, 8))
        body.add(main, weight=1)

        side = ttk.Frame(self.sidebar.inner, padding=(20, 14, 12, 14))
        side.pack(fill="both", expand=True)
        self._build_source_card(side)
        self._build_query_card(side)
        self._build_ai_card(side)

        actions = ttk.Frame(side)
        actions.pack(fill="x", pady=(14, 0))
        self.start_btn = ttk.Button(actions, text="Taramayı başlat", style="Big.Accent.TButton",
                                    command=self._start_scan)
        self.start_btn.pack(side="left", fill="x", expand=True)
        self.stop_btn = ttk.Button(actions, text="Durdur", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0), ipady=4)

        self._build_results(main)

    def _card(self, parent, step: str, title: str, subtitle: str = "") -> ttk.Frame:
        card = ttk.Frame(parent, style="Card.TFrame", padding=14)
        card.pack(fill="x", pady=(0, 12))
        head = ttk.Frame(card)
        head.pack(fill="x", pady=(0, 8))
        tk.Label(head, text=f" {step} ", bg=BRAND, fg="#ffffff", borderwidth=0,
                 font=("Segoe UI Semibold", 10)).pack(side="left")
        ttk.Label(head, text=title, style="Section.TLabel").pack(side="left", padx=8)
        if subtitle:
            ttk.Label(card, text=subtitle, style="Caption.TLabel", wraplength=WRAP,
                      justify="left").pack(anchor="w", pady=(0, 8))
        return card

    # ---------------------------------------------------------------- 1) kaynak
    def _build_source_card(self, parent) -> None:
        card = self._card(parent, "1", "Mesaj kaynağı")
        seg = ttk.Frame(card)
        seg.pack(fill="x", pady=(0, 10))
        self.source = tk.StringVar(value="web")
        for i, (key, label) in enumerate(SOURCES):
            ttk.Radiobutton(seg, text=label, value=key, variable=self.source, style="Toggle.TButton",
                            command=self._show_source).grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 4, 0))
            seg.columnconfigure(i, weight=1)

        self.source_frames: dict[str, ttk.Frame] = {}
        host = ttk.Frame(card)
        host.pack(fill="both", expand=True)

        # WhatsApp oturumu
        f = ttk.Frame(host)
        self.source_frames["web"] = f
        ttk.Label(f, text="Hesabınız, uygulamanın açtığı Edge penceresine bağlı cihaz olarak eklenir "
                          "(WhatsApp Desktop ile aynı sohbetler). İlk seferde QR kodu okutun.",
                  style="Caption.TLabel", wraplength=WRAP, justify="left").pack(anchor="w")
        row = ttk.Frame(f)
        row.pack(fill="x", pady=8)
        ttk.Button(row, text="WhatsApp'a bağlan", style="Accent.TButton",
                   command=self._connect_web).pack(side="left")
        ttk.Button(row, text="↻ Yenile", command=self._list_chats).pack(side="left", padx=6)
        self.chat_filter = tk.StringVar()
        self.chat_filter.trace_add("write", lambda *a: self._refresh_chat_list())
        self.chat_filter_entry = PlaceholderEntry(f, "🔍  Sohbet veya grup ara…", self.chat_filter)
        self.chat_filter_entry.pack(fill="x")
        lf = ttk.Frame(f)
        lf.pack(fill="both", expand=True, pady=(6, 0))
        self.chat_tree = ttk.Treeview(lf, show="tree", selectmode="extended", height=8, style="Chats.Treeview")
        sb = ttk.Scrollbar(lf, command=self.chat_tree.yview)
        self.chat_tree.configure(yscrollcommand=sb.set)
        self.chat_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.chat_tree.bind("<<TreeviewSelect>>", self._on_chat_select)
        sel_row = ttk.Frame(f)
        sel_row.pack(fill="x", pady=(4, 0))
        self.chat_sel_label = ttk.Label(sel_row, text="Bağlandıktan sonra sohbetler burada listelenir.",
                                        style="Caption.TLabel")
        self.chat_sel_label.pack(side="left")
        ttk.Button(sel_row, text="Seçimi temizle", style="Chip.TButton",
                   command=self._clear_chat_selection).pack(side="right")
        grid = ttk.Frame(f)
        grid.pack(fill="x", pady=(10, 0))
        ttk.Label(grid, text="En fazla mesaj (sohbet başına)").grid(row=0, column=0, sticky="w")
        self.max_msgs = tk.IntVar(value=500)
        ttk.Spinbox(grid, from_=50, to=20000, increment=50, textvariable=self.max_msgs,
                    width=8).grid(row=0, column=1, sticky="e", pady=2)
        ttk.Label(grid, text="Başlangıç tarihi (gg.aa.yyyy)").grid(row=1, column=0, sticky="w")
        self.since = tk.StringVar()
        ttk.Entry(grid, textvariable=self.since, width=12).grid(row=1, column=1, sticky="e", pady=2)
        grid.columnconfigure(0, weight=1)

        # Ekran tarama
        f = ttk.Frame(host)
        self.source_frames["screen"] = f
        ttk.Label(f, text="Açık WhatsApp Desktop penceresi kaydırılarak ekran görüntüleri alınır ve Claude "
                          "tarafından okunur. Tarama sırasında fare ve klavyeye dokunmayın.",
                  style="Caption.TLabel", wraplength=WRAP, justify="left").pack(anchor="w")
        ttk.Label(f, text="Sohbet / grup adları (her satıra bir tane)").pack(anchor="w", pady=(10, 4))
        self.screen_chats = tk.Text(f, height=6, width=40, wrap="word", font=("Segoe UI", 10))
        self.screen_chats.pack(fill="x")
        self.manual_open = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Sohbetleri kendim açacağım", style="Switch.TCheckbutton",
                        variable=self.manual_open).pack(anchor="w", pady=(10, 4))
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="Sohbet başına en fazla ekran").pack(side="left")
        self.max_pages = tk.IntVar(value=20)
        ttk.Spinbox(row, from_=1, to=300, textvariable=self.max_pages, width=6).pack(side="right")

        # Dışa aktarım
        f = ttk.Frame(host)
        self.source_frames["export"] = f
        ttk.Label(f, text="Telefonda: sohbet → ⋮ → Diğer → Sohbeti dışa aktar → Medya dahil. "
                          "En eksiksiz yöntemdir (tüm geçmiş + tam çözünürlüklü görseller).",
                  style="Caption.TLabel", wraplength=WRAP, justify="left").pack(anchor="w")
        row = ttk.Frame(f)
        row.pack(fill="x", pady=8)
        ttk.Button(row, text="+ Dosya ekle", style="Accent.TButton",
                   command=self._add_export_files).pack(side="left")
        ttk.Button(row, text="+ Klasör", command=self._add_export_dir).pack(side="left", padx=6)
        ttk.Button(row, text="Kaldır", command=self._remove_export).pack(side="right")
        self.export_tree = ttk.Treeview(f, show="tree", height=6, selectmode="extended", style="Chats.Treeview")
        self.export_tree.pack(fill="both", expand=True)

        self._show_source()

    def _show_source(self) -> None:
        for key, frame in self.source_frames.items():
            if key == self.source.get():
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()

    # ---------------------------------------------------------------- 2) arama
    def _build_query_card(self, parent) -> None:
        card = self._card(parent, "2", "Ne aranacak?",
                          "Bir kelime, ifade ya da tarif yazın. Claude anlamca uyan mesaj ve görselleri bulur.")
        self.criterion = tk.Text(card, height=4, width=40, wrap="word", font=("Segoe UI", 11))
        self.criterion.pack(fill="x")
        chips = ttk.Frame(card)
        chips.pack(fill="x", pady=(6, 10))
        ttk.Label(chips, text="Örnek:", style="Caption.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 4))
        for i, ex in enumerate(EXAMPLES):
            ttk.Button(chips, text=ex, style="Chip.TButton", command=lambda ex=ex: self._set_criterion(ex)) \
                .grid(row=i // 2, column=1 + i % 2, sticky="ew", padx=2, pady=2)

        seg = ttk.Frame(card)
        seg.pack(fill="x")
        self.mode = tk.StringVar(value="semantic")
        for i, (val, label) in enumerate((("semantic", "Anlamsal (yapay zekâ)"), ("keyword", "Kelime araması"))):
            ttk.Radiobutton(seg, text=label, value=val, variable=self.mode, style="Toggle.TButton",
                            command=self._update_mode_hint).grid(row=0, column=i, sticky="ew",
                                                                 padx=(0 if i == 0 else 4, 0))
            seg.columnconfigure(i, weight=1)
        self.mode_hint = ttk.Label(card, style="Caption.TLabel", wraplength=WRAP, justify="left")
        self.mode_hint.pack(anchor="w", pady=(6, 0))
        self.scan_images = tk.BooleanVar(value=True)
        ttk.Checkbutton(card, text="Görselleri de tara (yazı okuma + içerik tanıma)",
                        style="Switch.TCheckbutton", variable=self.scan_images).pack(anchor="w", pady=(10, 0))
        self._update_mode_hint()

    def _set_criterion(self, text: str) -> None:
        self.criterion.delete("1.0", "end")
        self.criterion.insert("1.0", text)
        self.criterion.focus_set()

    def _update_mode_hint(self) -> None:
        self.mode_hint.configure(text=(
            "Eş anlamlılar, yazım hataları, Türkçe karaktersiz yazımlar ve konuyla ilgili mesajlar da bulunur."
            if self.mode.get() == "semantic" else
            "Metinlerde yerel ve hızlı arama; virgülle birden çok kelime yazabilirsiniz (ödeme, fatura)."
        ))

    # ---------------------------------------------------------------- 3) yapay zekâ
    def _build_ai_card(self, parent) -> None:
        card = self._card(parent, "3", "Yapay zekâ")
        grid = ttk.Frame(card)
        grid.pack(fill="x")
        ttk.Label(grid, text="Bağlantı").grid(row=0, column=0, sticky="w", pady=3)
        self.backend_var = tk.StringVar(value=list(BACKENDS)[0])
        cb = ttk.Combobox(grid, textvariable=self.backend_var, values=list(BACKENDS), state="readonly", width=30)
        cb.grid(row=0, column=1, sticky="ew", pady=3, padx=(10, 0))
        cb.bind("<<ComboboxSelected>>", lambda e: self._toggle_key())
        ttk.Label(grid, text="Model").grid(row=1, column=0, sticky="w", pady=3)
        self.model_var = tk.StringVar(value=DEFAULT_MODEL)
        ttk.Combobox(grid, textvariable=self.model_var, values=MODEL_CHOICES, width=30) \
            .grid(row=1, column=1, sticky="ew", pady=3, padx=(10, 0))
        self.key_label = ttk.Label(grid, text="API anahtarı")
        self.api_key = tk.StringVar(value=os.environ.get("ANTHROPIC_API_KEY", ""))
        self.key_entry = ttk.Entry(grid, textvariable=self.api_key, show="•", width=30)
        grid.columnconfigure(1, weight=1)
        ttk.Button(card, text="Bağlantıyı test et", command=self._test_backend).pack(anchor="w", pady=(8, 0))

    def _toggle_key(self) -> None:
        if BACKENDS.get(self.backend_var.get()) == "api":
            self.key_label.grid(row=2, column=0, sticky="w", pady=3)
            self.key_entry.grid(row=2, column=1, sticky="ew", pady=3, padx=(10, 0))
        else:
            self.key_label.grid_remove()
            self.key_entry.grid_remove()

    def _set_claude_status(self, state: str, text: str) -> None:
        self._claude_state = (state, text)
        color = {"ok": self.c["ok"], "err": self.c["err"], "busy": self.c["warn"]}.get(state, self.c["muted"])
        self.claude_status.configure(text=f"●  {text}", foreground=color)

    # ---------------------------------------------------------------- sonuç alanı
    def _build_results(self, main) -> None:
        stats = ttk.Frame(main)
        stats.pack(fill="x")
        self.stat_vars: dict[str, tk.StringVar] = {}
        for i, (key, label) in enumerate((("total", "Toplam sonuç"), ("text", "Metin mesajı"),
                                          ("image", "Görsel"), ("chats", "Sohbet"))):
            card = ttk.Frame(stats, style="Card.TFrame", padding=(16, 10))
            card.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 10, 0))
            stats.columnconfigure(i, weight=1)
            var = tk.StringVar(value="0")
            self.stat_vars[key] = var
            ttk.Label(card, textvariable=var, style="StatNum.TLabel").pack(anchor="w")
            ttk.Label(card, text=label, style="Caption.TLabel").pack(anchor="w")

        bar = ttk.Frame(main)
        bar.pack(fill="x", pady=(14, 8))
        self.result_query = tk.StringVar()
        self.result_query.trace_add("write", lambda *a: self._rebuild_tree())
        self.result_entry = PlaceholderEntry(bar, "🔍  Sonuçlarda ara…", self.result_query, width=34)
        self.result_entry.pack(side="left")
        self.kind_filter = tk.StringVar(value="all")
        seg = ttk.Frame(bar)
        seg.pack(side="left", padx=10)
        for val, label in (("all", "Tümü"), ("text", "Metin"), ("image", "Görsel")):
            ttk.Radiobutton(seg, text=label, value=val, variable=self.kind_filter, style="Toggle.TButton",
                            command=self._rebuild_tree).pack(side="left", padx=(0, 4))
        ttk.Button(bar, text="HTML rapor", command=self._save_html).pack(side="right")
        ttk.Button(bar, text="Excel (CSV)", command=self._save_csv).pack(side="right", padx=6)
        ttk.Button(bar, text="Temizle", command=self._clear_results).pack(side="right")

        paned = ttk.PanedWindow(main, orient="vertical")
        paned.pack(fill="both", expand=True)

        table = ttk.Frame(paned)
        cols = ("sohbet", "tarih", "gonderen", "tur", "mesaj", "neden")
        self.tree = ttk.Treeview(table, columns=cols, show="headings", selectmode="browse")
        for c, title, w, stretch in [
            ("sohbet", "Sohbet", 140, False), ("tarih", "Tarih", 130, False), ("gonderen", "Gönderen", 120, False),
            ("tur", "Tür", 90, False), ("mesaj", "Mesaj / görsel içeriği", 360, True), ("neden", "Gerekçe", 220, True),
        ]:
            self.tree.heading(c, text=title, anchor="w", command=lambda c=c: self._sort(c))
            self.tree.column(c, width=w, minwidth=60, anchor="w", stretch=stretch)
        ys = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ys.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._show_detail)
        self.tree.bind("<Double-1>", self._open_image)
        self.empty_label = tk.Label(table, justify="center", borderwidth=0,
                                    font=("Segoe UI", 11))
        self._update_empty()
        paned.add(table, weight=3)

        nb = ttk.Notebook(paned)
        detail = ttk.Frame(nb, padding=8)
        self.detail_text = tk.Text(detail, height=8, wrap="word", font=("Segoe UI", 10))
        self.detail_text.pack(side="left", fill="both", expand=True)
        self.thumb_label = ttk.Label(detail, cursor="hand2")
        self.thumb_label.pack(side="left", padx=(10, 0))
        self.thumb_label.bind("<Button-1>", self._open_image)
        nb.add(detail, text="Ayrıntı")
        logf = ttk.Frame(nb, padding=8)
        self.log_text = tk.Text(logf, height=8, wrap="word", font=("Consolas", 9), state="disabled")
        self.log_text.pack(fill="both", expand=True)
        nb.add(logf, text="Günlük")
        self.bottom_nb = nb
        paned.add(nb, weight=1)

    def _update_empty(self) -> None:
        if self.tree.get_children():
            self.empty_label.place_forget()
            return
        if self.matches:
            text = "Filtreye uyan sonuç yok."
        else:
            text = ("Henüz sonuç yok\n\nSoldan kaynak ve sohbetleri seçin, aranacak tabiri yazın,\n"
                    "ardından “Taramayı başlat”a basın.")
        self.empty_label.configure(text=text, foreground=self.c["muted"])
        self.empty_label.place(relx=0.5, rely=0.55, anchor="center")

    # ================================================================== ayarlar
    @staticmethod
    def _read_settings() -> dict:
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _load_settings(self) -> None:
        s = self.settings
        self.criterion.insert("1.0", s.get("criterion", ""))
        self.mode.set(s.get("mode", "semantic"))
        self.scan_images.set(s.get("scan_images", True))
        if s.get("backend") in BACKENDS:
            self.backend_var.set(s["backend"])
        self.model_var.set(s.get("model", DEFAULT_MODEL))
        self.max_msgs.set(s.get("max_msgs", 500))
        self.screen_chats.insert("1.0", s.get("screen_chats", ""))
        if s.get("source") in self.source_frames:
            self.source.set(s["source"])
            self._show_source()
        self._update_mode_hint()
        self._toggle_key()

    def _save_settings(self) -> None:
        s = {
            "criterion": self._criterion(),
            "mode": self.mode.get(),
            "scan_images": self.scan_images.get(),
            "backend": self.backend_var.get(),
            "model": self.model_var.get(),
            "max_msgs": self._int(self.max_msgs, 500),
            "screen_chats": self.screen_chats.get("1.0", "end").strip(),
            "source": self.source.get(),
            "theme": self.theme,
        }
        try:
            SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _int(var: tk.Variable, default: int) -> int:
        try:
            return int(var.get())
        except (tk.TclError, ValueError):
            return default

    # ================================================================== olay döngüsü
    def log(self, msg: str) -> None:
        self.events.put(("log", msg))

    def _append_log(self, msg: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{datetime.now():%H:%M:%S}  ", "time")
        self.log_text.insert("end", msg + "\n", "err" if msg.startswith("HATA") else ())
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _poll(self) -> None:
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "log":
                    self._append_log(ev[1])
                    if not ev[1].startswith("HATA"):
                        self.status.set(ev[1])
                elif kind == "status":
                    self.status.set(ev[1])
                elif kind == "progress":
                    self.progress["value"] = max(0.0, min(1.0, ev[1]))
                elif kind == "match":
                    self._add_match(ev[1])
                elif kind == "chats":
                    self.all_chats = ev[1]
                    self._refresh_chat_list()
                elif kind == "claude":
                    self._set_claude_status(ev[1], ev[2])
                elif kind == "ask_open":
                    messagebox.showinfo(APP_NAME, f"WhatsApp Desktop'ta \"{ev[1]}\" sohbetini açın, "
                                                  "sonra Tamam'a basın.\n(Tamam'dan sonra fareye dokunmayın.)")
                    ev[2].set()
                elif kind == "error":
                    self._append_log("HATA: " + ev[1])
                    messagebox.showerror(APP_NAME, ev[1])
                elif kind == "done":
                    self._set_running(False)
                    self.status.set(ev[1])
                    self._append_log(ev[1])
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _set_running(self, running: bool) -> None:
        self.start_btn.configure(state="disabled" if running else "normal",
                                 text="Taranıyor…" if running else "Taramayı başlat")
        self.stop_btn.configure(state="normal" if running else "disabled")
        if running:
            self.progress["value"] = 0.0
        else:
            self.progress["value"] = 1.0 if self.matches else 0.0

    def _run_bg(self, fn, *args) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showwarning(APP_NAME, "Devam eden bir işlem var.")
            return
        self.cancel.clear()
        self._set_running(True)

        def wrapper():
            try:
                msg = fn(*args) or "İşlem tamamlandı."
            except Exception as e:  # kullanıcıya göster
                self.events.put(("error", str(e)))
                msg = "İşlem hata ile bitti."
            self.events.put(("done", msg))

        self.worker = threading.Thread(target=wrapper, daemon=True)
        self.worker.start()

    # ================================================================== WhatsApp oturumu
    def _ensure_session(self) -> WhatsAppWebSession:
        if self.session is None:
            self.session = WhatsAppWebSession(log=self.log)
        self.session.start()
        return self.session

    def _connect_web(self) -> None:
        def job():
            chats = self._ensure_session().list_chats()
            self.events.put(("chats", chats))
            return f"WhatsApp'a bağlanıldı, {len(chats)} sohbet listelendi."
        self._run_bg(job)

    def _list_chats(self) -> None:
        def job():
            chats = self._ensure_session().list_chats()
            self.events.put(("chats", chats))
            return f"{len(chats)} sohbet bulundu."
        self._run_bg(job)

    def _refresh_chat_list(self) -> None:
        if not hasattr(self, "chat_filter_entry"):
            return
        f = normalize_tr(self.chat_filter_entry.value().strip())
        self.chat_tree.delete(*self.chat_tree.get_children())
        keep = []
        for i, name in enumerate(self.all_chats):
            if f in normalize_tr(name):
                iid = str(i)
                self.chat_tree.insert("", "end", iid=iid, text=f"  {name}")
                if name in self.selected_chats:
                    keep.append(iid)
        self._syncing = True
        self.chat_tree.selection_set(keep)
        self._syncing = False
        self._update_chat_label()

    def _on_chat_select(self, _e=None) -> None:
        if getattr(self, "_syncing", False):
            return
        selected = set(self.chat_tree.selection())
        for iid in self.chat_tree.get_children():
            name = self.all_chats[int(iid)]
            (self.selected_chats.add if iid in selected else self.selected_chats.discard)(name)
        self._update_chat_label()

    def _clear_chat_selection(self) -> None:
        self.selected_chats.clear()
        self._refresh_chat_list()

    def _update_chat_label(self) -> None:
        if not self.all_chats:
            return
        n = len(self.selected_chats)
        self.chat_sel_label.configure(
            text=f"{n} sohbet seçili · toplam {len(self.all_chats)}" if n else
            f"{len(self.all_chats)} sohbet · Ctrl/Shift ile çoklu seçim")

    # ================================================================== dışa aktarım
    def _add_export_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="WhatsApp dışa aktarım dosyaları",
            filetypes=[("WhatsApp dışa aktarım", "*.zip *.txt"), ("Tümü", "*.*")],
        )
        for p in paths:
            self._add_export(Path(p))

    def _add_export_dir(self) -> None:
        d = filedialog.askdirectory(title="Açılmış dışa aktarım klasörü")
        if d:
            self._add_export(Path(d))

    def _add_export(self, p: Path) -> None:
        if p not in self.export_files:
            self.export_files.append(p)
            self.export_tree.insert("", "end", iid=str(p), text=f"  {p.name}")

    def _remove_export(self) -> None:
        for iid in self.export_tree.selection():
            self.export_files = [p for p in self.export_files if str(p) != iid]
            self.export_tree.delete(iid)

    # ================================================================== tarama
    def _criterion(self) -> str:
        return self.criterion.get("1.0", "end").strip()

    def _make_backend(self, required: bool):
        if not required:
            return None
        return make_backend(BACKENDS[self.backend_var.get()], self.model_var.get().strip() or DEFAULT_MODEL,
                            self.api_key.get().strip())

    def _test_backend(self) -> None:
        def job():
            self.events.put(("claude", "busy", "Claude test ediliyor…"))
            try:
                msg = self._make_backend(True).check()
            except Exception:
                self.events.put(("claude", "err", "Claude bağlantısı yok"))
                raise
            self.events.put(("claude", "ok", f"Claude hazır · {self.model_var.get()}"))
            return msg
        self._run_bg(job)

    def _start_scan(self) -> None:
        criterion = self._criterion()
        if not criterion:
            messagebox.showwarning(APP_NAME, "Lütfen aranacak tabiri / kriteri yazın.")
            self.criterion.focus_set()
            return
        self._save_settings()
        source = self.source.get()
        opts = MatchOptions(criterion=criterion, mode=self.mode.get(), scan_images=self.scan_images.get())
        needs_ai = opts.mode == "semantic" or opts.scan_images

        if source == "web":
            chats = [c for c in self.all_chats if c in self.selected_chats]
            if not chats:
                messagebox.showwarning(APP_NAME, "Önce WhatsApp'a bağlanıp en az bir sohbet seçin.")
                return
            since = None
            if self.since.get().strip():
                since = parse_date(self.since.get())
                if since is None:
                    messagebox.showwarning(APP_NAME, "Tarih biçimi gg.aa.yyyy olmalı.")
                    return
            self._run_bg(self._scan_web, chats, opts, needs_ai, self._int(self.max_msgs, 500), since)
        elif source == "screen":
            chats = [c.strip() for c in self.screen_chats.get("1.0", "end").splitlines() if c.strip()]
            if not chats:
                chats = ["(açık sohbet)"]
                self.manual_open.set(True)
            self._run_bg(self._scan_screen, chats, opts, self._int(self.max_pages, 20), self.manual_open.get())
        else:
            if not self.export_files:
                messagebox.showwarning(APP_NAME, "Önce dışa aktarılmış sohbet dosyası ekleyin.")
                return
            self._run_bg(self._scan_exports, list(self.export_files), opts, needs_ai)

    def _matcher(self, opts: MatchOptions, needs_ai: bool, step: int, total: int) -> MessageMatcher:
        backend = self._make_backend(needs_ai)

        def progress(msg: str, p: float):
            if p < 0:
                self.log(msg)
            else:
                self.events.put(("progress", (step + p) / total))
                self.events.put(("status", msg))

        return MessageMatcher(backend, opts, on_match=lambda m: self.events.put(("match", m)),
                              on_progress=progress, cancel=self.cancel)

    def _scan_web(self, chats, opts, needs_ai, max_msgs, since) -> str:
        session = self._ensure_session()
        media = app_data_dir() / "medya" / datetime.now().strftime("%Y%m%d_%H%M%S")
        for i, chat in enumerate(chats):
            if self.cancel.is_set():
                break
            self.log(f"[{i + 1}/{len(chats)}] {chat}: mesajlar okunuyor…")
            msgs = session.collect(chat, media, max_messages=max_msgs, since=since,
                                   download_images=opts.scan_images, cancel=self.cancel)
            n_img = sum(1 for m in msgs if m.has_image)
            self.log(f"{chat}: {len(msgs)} mesaj ({n_img} görsel) okundu, taranıyor…")
            self._matcher(opts, needs_ai, i, len(chats)).run(msgs)
        return self._summary()

    def _scan_exports(self, files, opts, needs_ai) -> str:
        for i, f in enumerate(files):
            if self.cancel.is_set():
                break
            msgs = parse_export(f)
            n_img = sum(1 for m in msgs if m.has_image)
            self.log(f"{f.name}: {len(msgs)} mesaj ({n_img} görsel) okundu, taranıyor…")
            self._matcher(opts, needs_ai, i, len(files)).run(msgs)
        return self._summary()

    def _scan_screen(self, chats, opts, max_pages, manual) -> str:
        from .sources.desktop_screen import WhatsAppDesktopScreen, analyze_frames

        backend = self._make_backend(True)
        screen = WhatsAppDesktopScreen(log=self.log)
        base = app_data_dir() / "ekran" / datetime.now().strftime("%Y%m%d_%H%M%S")
        for i, chat in enumerate(chats):
            if self.cancel.is_set():
                break
            if manual:
                ev = threading.Event()
                self.events.put(("ask_open", chat, ev))
                ev.wait()
            else:
                screen.open_chat(chat)
            frames = screen.capture(chat, base / safe_name(chat), max_pages=max_pages, cancel=self.cancel)
            self.log(f"{chat}: {len(frames)} ekran görüntüsü alındı, Claude ile analiz ediliyor…")
            analyze_frames(backend, chat, frames, opts.criterion,
                           on_match=lambda m: self.events.put(("match", m)), log=self.log, cancel=self.cancel)
            self.events.put(("progress", (i + 1) / len(chats)))
        return self._summary()

    def _summary(self) -> str:
        state = "durduruldu" if self.cancel.is_set() else "tamamlandı"
        return f"Tarama {state}. Sonuçlar listede."

    def _stop(self) -> None:
        self.cancel.set()
        self.status.set("Durduruluyor… (devam eden istek bitince duracak)")

    # ================================================================== sonuçlar
    @staticmethod
    def _is_image(m: Match) -> bool:
        return m.kind == "görsel"

    def _passes_filter(self, m: Match) -> bool:
        kind = self.kind_filter.get()
        if kind == "text" and self._is_image(m):
            return False
        if kind == "image" and not self._is_image(m):
            return False
        q = normalize_tr(self.result_entry.value().strip())
        if q:
            hay = normalize_tr(" ".join((m.message.chat, m.message.sender, m.display_text(), m.reason)))
            return q in hay
        return True

    def _insert_row(self, idx: int, m: Match) -> None:
        msg = m.message
        n = len(self.tree.get_children())
        tags = ["odd" if n % 2 else "even"]
        if self._is_image(m):
            tags.append("görsel")
        kind = {"görsel": "🖼  Görsel", "metin": "💬  Metin", "ekran": "🖥  Ekran"}.get(m.kind, m.kind)
        self.tree.insert("", "end", iid=str(idx), tags=tags,
                         values=(msg.chat, msg.timestamp, msg.sender, kind,
                                 m.display_text().replace("\n", " ")[:400], m.reason))

    def _rebuild_tree(self) -> None:
        if not hasattr(self, "tree"):
            return
        self.tree.delete(*self.tree.get_children())
        for i, m in enumerate(self.matches):
            if self._passes_filter(m):
                self._insert_row(i, m)
        self._update_empty()

    def _add_match(self, m: Match) -> None:
        self.matches.append(m)
        if self._passes_filter(m):
            self._insert_row(len(self.matches) - 1, m)
        self._update_stats()
        self._update_empty()

    def _update_stats(self) -> None:
        n_img = sum(1 for m in self.matches if self._is_image(m))
        self.stat_vars["total"].set(str(len(self.matches)))
        self.stat_vars["image"].set(str(n_img))
        self.stat_vars["text"].set(str(len(self.matches) - n_img))
        self.stat_vars["chats"].set(str(len({m.message.chat for m in self.matches})))
        self.title(f"{APP_NAME} – {len(self.matches)} sonuç" if self.matches else APP_NAME)

    def _clear_results(self) -> None:
        self.matches.clear()
        self.tree.delete(*self.tree.get_children())
        self.detail_text.delete("1.0", "end")
        self.thumb_label.configure(image="")
        self._update_stats()
        self._update_empty()

    def _selected(self) -> Optional[Match]:
        sel = self.tree.selection()
        return self.matches[int(sel[0])] if sel else None

    def _show_detail(self, _e=None) -> None:
        m = self._selected()
        if not m:
            return
        msg = m.message
        t = self.detail_text
        t.delete("1.0", "end")
        t.tag_configure("key", foreground=self.c["muted"])
        t.tag_configure("head", font=("Segoe UI Semibold", 11))
        t.insert("end", f"{msg.chat}\n", "head")
        for k, v in (("Tarih", msg.timestamp), ("Gönderen", msg.sender), ("Tür", m.kind), ("Gerekçe", m.reason)):
            t.insert("end", f"{k}: ", "key")
            t.insert("end", f"{v}\n")
        if msg.text:
            t.insert("end", f"\n{msg.text}\n")
        if m.ocr_text:
            t.insert("end", "\nGörseldeki yazı\n", "key")
            t.insert("end", f"{m.ocr_text}\n")
        if m.image_description:
            t.insert("end", "\nGörsel tarifi\n", "key")
            t.insert("end", f"{m.image_description}\n")
        self.bottom_nb.select(0)
        self.thumb_label.configure(image="")
        if msg.image_path and Path(msg.image_path).exists():
            try:
                from PIL import Image, ImageTk

                with Image.open(msg.image_path) as im:
                    im = im.convert("RGB")
                    im.thumbnail((230, 190))
                    self._thumb = ImageTk.PhotoImage(im)
                self.thumb_label.configure(image=self._thumb)
            except Exception:
                pass

    def _open_image(self, _e=None) -> None:
        m = self._selected()
        if m and m.message.image_path and Path(m.message.image_path).exists():
            open_path(Path(m.message.image_path))

    def _sort(self, col: str) -> None:
        items = [(self.tree.set(k, col), k) for k in self.tree.get_children("")]
        items.sort(key=lambda x: normalize_tr(x[0]), reverse=self._sort_rev)
        for idx, (_, k) in enumerate(items):
            self.tree.move(k, "", idx)
            tags = [t for t in self.tree.item(k, "tags") if t not in ("odd", "even")]
            self.tree.item(k, tags=["odd" if idx % 2 else "even", *tags])
        self._sort_rev = not self._sort_rev

    def _ordered_matches(self) -> list[Match]:
        return [self.matches[int(k)] for k in self.tree.get_children("")]

    def _save_csv(self) -> None:
        if not self.tree.get_children():
            messagebox.showinfo(APP_NAME, "Kaydedilecek sonuç yok.")
            return
        p = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV (Excel)", "*.csv")],
                                         initialfile=f"whatsapp_sonuclar_{datetime.now():%Y%m%d_%H%M}.csv")
        if p:
            save_csv(self._ordered_matches(), Path(p))
            self._append_log(f"CSV kaydedildi: {p}")

    def _save_html(self) -> None:
        if not self.tree.get_children():
            messagebox.showinfo(APP_NAME, "Kaydedilecek sonuç yok.")
            return
        p = filedialog.asksaveasfilename(defaultextension=".html", filetypes=[("HTML rapor", "*.html")],
                                         initialfile=f"whatsapp_rapor_{datetime.now():%Y%m%d_%H%M}.html")
        if p:
            save_html(self._ordered_matches(), Path(p), self._criterion())
            self._append_log(f"Rapor kaydedildi: {p}")
            open_path(Path(p))

    def _on_close(self) -> None:
        self.cancel.set()
        self._save_settings()
        if self.session:
            try:
                self.session.close()
            except Exception:
                pass
        self.destroy()


def main() -> None:
    if sys.platform == "win32":
        try:  # Yüksek DPI ekranlarda net görüntü ve doğru ekran koordinatları
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
    App().mainloop()
