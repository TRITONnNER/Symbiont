"""
manifest.py — сборка и подпись манифеста (узлы + правила + категории).
Подпись Ed25519 над канонической сериализацией тела БЕЗ поля sig.
Клиент отвергает манифест без валидной подписи. См. schema/manifest.schema.json.
"""
from __future__ import annotations
import base64, json, time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def canonical(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign_manifest(body: dict, sk: Ed25519PrivateKey) -> dict:
    """Возвращает манифест с полем sig='ed25519:<base64>'. body НЕ должен содержать sig."""
    sig = sk.sign(canonical(body))
    return {**body, "sig": "ed25519:" + base64.b64encode(sig).decode()}


def verify_manifest(manifest: dict, pk: Ed25519PublicKey) -> bool:
    m = dict(manifest)
    sig_field = m.pop("sig", "")
    if not sig_field.startswith("ed25519:"):
        return False
    try:
        pk.verify(base64.b64decode(sig_field[len("ed25519:"):]), canonical(m))
        return True
    except Exception:
        return False


def demo_manifest(version: int = 185) -> dict:
    """Демо-тело манифеста (без подписи). Узлы/правила — иллюстративные."""
    return {
        "version": version,
        "issuedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "nodes": [
            {"id": "nl-01", "country": "Netherlands", "code": "NL", "loadPct": 18,
             "protocols": ["reality", "hysteria2"], "roles": ["edge"], "xrayCore": True},
            {"id": "fi-01", "country": "Finland", "code": "FI", "loadPct": 27,
             "protocols": ["reality"], "roles": ["edge"], "xrayCore": True},
            {"id": "de-01", "country": "Germany", "code": "DE", "loadPct": 42,
             "protocols": ["reality", "ss2022"], "roles": ["edge", "backbone"], "xrayCore": True},
            # relay с «белым» РФ-IP: foreign-узлы уходят через него (detour) против
            # whitelist. Анонимности НЕ даёт (юрисдикция РФ/СОРМ) — только обход CIDR.
            {"id": "ru-relay-01", "country": "Russia", "code": "RU", "loadPct": 12,
             "protocols": ["reality"], "roles": ["relay"], "xrayCore": True,
             "whiteIp": True, "provider": "yandex-cloud"},
        ],
        "rules": [
            {"kind": "domainSuffix", "match": "sberbank.ru", "action": "direct"},
            {"kind": "tld", "match": ".gov.ru", "action": "direct"},
            {"kind": "domainSuffix", "match": "gosuslugi.ru", "action": "direct"},
            {"kind": "domainSuffix", "match": "youtube.com", "action": "bypass"},
        ],
        "categories": {
            "ads": ["doubleclick.net", "googlesyndication.com"],
            "trackers": ["google-analytics.com"],
            "threats": ["known-bad.example"],
            "directApps": ["bank", "gov", "marketplace"],
            "bypassApps": ["messenger", "video"],
            "boostApps": ["game", "voice"],
        },
        # Анти-DPI параметры для движка (по research 31.05.2026). Применяются в
        # singbox_config: ротация отпечатка, health-check >32 КБ, деградация каскада.
        "antiDpi": {
            # chrome ОБЯЗАН соответствовать живому релизу — устаревший отпечаток
            # сам становится сигнатурой (урок Telegram MTProto, май 2026).
            "fingerprintPool": ["chrome", "firefox", "safari", "edge", "ios"],
            "targetChromeVersion": 148,
            "minUtlsVersion": "1.8.2",   # ниже — CVE-2026-26995 / CVE-2026-27017
            "rotation": "per-session",   # не per-connection (постоянная смена = аномалия)
            # generate_204 (<16 КБ) прошёл бы сквозь «занавес» — тянем >32 КБ.
            "healthCheckUrl": "https://speed.cloudflare.com/__down?bytes=50000",
            "healthCheckMinBytes": 32768,
            "cascade": ["reality-xhttp", "hysteria2", "ss2022"],
            "udpFallbackToTcp": True,    # при блокировке UDP — на TCP-узлы
            "canaryPercent": 0,          # доля пользователей в пробной раскатке конфига
        },
    }
