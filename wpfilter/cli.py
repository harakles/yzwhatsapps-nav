"""Komut satırı kullanımı (dışa aktarılmış sohbetler için).

Örnek:
    python -m wpfilter.cli "Sohbet.zip" -q "fatura veya ödeme" -o sonuc.html
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .claude_backend import DEFAULT_MODEL, make_backend
from .matcher import MatchOptions, MessageMatcher
from .models import Match
from .report import save_csv, save_html
from .sources.export_zip import parse_export


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="WhatsApp dışa aktarımlarında tabir arama (Claude ile)")
    ap.add_argument("files", nargs="+", help=".zip / .txt / klasör")
    ap.add_argument("-q", "--query", required=True, help="Aranacak tabir / kriter")
    ap.add_argument("--keyword", action="store_true", help="Yapay zekâsız kelime araması (metinler)")
    ap.add_argument("--no-images", action="store_true", help="Görselleri tarama")
    ap.add_argument("--backend", choices=["cli", "api"], default="cli", help="cli = Claude aboneliği (varsayılan)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("-o", "--output", help="Sonuç dosyası (.csv veya .html)")
    args = ap.parse_args(argv)

    opts = MatchOptions(criterion=args.query, mode="keyword" if args.keyword else "semantic",
                        scan_images=not args.no_images)
    needs_ai = opts.mode == "semantic" or opts.scan_images
    backend = make_backend(args.backend, args.model) if needs_ai else None

    matches: list[Match] = []

    def on_match(m: Match) -> None:
        matches.append(m)
        print(f"[{m.message.chat}] {m.message.timestamp} {m.message.sender} ({m.kind}): "
              f"{m.display_text()[:200]}  ->  {m.reason}")

    matcher = MessageMatcher(backend, opts, on_match=on_match,
                             on_progress=lambda msg, p: print(msg, file=sys.stderr) if p < 0 else None)
    for f in args.files:
        msgs = parse_export(f)
        print(f"{f}: {len(msgs)} mesaj okundu", file=sys.stderr)
        matcher.run(msgs)

    print(f"\nToplam {len(matches)} sonuç.", file=sys.stderr)
    if args.output:
        out = Path(args.output)
        (save_html(matches, out, args.query) if out.suffix.lower() == ".html" else save_csv(matches, out))
        print(f"Kaydedildi: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
