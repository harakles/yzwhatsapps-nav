"""Tkinter tabanlı Windows masaüstü arayüzü."""
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
from .matcher import MatchOptions, MessageMatcher
from .models import Match
from .report import save_csv, save_html
from .sources.export_zip import parse_export
from .sources.whatsapp_web import WhatsAppWebSession, app_data_dir, parse_date, safe_name

BACKENDS = {
    "Claude aboneliği (Claude Code)": "cli",
    "Anthropic API anahtarı": "api",
}
SETTINGS_FILE = app_data_dir() / "ayarlar.json"


def open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {__version__}")
        self.geometry("1280x860")
        self.minsize(1000, 700)

        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.cancel = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.session: Optional[WhatsAppWebSession] = None
        self.matches: list[Match] = []
        self.all_chats: list[str] = []
        self.export_files: list[Path] = []
        self._thumb = None

        self._build()
        self._load_settings()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)

    # ================================================================== arayüz
    def _build(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))

        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")

        # ---- 1) Kaynak
        src = ttk.LabelFrame(top, text="1) Mesaj kaynağı", padding=6)
        src.pack(side="left", fill="both", expand=True)
        self.nb = ttk.Notebook(src)
        self.nb.pack(fill="both", expand=True)
        self._build_web_tab()
        self._build_screen_tab()
        self._build_export_tab()

        # ---- 2) Arama + 3) Yapay zekâ
        right = ttk.Frame(top)
        right.pack(side="left", fill="both", padx=(8, 0))

        q = ttk.LabelFrame(right, text="2) Ne aranacak?", padding=6)
        q.pack(fill="x")
        ttk.Label(q, text="Aranacak tabir / kriter (ör. \"fatura\", \"kira ödemesi ile ilgili mesajlar\", "
                          "\"içinde plaka görünen fotoğraflar\"):", wraplength=420).pack(anchor="w")
        self.criterion = tk.Text(q, height=4, width=55, wrap="word", font=("Segoe UI", 10))
        self.criterion.pack(fill="x", pady=4)
        self.mode = tk.StringVar(value="semantic")
        ttk.Radiobutton(q, text="Anlamsal arama (yapay zekâ; eş anlamlı, yazım hatası, konu)",
                        variable=self.mode, value="semantic").pack(anchor="w")
        ttk.Radiobutton(q, text="Kelime araması (metinde yerel; virgülle birden çok kelime)",
                        variable=self.mode, value="keyword").pack(anchor="w")
        self.scan_images = tk.BooleanVar(value=True)
        ttk.Checkbutton(q, text="Görselleri de tara (yazı okuma + içerik tanıma, yapay zekâ ile)",
                        variable=self.scan_images).pack(anchor="w", pady=(4, 0))

        ai = ttk.LabelFrame(right, text="3) Yapay zekâ", padding=6)
        ai.pack(fill="x", pady=(8, 0))
        row = ttk.Frame(ai)
        row.pack(fill="x")
        ttk.Label(row, text="Bağlantı:").pack(side="left")
        self.backend_var = tk.StringVar(value=list(BACKENDS)[0])
        cb = ttk.Combobox(row, textvariable=self.backend_var, values=list(BACKENDS), state="readonly", width=32)
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self._toggle_key())
        row2 = ttk.Frame(ai)
        row2.pack(fill="x", pady=4)
        ttk.Label(row2, text="Model:").pack(side="left")
        self.model_var = tk.StringVar(value=DEFAULT_MODEL)
        ttk.Combobox(row2, textvariable=self.model_var, values=MODEL_CHOICES, width=22).pack(side="left", padx=4)
        ttk.Button(row2, text="Bağlantıyı test et", command=self._test_backend).pack(side="left", padx=4)
        self.key_row = ttk.Frame(ai)
        ttk.Label(self.key_row, text="API anahtarı:").pack(side="left")
        self.api_key = tk.StringVar(value=os.environ.get("ANTHROPIC_API_KEY", ""))
        ttk.Entry(self.key_row, textvariable=self.api_key, show="•", width=40).pack(side="left", padx=4)

        # ---- Eylemler
        act = ttk.Frame(self, padding=(8, 0))
        act.pack(fill="x")
        self.start_btn = ttk.Button(act, text="▶  Taramayı başlat", style="Accent.TButton", command=self._start_scan)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(act, text="■  Durdur", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        ttk.Button(act, text="Sonuçları temizle", command=self._clear_results).pack(side="left", padx=6)
        ttk.Button(act, text="HTML rapor kaydet", command=self._save_html).pack(side="right")
        ttk.Button(act, text="Excel (CSV) kaydet", command=self._save_csv).pack(side="right", padx=6)

        prog = ttk.Frame(self, padding=(8, 6))
        prog.pack(fill="x")
        self.progress = ttk.Progressbar(prog, mode="determinate", maximum=1.0)
        self.progress.pack(fill="x")
        self.status = tk.StringVar(value="Hazır.")
        ttk.Label(prog, textvariable=self.status).pack(anchor="w")

        # ---- Sonuçlar
        paned = ttk.PanedWindow(self, orient="vertical")
        paned.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        res = ttk.LabelFrame(paned, text="Sonuçlar", padding=4)
        cols = ("sohbet", "tarih", "gonderen", "tur", "mesaj", "neden")
        self.tree = ttk.Treeview(res, columns=cols, show="headings", selectmode="browse")
        for c, title, w in [
            ("sohbet", "Sohbet", 150), ("tarih", "Tarih", 130), ("gonderen", "Gönderen", 130),
            ("tur", "Tür", 60), ("mesaj", "Mesaj / görsel içeriği", 520), ("neden", "Gerekçe", 260),
        ]:
            self.tree.heading(c, text=title, command=lambda c=c: self._sort(c))
            self.tree.column(c, width=w, anchor="w", stretch=c in ("mesaj", "neden"))
        ys = ttk.Scrollbar(res, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ys.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._show_detail)
        self.tree.bind("<Double-1>", self._open_image)
        paned.add(res, weight=4)

        detail = ttk.Frame(paned)
        dl = ttk.LabelFrame(detail, text="Seçili sonuç (görsele çift tıklayarak büyütün)", padding=4)
        dl.pack(side="left", fill="both", expand=True)
        self.detail_text = tk.Text(dl, height=8, wrap="word", font=("Segoe UI", 10))
        self.detail_text.pack(side="left", fill="both", expand=True)
        self.thumb_label = ttk.Label(dl)
        self.thumb_label.pack(side="left", padx=6)
        self.thumb_label.bind("<Double-1>", self._open_image)
        lg = ttk.LabelFrame(detail, text="Günlük", padding=4)
        lg.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.log_text = tk.Text(lg, height=8, wrap="word", font=("Consolas", 9), state="disabled")
        self.log_text.pack(fill="both", expand=True)
        paned.add(detail, weight=1)

    def _build_web_tab(self) -> None:
        tab = ttk.Frame(self.nb, padding=6)
        self.nb.add(tab, text="WhatsApp oturumu (önerilen)")
        info = ("WhatsApp hesabınız, uygulamanın açtığı Edge penceresine 'bağlı cihaz' olarak eklenir "
                "(WhatsApp Desktop ile aynı hesap ve sohbetler). İlk seferde QR kodu okutun.")
        ttk.Label(tab, text=info, wraplength=560, foreground="#555").pack(anchor="w")
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=4)
        ttk.Button(row, text="WhatsApp'a bağlan", command=self._connect_web).pack(side="left")
        ttk.Button(row, text="Sohbetleri listele", command=self._list_chats).pack(side="left", padx=4)
        ttk.Label(row, text="  Filtre:").pack(side="left")
        self.chat_filter = tk.StringVar()
        self.chat_filter.trace_add("write", lambda *a: self._refresh_chat_list())
        ttk.Entry(row, textvariable=self.chat_filter, width=20).pack(side="left")

        lf = ttk.Frame(tab)
        lf.pack(fill="both", expand=True)
        self.chat_list = tk.Listbox(lf, selectmode="extended", height=8, exportselection=False)
        sb = ttk.Scrollbar(lf, command=self.chat_list.yview)
        self.chat_list.configure(yscrollcommand=sb.set)
        self.chat_list.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        ttk.Label(tab, text="Ctrl / Shift ile birden çok sohbet ve grup seçebilirsiniz.",
                  foreground="#555").pack(anchor="w")

        opt = ttk.Frame(tab)
        opt.pack(fill="x", pady=(4, 0))
        ttk.Label(opt, text="Sohbet başına en fazla mesaj:").pack(side="left")
        self.max_msgs = tk.IntVar(value=500)
        ttk.Spinbox(opt, from_=50, to=20000, increment=50, textvariable=self.max_msgs, width=7).pack(side="left", padx=4)
        ttk.Label(opt, text="  Şu tarihten itibaren (gg.aa.yyyy, boş = hepsi):").pack(side="left")
        self.since = tk.StringVar()
        ttk.Entry(opt, textvariable=self.since, width=12).pack(side="left", padx=4)

    def _build_screen_tab(self) -> None:
        tab = ttk.Frame(self.nb, padding=6)
        self.nb.add(tab, text="WhatsApp Desktop ekran tarama")
        info = ("Açık olan WhatsApp Desktop penceresi öne getirilir, sohbet yukarı kaydırılarak ekran görüntüleri "
                "alınır ve Claude tarafından okunur (yazılar + görseller). Tarama sırasında fare/klavyeye dokunmayın.")
        ttk.Label(tab, text=info, wraplength=560, foreground="#555").pack(anchor="w")
        ttk.Label(tab, text="Taranacak sohbet / grup adları (her satıra bir tane):").pack(anchor="w", pady=(6, 0))
        self.screen_chats = tk.Text(tab, height=6, width=50)
        self.screen_chats.pack(fill="both", expand=True)
        opt = ttk.Frame(tab)
        opt.pack(fill="x", pady=4)
        self.manual_open = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="Sohbetleri kendim açacağım (otomatik arama yerine)",
                        variable=self.manual_open).pack(side="left")
        ttk.Label(opt, text="   Sohbet başına en fazla ekran:").pack(side="left")
        self.max_pages = tk.IntVar(value=20)
        ttk.Spinbox(opt, from_=1, to=300, textvariable=self.max_pages, width=5).pack(side="left", padx=4)

    def _build_export_tab(self) -> None:
        tab = ttk.Frame(self.nb, padding=6)
        self.nb.add(tab, text="Dışa aktarılmış sohbet (.zip)")
        info = ("Telefonda sohbet → ⋮ → Diğer → Sohbeti dışa aktar → 'Medya dahil' ile alınan .zip dosyalarını "
                "ekleyin. En eksiksiz ve en hızlı yöntemdir (tüm geçmiş + tam çözünürlüklü görseller).")
        ttk.Label(tab, text=info, wraplength=560, foreground="#555").pack(anchor="w")
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=4)
        ttk.Button(row, text="Dosya ekle (.zip / .txt)", command=self._add_export_files).pack(side="left")
        ttk.Button(row, text="Klasör ekle", command=self._add_export_dir).pack(side="left", padx=4)
        ttk.Button(row, text="Listeyi temizle", command=self._clear_exports).pack(side="left")
        self.export_list = tk.Listbox(tab, height=8)
        self.export_list.pack(fill="both", expand=True)

    def _toggle_key(self) -> None:
        if BACKENDS.get(self.backend_var.get()) == "api":
            self.key_row.pack(fill="x")
        else:
            self.key_row.pack_forget()

    # ================================================================== ayarlar
    def _load_settings(self) -> None:
        try:
            s = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            s = {}
        self.criterion.insert("1.0", s.get("criterion", ""))
        self.mode.set(s.get("mode", "semantic"))
        self.scan_images.set(s.get("scan_images", True))
        self.backend_var.set(s.get("backend", list(BACKENDS)[0]))
        self.model_var.set(s.get("model", DEFAULT_MODEL))
        self.max_msgs.set(s.get("max_msgs", 500))
        self.screen_chats.insert("1.0", s.get("screen_chats", ""))
        self._toggle_key()

    def _save_settings(self) -> None:
        s = {
            "criterion": self.criterion.get("1.0", "end").strip(),
            "mode": self.mode.get(),
            "scan_images": self.scan_images.get(),
            "backend": self.backend_var.get(),
            "model": self.model_var.get(),
            "max_msgs": self._int(self.max_msgs, 500),
            "screen_chats": self.screen_chats.get("1.0", "end").strip(),
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

    # ================================================================== yardımcılar
    def log(self, msg: str) -> None:
        self.events.put(("log", msg))

    def _append_log(self, msg: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{datetime.now():%H:%M:%S}] {msg}\n")
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
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")
        if not running:
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
            self._ensure_session()
            self.events.put(("chats", self.session.list_chats()))  # type: ignore[union-attr]
            return "WhatsApp'a bağlanıldı, sohbetler listelendi."
        self._run_bg(job)

    def _list_chats(self) -> None:
        def job():
            chats = self._ensure_session().list_chats()
            self.events.put(("chats", chats))
            return f"{len(chats)} sohbet bulundu."
        self._run_bg(job)

    def _refresh_chat_list(self) -> None:
        selected = {self.chat_list.get(i) for i in self.chat_list.curselection()}
        f = self.chat_filter.get().lower().strip()
        self.chat_list.delete(0, "end")
        for name in self.all_chats:
            if f in name.lower():
                self.chat_list.insert("end", name)
                if name in selected:
                    self.chat_list.selection_set("end")

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
            self.export_list.insert("end", str(p))

    def _clear_exports(self) -> None:
        self.export_files.clear()
        self.export_list.delete(0, "end")

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
            backend = self._make_backend(True)
            self.log("Claude bağlantısı test ediliyor…")
            return backend.check()
        self._run_bg(job)

    def _start_scan(self) -> None:
        criterion = self._criterion()
        if not criterion:
            messagebox.showwarning(APP_NAME, "Lütfen aranacak tabiri / kriteri yazın.")
            return
        self._save_settings()
        tab = self.nb.index(self.nb.select())
        opts = MatchOptions(criterion=criterion, mode=self.mode.get(), scan_images=self.scan_images.get())
        needs_ai = opts.mode == "semantic" or opts.scan_images

        if tab == 0:
            chats = [self.chat_list.get(i) for i in self.chat_list.curselection()]
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
        elif tab == 1:
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
    def _add_match(self, m: Match) -> None:
        self.matches.append(m)
        msg = m.message
        text = m.display_text().replace("\n", " ")
        self.tree.insert("", "end", iid=str(len(self.matches) - 1),
                         values=(msg.chat, msg.timestamp, msg.sender, m.kind, text[:400], m.reason))
        self.title(f"{APP_NAME} – {len(self.matches)} sonuç")

    def _clear_results(self) -> None:
        self.matches.clear()
        self.tree.delete(*self.tree.get_children())
        self.detail_text.delete("1.0", "end")
        self.thumb_label.configure(image="")
        self.title(f"{APP_NAME} {__version__}")

    def _selected(self) -> Optional[Match]:
        sel = self.tree.selection()
        return self.matches[int(sel[0])] if sel else None

    def _show_detail(self, _e=None) -> None:
        m = self._selected()
        if not m:
            return
        msg = m.message
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert(
            "1.0",
            f"Sohbet: {msg.chat}\nTarih: {msg.timestamp}\nGönderen: {msg.sender}\nTür: {m.kind}\n"
            f"Gerekçe: {m.reason}\n\n{msg.text}\n"
            + (f"\n[Görseldeki yazı]\n{m.ocr_text}\n" if m.ocr_text else "")
            + (f"\n[Görsel tarifi]\n{m.image_description}\n" if m.image_description else ""),
        )
        self.thumb_label.configure(image="")
        if msg.image_path and Path(msg.image_path).exists():
            try:
                from PIL import Image, ImageTk

                with Image.open(msg.image_path) as im:
                    im = im.convert("RGB")
                    im.thumbnail((260, 180))
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
        reverse = getattr(self, "_sort_rev", False)
        items.sort(reverse=reverse)
        for idx, (_, k) in enumerate(items):
            self.tree.move(k, "", idx)
        self._sort_rev = not reverse

    def _ordered_matches(self) -> list[Match]:
        return [self.matches[int(k)] for k in self.tree.get_children("")]

    def _save_csv(self) -> None:
        if not self.matches:
            messagebox.showinfo(APP_NAME, "Kaydedilecek sonuç yok.")
            return
        p = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV (Excel)", "*.csv")],
                                         initialfile=f"whatsapp_sonuclar_{datetime.now():%Y%m%d_%H%M}.csv")
        if p:
            save_csv(self._ordered_matches(), Path(p))
            self._append_log(f"CSV kaydedildi: {p}")

    def _save_html(self) -> None:
        if not self.matches:
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
