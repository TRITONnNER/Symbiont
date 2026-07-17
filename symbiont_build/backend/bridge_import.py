"""bridge_import.py — разбор пользовательской ссылки «свой мост» в нормализованный
узел каскада. ТОЛЬКО парсинг + валидация, без сети.

Поддерживаем ровно те протоколы, что умеет движок Симбионта (allow-list):
  • vless://           — VLESS (+Reality или TLS)     → protocol "reality"/"vless"
  • ss:// / shadowsocks:// — Shadowsocks (в т.ч. 2022) → protocol "ss2022"/"shadowsocks"
  • hysteria2:// / hy2://  — Hysteria2 (UDP)           → protocol "hysteria2"

Всё прочее (vmess, trojan, socks, ...) отклоняется с понятной причиной.
Приватные адреса (localhost / RFC1918) и insecure-TLS помечаются предупреждением,
но не блокируются жёстко — решение показать/добавить принимает клиент.

Секрет моста на сервере НЕ хранится: функция возвращает разобранный узел вызвавшему,
а клиент держит мост ЛОКАЛЬНО (приватность — как и обещано бренду).
"""
from __future__ import annotations
import base64, ipaddress
from urllib.parse import urlparse, parse_qs, unquote

# scheme → допустим ли к импорту
_ALLOWED = {"vless", "ss", "shadowsocks", "hysteria2", "hy2"}


class BridgeError(ValueError):
    """Ошибка разбора с машинным кодом причины (см. _REASONS)."""


_REASONS = {
    "empty_or_malformed": "Пустая или неполная ссылка",
    "protocol_not_supported": "Протокол не поддерживается (нужен vless / ss / hysteria2)",
    "incomplete": "В ссылке не хватает адреса, порта или ключа",
    "reality_missing_pubkey": "Reality без public key (pbk) — ссылка неполная",
    "security_unsupported": "Тип security в ссылке не поддерживается",
    "bad_port": "Порт указан неверно",
    "ss_malformed": "Ссылка Shadowsocks повреждена",
}


def reason_text(code: str) -> str:
    return _REASONS.get(code, code)


def _b64pad(s: str) -> str:
    return s + "=" * (-len(s) % 4)


def _b64try(s: str):
    """Пытаемся декодировать base64 (url-safe и обычный). None, если не base64."""
    for dec in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            return dec(_b64pad(s)).decode("utf-8", "strict")
        except Exception:
            continue
    return None


def _split_hostport(hp: str):
    hp = hp.strip()
    if hp.startswith("["):                       # [ipv6]:port
        host, _, port = hp[1:].partition("]")
        port = port.lstrip(":")
    else:
        host, _, port = hp.rpartition(":")
    if not host or not port:
        raise BridgeError("incomplete")
    try:
        port_i = int(port)
        if not (0 < port_i < 65536):
            raise ValueError
    except ValueError:
        raise BridgeError("bad_port")
    return host, port_i


def _is_private_host(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified
    except ValueError:
        h = host.lower()
        return h == "localhost" or h.endswith(".local") or h.endswith(".internal")


def parse_bridge(uri: str) -> dict:
    """Разобрать ссылку. Бросает BridgeError(code) при ошибке. Иначе возвращает:
    {protocol, server, port, params{...}, name, warnings[], source}."""
    uri = (uri or "").strip()
    if "://" not in uri:
        raise BridgeError("empty_or_malformed")
    scheme = uri.split("://", 1)[0].lower()
    if scheme not in _ALLOWED:
        raise BridgeError("protocol_not_supported")

    if scheme == "vless":
        node = _parse_vless(uri)
    elif scheme in ("ss", "shadowsocks"):
        node = _parse_ss(uri)
    else:                                          # hysteria2 / hy2
        node = _parse_hy2(uri)

    warnings = []
    if _is_private_host(node["server"]):
        warnings.append("private_address")
    if node["params"].get("insecure"):
        warnings.append("insecure_tls")
    node["warnings"] = warnings
    node["source"] = "user_bridge"
    return node


def _parse_vless(uri: str) -> dict:
    u = urlparse(uri)
    if not (u.username and u.hostname and u.port):
        raise BridgeError("incomplete")
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    security = (q.get("security") or "").lower()
    params: dict = {"uuid": u.username}
    if q.get("sni") or q.get("host"):
        params["sni"] = q.get("sni") or q.get("host")
    if q.get("fp"):
        params["fingerprint"] = q["fp"]
    if q.get("flow"):
        params["flow"] = q["flow"]
    ttype = (q.get("type") or "tcp").lower()
    if ttype in ("xhttp", "http"):
        params["transport"] = ttype
        if q.get("path"):
            params["path"] = q["path"]
    if security == "reality":
        if not q.get("pbk"):
            raise BridgeError("reality_missing_pubkey")
        params["public_key"] = q["pbk"]
        params["short_id"] = q.get("sid", "")
        protocol = "reality"
    elif security in ("tls", ""):
        protocol = "vless"
    else:
        raise BridgeError("security_unsupported")
    return {"protocol": protocol, "server": u.hostname, "port": u.port,
            "params": params, "name": unquote(u.fragment or "")}


def _parse_ss(uri: str) -> dict:
    body = uri.split("://", 1)[1]
    frag = ""
    if "#" in body:
        body, frag = body.split("#", 1)
    if "@" in body:                                # форма A: userinfo@host:port
        userinfo, hostport = body.rsplit("@", 1)
        dec = _b64try(userinfo) or userinfo        # userinfo может быть base64 или plain
        if ":" not in dec:
            raise BridgeError("ss_malformed")
        method, password = dec.split(":", 1)
        host, port = _split_hostport(hostport)
    else:                                          # форма B: base64(method:pass@host:port)
        dec = _b64try(body)
        if not dec or "@" not in dec:
            raise BridgeError("ss_malformed")
        userinfo, hostport = dec.rsplit("@", 1)
        if ":" not in userinfo:
            raise BridgeError("ss_malformed")
        method, password = userinfo.split(":", 1)
        host, port = _split_hostport(hostport)
    protocol = "ss2022" if method.lower().startswith("2022-") else "shadowsocks"
    return {"protocol": protocol, "server": host, "port": port,
            "params": {"method": method, "password": password}, "name": unquote(frag)}


def _parse_hy2(uri: str) -> dict:
    u = urlparse(uri)
    if not (u.hostname and u.port):
        raise BridgeError("incomplete")
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    params: dict = {"password": unquote(u.username or q.get("password", ""))}
    if q.get("sni"):
        params["sni"] = q["sni"]
    obfs = q.get("obfs-password") or (q.get("obfs") if q.get("obfs") not in (None, "salamander") else None)
    if obfs:
        params["obfs"] = obfs
    if str(q.get("insecure", "")).lower() in ("1", "true"):
        params["insecure"] = True
    return {"protocol": "hysteria2", "server": u.hostname, "port": u.port,
            "params": params, "name": unquote(u.fragment or "")}
