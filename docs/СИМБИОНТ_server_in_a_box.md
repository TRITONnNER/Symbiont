# Симбионт — Server-in-a-Box: узел за одну команду

Превращает чистый VPS в рабочий узел Симбионта **одной строкой**, само-регистрирует его
в списке серверов и даёт CLI управления. Это и есть «купил сервер — и всё».

---

## Одна команда (на чистом Ubuntu 22.04/24.04, от root)

```bash
curl -fsSL https://raw.githubusercontent.com/TRITONnNER/Symbiont/main/symbiont_build/server/get.sh \
  | sudo bash -s -- --register-to https://ВАШ_БЭКЕНД --node-secret СЕКРЕТ_ОПЕРАТОРА
```

- `--register-to` — адрес вашего задеплоенного бэкенда.
- `--node-secret` — операторский секрет (`SYMBIONT_NODE_SECRET` на бэкенде) — им узел подписывает саморегистрацию.
- Необязательно: `--code NL --country Netherlands --sni www.microsoft.com` (иначе авто-подбор по IP).
- До релиза в `main` можно указать ветку/источник: `SYMBIONT_REF=<branch>` или `SYMBIONT_SRC=<raw-base>`.

### Что происходит само
1. Ставит зависимости, скачивает набор узла в `/opt/symbiont`.
2. `bootstrap_vps.sh`: **авто-подбор** страны/SNI (`node_autotune.py`), установка **sing-box**
   (VLESS-Reality `443/tcp` + Hysteria2 `8443/udp` + Shadowsocks-2022 `9443/tcp`), systemd, firewall.
3. **Само-регистрация** в подписанном манифесте бэкенда (`/v1/node/register`, HMAC) →
   бэкенд пересобирает и переподписывает манифест → **узел появляется у клиентов сам**.
4. **Heartbeat** (каждые 60с): замолчал дольше `NODE_STALE_SEC` → само-починка выкидывает из
   списка; вернулся → снова появляется. Живая загрузка тоже идёт в манифест.
5. Ставит CLI **`symbiont-node`** в `PATH`.

---

## Управление узлом на сервере — `symbiont-node`

```
symbiont-node status         состояние узла и heartbeat + инфо (id, страна, протоколы)
symbiont-node logs [N] [-f]   логи sing-box (N строк; -f — следить)
symbiont-node restart         перезапустить узел
symbiont-node update          обновить до свежей версии (та же команда установки)
symbiont-node remove          снять узел (уйдёт из списка серверов по self-heal)
symbiont-node info            полный манифест узла
```

Параметры оператора хранятся в `/etc/symbiont/sib.env` (для `update`/`remove`).

---

## Управление флотом централизованно (оператор / панель)

Новые админ-эндпоинты (под `X-Admin-Token`) — для панели владельца / Server-in-a-Box:

```
GET  /v1/admin/nodes              список узлов: id, страна, host, протоколы, роли,
                                  загрузка, last_seen, alive (жив ли по heartbeat)
POST /v1/admin/node/{id}/remove   снять узел из манифеста (секрет отзывается, манифест пересобран)
```

(Покрыто: регистрация → список(alive) → удаление → требование админ-токена — проверено.)

---

## Что нужно ДО этого (разово) — тоже одной командой

**Задеплоить бэкенд** (на отдельном VPS с доменом):

```bash
curl -fsSL https://raw.githubusercontent.com/TRITONnNER/Symbiont/main/symbiont_build/server/deploy-backend.sh \
  | sudo bash -s -- --domain api.example.com --email you@example.com
```

Ставит зависимости, код, venv, **генерит секреты** (в `/var/lib/symbiont`), поднимает
systemd-сервис (uvicorn, один воркер — состояние в памяти+SQLite), **nginx + TLS (certbot)**,
и печатает **base URL + ADMIN_TOKEN + NODE_SECRET**. Эти `NODE_SECRET`/`ADMIN_TOKEN` и подставляете
в `get.sh` (узлы) и в управление флотом. Повтор команды = обновление (идемпотентно).

> Перед приёмом реальных платежей — подключить боевой платёжный провайдер (роадмап).

Полный путь: **купил VPS → `deploy-backend.sh` (один раз) → на каждый узел `get.sh` (одна команда)**.

---

## Где это в «купить сервер — и всё»

| Шаг | Статус |
|---|---|
| Установка узла одной командой | ✅ `get.sh` |
| Авто-настройка (протоколы/страна/SNI/systemd/firewall) | ✅ `bootstrap_vps.sh` |
| Само-регистрация в списке серверов | ✅ манифест + heartbeat |
| Управление узлом на сервере | ✅ `symbiont-node` CLI |
| Управление флотом централизованно | ✅ `/v1/admin/nodes` (+remove) |
| Задеплоить бэкенд + домен + секреты | 🟡 разовая операторская настройка |
| Купить VPS | 🟡 за вами |
