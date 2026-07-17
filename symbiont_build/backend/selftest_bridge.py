# backend/selftest_bridge.py — разбор ссылок «свой мост» (vless/ss/hysteria2).
import os, tempfile, sys
os.chdir(tempfile.mkdtemp())
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient
import server, bridge_import

c = TestClient(server.app)
ok = 0
def check(cond, name):
    global ok
    assert cond, f"[FAIL] {name}"
    print(f"[OK] {name}"); ok += 1

def parse(uri):
    return c.post("/v1/config/parse-bridge", json={"uri": uri}).json()

# 1) VLESS + Reality — как в примере с картинки
r = parse("vless://11111111-2222-3333-4444-555555555555@bridge.example.com:443"
          "?type=tcp&security=reality&pbk=PUBKEY123&sid=ab12&sni=www.microsoft.com&fp=chrome#Мой%20мост")
check(r["ok"] and r["node"]["protocol"] == "reality", "vless+reality распознан как reality")
n = r["node"]
check(n["server"] == "bridge.example.com" and n["port"] == 443, "vless: host/port разобраны")
check(n["params"]["public_key"] == "PUBKEY123" and n["params"]["short_id"] == "ab12", "vless: reality pbk/sid")
check(n["params"]["sni"] == "www.microsoft.com" and n["params"]["uuid"].startswith("11111111"), "vless: sni/uuid")
check(n["name"] == "Мой мост", "vless: имя из фрагмента (url-decoded)")

# 2) VLESS + Reality без pbk → ошибка
check(parse("vless://uid@h.com:443?security=reality")["ok"] is False, "vless+reality без pbk → ошибка")

# 3) Shadowsocks 2022 (форма userinfo@host:port, base64 userinfo)
import base64
ui = base64.urlsafe_b64encode(b"2022-blake3-aes-128-gcm:PASSWORD").decode().rstrip("=")
r2 = parse(f"ss://{ui}@ss.example.com:8388#SS")
check(r2["ok"] and r2["node"]["protocol"] == "ss2022", "ss 2022 распознан как ss2022")
check(r2["node"]["params"]["method"].startswith("2022-"), "ss: метод 2022 разобран")
check(r2["node"]["server"] == "ss.example.com" and r2["node"]["port"] == 8388, "ss: host/port")

# 4) Shadowsocks legacy метод → protocol shadowsocks (не 2022)
ui2 = base64.urlsafe_b64encode(b"aes-256-gcm:pw").decode().rstrip("=")
check(parse(f"ss://{ui2}@h.com:80")["node"]["protocol"] == "shadowsocks", "ss legacy → shadowsocks")

# 5) Hysteria2
r3 = parse("hysteria2://secretpass@hy.example.com:443?sni=example.com&insecure=1#HY")
check(r3["ok"] and r3["node"]["protocol"] == "hysteria2", "hysteria2 распознан")
check(r3["node"]["params"]["password"] == "secretpass", "hy2: пароль")
check("insecure_tls" in r3["node"]["warnings"], "hy2: insecure=1 → предупреждение insecure_tls")

# 6) приватный адрес → предупреждение private_address
rp = parse("vless://uid@192.168.1.10:443?security=tls&sni=x")
check(rp["ok"] and "private_address" in rp["node"]["warnings"], "приватный IP → предупреждение")

# 7) неподдерживаемый протокол → ошибка с понятным сообщением
rv = parse("vmess://eyJ2IjoiMiJ9")
check(rv["ok"] is False and rv["error"] == "protocol_not_supported", "vmess → protocol_not_supported")

# 8) мусор → ошибка, не 500
check(parse("не ссылка")["ok"] is False, "мусор → ошибка (не падение)")

print(f"\n[bridge] {ok}/{ok} проверок зелёные")
