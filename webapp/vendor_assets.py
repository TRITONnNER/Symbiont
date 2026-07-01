#!/usr/bin/env python3
# Rewrites external CDN references (Google Fonts, flagcdn, simpleicons) to the
# local vendored copies so the web shell renders 100% offline — important for
# the RF audience where these CDNs are frequently blocked.
import re, sys, io, os

ROOT = os.path.dirname(os.path.abspath(__file__))

TARGETS = [
    "Симбионт.dc.html",
    "Card.dc.html",
    "ConnectionCore.dc.html",
    "ListRow.dc.html",
    "Picker.dc.html",
]

FONT_LINK_RE = re.compile(r'.*<link[^>]*fonts\.(googleapis|gstatic)\.com[^>]*>.*')
SIMPLEICONS_RE = re.compile(r'https://cdn\.simpleicons\.org/([a-zA-Z0-9._-]+)')
FLAGCDN = "https://flagcdn.com/w40/"
FONTS_CSS_LINK = '<link rel="stylesheet" href="./fonts.css">'

def patch(path):
    full = os.path.join(ROOT, path)
    with io.open(full, "r", encoding="utf-8") as f:
        text = f.read()

    changed = {"font_links": 0, "flag": 0, "icon": 0, "fonts_css_added": 0}

    # 1) Drop every <link> that points at Google Fonts CDNs (preconnect + css2).
    out_lines = []
    dropped_font_block = False
    for line in text.split("\n"):
        if FONT_LINK_RE.match(line):
            changed["font_links"] += 1
            dropped_font_block = True
            # Emit a single local fonts.css link at the first dropped position,
            # but only if the file does not already reference it.
            if FONTS_CSS_LINK not in text and "./fonts.css" not in "\n".join(out_lines):
                indent = line[: len(line) - len(line.lstrip())]
                out_lines.append(indent + FONTS_CSS_LINK)
                changed["fonts_css_added"] += 1
            continue
        out_lines.append(line)
    text = "\n".join(out_lines)

    # 2) flagcdn -> local ./flags/
    n = text.count(FLAGCDN)
    if n:
        text = text.replace(FLAGCDN, "./flags/")
        changed["flag"] = n

    # 3) simpleicons -> local ./icons/<slug>.svg
    def _icon(m):
        changed["icon"] += 1
        return "./icons/%s.svg" % m.group(1)
    text = SIMPLEICONS_RE.sub(_icon, text)

    with io.open(full, "w", encoding="utf-8") as f:
        f.write(text)
    return changed

if __name__ == "__main__":
    for t in TARGETS:
        c = patch(t)
        print("%-24s font_links_dropped=%d fonts_css_added=%d flag=%d icon=%d"
              % (t, c["font_links"], c["fonts_css_added"], c["flag"], c["icon"]))
