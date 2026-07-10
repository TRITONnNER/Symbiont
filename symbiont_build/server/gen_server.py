#!/usr/bin/env python3
"""
gen_server.py — генератор серверной конфигурации Симбионта.

Создаёт ВСЁ, что нужно одному VPN-серверу, на одном IP, тремя протоколами:
  • VLESS + Reality   (TCP 443)  — основной обход DPI;
  • Hysteria2         (UDP 8443) — скорость/UDP/игры;
  • Shadowsocks-2022  (TCP 9443) — резерв.

Выдаёт:
  1) server config.json для sing-box (кладётся на сервер);
  2) набор секретов (Reality keypair, uuid, пароли) — secrets.json;
  3) запись узла в формате манифеста нашего бэкенда (manifest_node.json),
     чтобы клиент Симбионт увидел узел без ручной правки.

Зависимости для генерации Reality-ключей и uuid — только стандартная python+cryptography
(та же, что уже в backend). Сам sing-box на сервере не требуется для генерации.

Использование:
  python3 gen_server.py --host 203.0.113.10 --code NL --country Netherlands \
      --sni www.microsoft.com --out ./out
"""
from __future__ import annotations
import argparse, base64, json, os, secrets, sys, uuid as uuidlib
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives import serialization


# ── Reality keypair (X25519) ────────────────────────────────────────────────
def reality_keypair() -> tuple[str, str]:
    """Возвращает (private_b64url, public_b64url) для Reality (как у sing-box generate)."""
    sk = X25519PrivateKey.generate()
    priv = sk.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub = sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    b64u = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
    return b64u(priv), b64u(pub)


def short_id() -> str:
    """Reality short_id — 8 байт hex (16 символов)."""
    return secrets.token_hex(8)


def gen_password(nbytes: int = 16) -> str:
    return base64.b64encode(secrets.token_bytes(nbytes)).decode()


def ss2022_key() -> str:
    """Ключ для 2022-blake3-aes-128-gcm — base64 от 16 байт."""
    return base64.b64encode(secrets.token_bytes(16)).decode()


# ── сборка server config.json (схема sing-box 1.13) ─────────────────────────
def build_server_config(args, sec) -> dict:
    return {
        "log": {"level": "warn", "timestamp": True},
        "inbounds": [
            # 1) VLESS + Reality (основной)
            {
                "type": "vless",
                "tag": "vless-reality",
                "listen": "::",
                "listen_port": args.reality_port,
                "users": [{"uuid": sec["uuid"], "flow": ""}],  # flow пуст: совместимо с XHTTP/h2-фолбэком клиента
                "tls": {
                    "enabled": True,
                    "server_name": args.sni,
                    "reality": {
                        "enabled": True,
                        "handshake": {"server": args.sni, "server_port": 443},
                        "private_key": sec["reality_private"],
                        "short_id": [sec["reality_short_id"]],
                    },
                },
            },
            # 2) Hysteria2 (скорость/UDP)
            {
                "type": "hysteria2",
                "tag": "hysteria2",
                "listen": "::",
                "listen_port": args.hysteria_port,
                "users": [{"password": sec["hysteria_password"]}],
                "masquerade": "https://www.bing.com",
                "tls": {
                    "enabled": True,
                    "alpn": ["h3"],
                    # самоподписанный серт генерируется install.sh через openssl;
                    # пути подставляются туда же.
                    "certificate_path": args.cert_path,
                    "key_path": args.key_path,
                },
            },
            # 3) Shadowsocks-2022 (резерв)
            {
                "type": "shadowsocks",
                "tag": "ss2022",
                "listen": "::",
                "listen_port": args.ss_port,
                "method": "2022-blake3-aes-128-gcm",
                "password": sec["ss_password"],
            },
        ],
        "outbounds": [
            {"type": "direct", "tag": "direct"},
            {"type": "block", "tag": "block"},
        ],
        "route": {
            "rules": [
                # блокируем приватные диапазоны от утечек наружу через сервер
                {"ip_is_private": True, "outbound": "block"},
            ],
            "final": "direct",
        },
    }


# ── запись узла для манифеста бэкенда ───────────────────────────────────────
def build_manifest_node(args, sec) -> dict:
    """Формат совпадает с manifest.py demo_manifest()['nodes'][i] + транспорт-секреты
    в params, которые клиентский singbox_config ждёт из манифеста."""
    is_relay = args.role == "relay"
    node = {
        "id": args.node_id,
        "country": args.country,
        "code": args.code,
        "loadPct": 0,
        "protocols": ["reality", "hysteria2", "ss2022"],
        # роль узла: edge — точка выхода (видна пользователю); relay — «белый» IP для
        # detour против CIDR-whitelist (клиент его прячет из списка узлов).
        "roles": ["relay"] if is_relay else ["edge"],
        "xrayCore": False,
        "host": args.host,             # для реального пинга в клиенте
        "port": args.reality_port,
        # транспорт-параметры (клиент подставит в outbound). НЕ секретный приватный ключ!
        "transport": {
            "reality": {
                "server": args.host, "port": args.reality_port, "uuid": sec["uuid"],
                "sni": args.sni, "public_key": sec["reality_public"],
                "short_id": sec["reality_short_id"],
            },
            "hysteria2": {
                "server": args.host, "port": args.hysteria_port,
                "password": sec["hysteria_password"], "sni": args.sni,
            },
            "ss2022": {
                "server": args.host, "port": args.ss_port,
                "method": "2022-blake3-aes-128-gcm", "password": sec["ss_password"],
            },
        },
    }
    if is_relay:
        node["whiteIp"] = True   # «белый» внутрироссийский IP (для detour)
    return node


def assert_legal_role(role: str, code: str) -> None:
    """Юридический предохранитель (блюпринт, раздел юр-рисков РФ). Публичный exit-узел
    внутри РФ недопустим: оператор становится «контролёром» трафика — СОРМ на ISP,
    обязанности ОРИ/FGIS, ст. 13.52/13.53 КоАП, VPN как отягчающее по ст. 63 УК.
    relay в РФ разрешён (это НЕ точка выхода — только обход CIDR-whitelist), но с
    предупреждением: анонимности он не даёт (СОРМ видит метаданные)."""
    is_ru = (code or "").strip().upper() == "RU"
    if role in ("node", "edge", "exit") and is_ru:
        raise SystemExit(
            "ОТКАЗ: публичный exit-узел в РФ недопустим. Оператор становится «контролёром»\n"
            "трафика (СОРМ на ISP, обязанности ОРИ/FGIS, ст. 13.52/13.53 КоАП, VPN как\n"
            "отягчающее по ст. 63 УК). Размести exit ВНЕ РФ. Для обхода CIDR-whitelist\n"
            "внутри РФ используй --role relay — это НЕ точка выхода и анонимности не даёт."
        )
    if role == "relay" and is_ru:
        print("[symbiont] ВНИМАНИЕ: relay в РФ обходит CIDR-whitelist, но НЕ даёт "
              "анонимности\n           (СОРМ на ISP видит метаданные). Используй только "
              "эфемерный RAM-only узел.")


def main():
    ap = argparse.ArgumentParser(description="Генератор серверной конфигурации Симбионта")
    ap.add_argument("--host", help="публичный IP или домен сервера (не нужен при --localhost)")
    ap.add_argument("--code", default="NL", help="ISO-код страны (для флага), напр. NL")
    ap.add_argument("--country", default="Netherlands", help="название страны")
    ap.add_argument("--node-id", default=None, help="id узла (по умолчанию <code>-01)")
    ap.add_argument("--role", default="node", choices=["node", "relay"],
                    help="роль: node — точка выхода (вне РФ!); relay — «белый» IP для "
                         "обхода CIDR-whitelist (не точка выхода)")
    ap.add_argument("--sni", default="www.microsoft.com",
                    help="домен для Reality-маскировки (должен реально отвечать по 443)")
    ap.add_argument("--reality-port", type=int, default=443)
    ap.add_argument("--hysteria-port", type=int, default=8443)
    ap.add_argument("--ss-port", type=int, default=9443)
    ap.add_argument("--cert-path", default="/etc/symbiont/cert.pem")
    ap.add_argument("--key-path", default="/etc/symbiont/key.pem")
    ap.add_argument("--out", default="./out", help="каталог вывода")
    ap.add_argument("--localhost", action="store_true",
                    help="ЭКСПЕРИМЕНТ: ПК как сервер. host=127.0.0.1, узел id='local-pc' — "
                         "поднять сервер и клиент на одной машине для проверки связки.")
    args = ap.parse_args()

    if args.localhost:
        args.host = "127.0.0.1"
        args.code = "PC"
        args.country = "My PC (experiment)"
        args.node_id = "local-pc"

    if not args.host:
        ap.error("укажи --host <IP/домен> (или используй --localhost для эксперимента на ПК)")

    if not args.node_id:
        args.node_id = f"{args.code.lower()}-01"

    # ЮРИДИЧЕСКИЙ ПРЕДОХРАНИТЕЛЬ: не генерировать публичный exit-узел в РФ.
    assert_legal_role(args.role, args.code)

    # секреты
    rpriv, rpub = reality_keypair()
    sec = {
        "uuid": str(uuidlib.uuid4()),
        "reality_private": rpriv,
        "reality_public": rpub,
        "reality_short_id": short_id(),
        "hysteria_password": gen_password(16),
        "ss_password": ss2022_key(),
    }

    os.makedirs(args.out, exist_ok=True)
    server_cfg = build_server_config(args, sec)
    node = build_manifest_node(args, sec)

    with open(os.path.join(args.out, "config.json"), "w", encoding="utf-8") as f:
        json.dump(server_cfg, f, indent=2, ensure_ascii=False)
    with open(os.path.join(args.out, "secrets.json"), "w", encoding="utf-8") as f:
        json.dump(sec, f, indent=2, ensure_ascii=False)
    with open(os.path.join(args.out, "manifest_node.json"), "w", encoding="utf-8") as f:
        json.dump(node, f, indent=2, ensure_ascii=False)

    print("OK. Generated in", args.out)
    print("  config.json        - sing-box server config (deploy to: /etc/symbiont/config.json)")
    print("  secrets.json       - private keys/passwords (KEEP SECRET, do not commit)")
    print("  manifest_node.json - node entry for backend manifest (lets the client see the node)")
    print()
    print(f"  Node: {node['id']}  {args.country} ({args.code})  host={args.host}")
    print(f"  Reality :{args.reality_port}  Hysteria2 :{args.hysteria_port}  SS2022 :{args.ss_port}")


if __name__ == "__main__":
    main()
