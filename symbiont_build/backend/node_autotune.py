#!/usr/bin/env python3
"""
node_autotune.py — авто-подбор «лучших показателей» для узла Server-in-a-Box.

Запускается на свежем VPS ДО gen_server.py и выдаёт параметры, по которым узел
сам настроится оптимально под СВОЮ сеть, а не по статичным дефолтам:

  • гео                — страна/код по IP (убирает ручной --country);
  • лучший SNI         — из пула кандидатов берём живой TLS1.3 с наименьшей задержкой;
  • протоколы          — Hysteria2 (UDP) включаем только если UDP-egress жив, иначе TCP;
  • уникальный id      — nl-<hash6(host)> вместо nl-01 (узлы не затирают друг друга);
  • стартовый loadPct  — из замера ёмкости (для балансировки на клиенте).

РАЗДЕЛЕНИЕ:
  • Чистая логика выбора (pick_best_sni / decide_protocols / unique_node_id /
    load_from_capacity) — детерминирована и покрыта тестами (selftest_autotune.py).
  • Живые пробы (probe_sni / test_udp_egress / detect_geo / measure_capacity) — ходят
    в сеть и выполняются НА VPS (в среде сборки нет нужного egress). Здесь они есть,
    но проверяются на реальном сервере.

Вывод main() — JSON с выбранными параметрами, который читает bootstrap_vps.sh и
передаёт в gen_server.py.
"""
from __future__ import annotations
import hashlib
import json
import socket
import ssl
import sys
import time
from typing import Optional

# Пул кандидатов SNI для Reality-маскировки: высокорепутационные TLS1.3/CDN-домены,
# которые реально отвечают по 443 и которые «дорого» блокировать целиком. Узел сам
# выберет из них живой с наименьшей задержкой ИМЕННО со своей сети.
SNI_CANDIDATES = [
    "www.microsoft.com", "www.apple.com", "www.cloudflare.com", "dl.google.com",
    "www.amazon.com", "www.bing.com", "vh.akamaihd.net", "www.icloud.com",
    "www.tesla.com", "www.samsung.com",
]


# ── ЧИСТАЯ ЛОГИКА ВЫБОРА (тестируется) ────────────────────────────────────────
def pick_best_sni(results: list[dict]) -> Optional[str]:
    """results: [{host, ok, latency_ms}]. Берём живой с минимальной задержкой.
    None — если ни один не отвечает (узел тогда падает на дефолт/ретрай)."""
    alive = [r for r in results if r.get("ok") and r.get("latency_ms") is not None]
    if not alive:
        return None
    return min(alive, key=lambda r: r["latency_ms"])["host"]


def decide_protocols(udp_ok: bool) -> list[str]:
    """UDP жив → полный каскад; UDP мёртв (часто на дешёвых/азиатских VPS) → только TCP,
    чтобы не рекламировать заведомо неработающий Hysteria2."""
    return ["reality", "hysteria2", "ss2022"] if udp_ok else ["reality", "ss2022"]


def unique_node_id(code: str, host: str) -> str:
    """Детерминированный уникальный id: <code>-<6 hex от host>. Один и тот же host →
    тот же id (идемпотентная пере-регистрация), разные host → разные id (нет коллизий
    как у статичного nl-01)."""
    h6 = hashlib.sha256(host.encode()).hexdigest()[:6]
    return f"{(code or 'xx').lower()}-{h6}"


def load_from_capacity(mbps: Optional[float]) -> int:
    """Грубая стартовая нагрузка из замера ёмкости: быстрый канал → ниже стартовый
    loadPct (приоритетнее у клиента). Клампим 5..60. None → нейтральные 30."""
    if mbps is None or mbps <= 0:
        return 30
    if mbps >= 1000:
        return 5
    if mbps >= 500:
        return 10
    if mbps >= 200:
        return 18
    if mbps >= 50:
        return 30
    return 45


# ── ЖИВЫЕ ПРОБЫ (выполняются на VPS; в контейнере не гоняются) ────────────────
def probe_sni(host: str, port: int = 443, timeout: float = 4.0) -> dict:
    """Реальный TLS1.3-хендшейк к кандидату: меряем задержку и что согласован TLS1.3."""
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    t0 = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                ver = ssock.version()
        dt = int((time.time() - t0) * 1000)
        return {"host": host, "ok": ver == "TLSv1.3", "latency_ms": dt, "version": ver}
    except Exception as e:
        return {"host": host, "ok": False, "latency_ms": None, "error": str(e)}


def test_udp_egress(host: str = "1.1.1.1", port: int = 53, timeout: float = 3.0) -> bool:
    """Грубая проверка UDP-egress: DNS-запрос по UDP к публичному резолверу. Если UDP
    зарезан у провайдера/в сети VPS — вернётся False, и Hysteria2 отключим."""
    # минимальный DNS-запрос A-записи google.com (txid 0x1234, RD=1)
    query = (b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
             b"\x06google\x03com\x00\x00\x01\x00\x01")
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        s.sendto(query, (host, port))
        s.recvfrom(512)
        s.close()
        return True
    except Exception:
        return False


def detect_geo(timeout: float = 6.0) -> dict:
    """Страна/код по IP (ip-api.com). На VPS — без аргументов; вернёт {code, country}."""
    import urllib.request
    try:
        with urllib.request.urlopen("http://ip-api.com/json/?fields=countryCode,country",
                                    timeout=timeout) as r:
            d = json.loads(r.read().decode())
        return {"code": d.get("countryCode", "XX"), "country": d.get("country", "Unknown")}
    except Exception:
        return {"code": "XX", "country": "Unknown"}


def measure_capacity(timeout: float = 10.0) -> Optional[float]:
    """Грубая оценка пропускной способности (МБ/с → Мбит/с) скачиванием с CDN-якоря.
    На VPS. None при сбое."""
    import urllib.request
    url = "https://speed.cloudflare.com/__down?bytes=25000000"  # 25 МБ
    try:
        t0 = time.time()
        with urllib.request.urlopen(url, timeout=timeout) as r:
            total = 0
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                total += len(chunk)
        dt = time.time() - t0
        if dt <= 0 or total <= 0:
            return None
        return round((total * 8) / dt / 1e6, 1)  # Мбит/с
    except Exception:
        return None


def autotune(host: str, *, role: str = "node") -> dict:
    """Полный авто-подбор на VPS: гео + лучший SNI + UDP + ёмкость + уникальный id.
    Возвращает параметры для gen_server.py."""
    geo = detect_geo()
    probes = [probe_sni(h) for h in SNI_CANDIDATES]
    best_sni = pick_best_sni(probes) or "www.microsoft.com"
    udp_ok = test_udp_egress()
    protocols = decide_protocols(udp_ok)
    mbps = measure_capacity()
    return {
        "host": host,
        "code": geo["code"],
        "country": geo["country"],
        "node_id": unique_node_id(geo["code"], host),
        "sni": best_sni,
        "protocols": protocols,
        "udp_ok": udp_ok,
        "load_pct": load_from_capacity(mbps),
        "mbps": mbps,
        "sni_probes": probes,
    }


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Авто-подбор лучших параметров узла")
    ap.add_argument("--host", required=True, help="публичный IP/домен узла")
    ap.add_argument("--role", default="node", choices=["node", "relay"])
    args = ap.parse_args()
    result = autotune(args.host, role=args.role)
    # для bootstrap: только машиночитаемый JSON в stdout
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
