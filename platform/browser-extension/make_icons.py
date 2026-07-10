#!/usr/bin/env python3
"""Генерирует иконки расширения (mint-квадрат с ∞) без внешних зависимостей.
   python make_icons.py  →  icons/icon16.png, icon48.png, icon128.png
"""
import os
import zlib
import struct
import math

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
MINT = (0x34, 0xE5, 0xB0)
DARK = (0x06, 0x23, 0x1A)


def _png(width, height, pixels):
    def chunk(typ, data):
        c = typ + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter: none
        for x in range(width):
            r, g, b, a = pixels[y * width + x]
            raw += bytes((r, g, b, a))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def render(size):
    px = []
    cy = size / 2.0
    R = size * 0.19          # радиус петли
    T = max(1.2, size * 0.072)  # толщина линии
    cxl = size / 2.0 - R
    cxr = size / 2.0 + R
    rad = size * 0.22        # скругление квадрата
    for y in range(size):
        for x in range(size):
            # скругление углов → прозрачный фон
            dx = min(x, size - 1 - x)
            dy = min(y, size - 1 - y)
            corner = (dx < rad and dy < rad and
                      math.hypot(rad - dx, rad - dy) > rad)
            if corner:
                px.append((0, 0, 0, 0))
                continue
            dl = abs(math.hypot(x - cxl, y - cy) - R)
            dr = abs(math.hypot(x - cxr, y - cy) - R)
            # ограничим "внешние" края петель, чтобы вышла ∞, а не два кольца
            on_left = dl < T and x <= size / 2.0 + T
            on_right = dr < T and x >= size / 2.0 - T
            if on_left or on_right:
                px.append((DARK[0], DARK[1], DARK[2], 255))
            else:
                px.append((MINT[0], MINT[1], MINT[2], 255))
    return _png(size, size, px)


def main():
    os.makedirs(OUT, exist_ok=True)
    for s in (16, 48, 128):
        with open(os.path.join(OUT, "icon%d.png" % s), "wb") as f:
            f.write(render(s))
        print("wrote icons/icon%d.png" % s)


if __name__ == "__main__":
    main()
