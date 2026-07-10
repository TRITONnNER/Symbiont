# Симбионт — нативная интеграция Sing-box (пошагово)

Этот гайд показывает, **как подключить готовый open-source Sing-box** к нашему
Flutter-клиенту через границу `SymbiontEngine` (см. `lib/engine/singbox_engine.dart`).

> **Жёсткая граница.** Туннелирование, протоколы и пробой делает библиотека
> sing-box (libbox). Мы пишем ТОЛЬКО «обвязку»: VPN-сервис ОС + канал команд/статуса
> + передачу конфига. Никакого собственного кода обхода здесь нет и не будет.

Канал: `MethodChannel('symbiont/engine')` (команды) + `EventChannel('symbiont/engine/status')` (статус).
Конфиг sing-box собирается из подписанного манифеста (узел + протокол), полученного с бэкенда.

---

## 0. Откуда берётся sing-box

- Готовые сборки и исходники: проект **sing-box** (SagerNet), мобильная обвязка — **libbox**
  (gomobile-биндинг). Для Android — `.aar`, для iOS/macOS — `.xcframework`.
- Сборка libbox (один раз, в CI): `gomobile bind` по официальной инструкции sing-box.
  Здесь не приводится — это стандартный шаг публичного проекта.
- Никаких форков с «самодельным обходом» — берём как зависимость.

---

## 1. Android — VpnService + канал

### 1.1. Манифест и разрешения
`android/app/src/main/AndroidManifest.xml`:
```xml
<uses-permission android:name="android.permission.INTERNET"/>
<uses-permission android:name="android.permission.FOREGROUND_SERVICE"/>
<uses-permission android:name="android.permission.FOREGROUND_SERVICE_SPECIAL_USE"/>

<application ...>
  <service
      android:name=".SymbiontVpnService"
      android:permission="android.permission.BIND_VPN_SERVICE"
      android:foregroundServiceType="specialUse"
      android:exported="false">
    <intent-filter><action android:name="android.net.VpnService"/></intent-filter>
  </service>
</application>
```
Положить `libbox.aar` в `android/app/libs/` и подключить в `android/app/build.gradle`:
```gradle
dependencies { implementation files('libs/libbox.aar') }
```

### 1.2. VpnService — отдаёт fd библиотеке
`SymbiontVpnService.kt` (СКЕЛЕТ; вся сетевая работа — внутри libbox):
```kotlin
class SymbiontVpnService : VpnService() {
    private var boxService: libbox.BoxService? = null   // из libbox

    fun startTunnel(singboxConfigJson: String) {
        // 1) поднимаем TUN-интерфейс средствами ОС
        val builder = Builder()
            .setSession("Симбионт")
            .setMtu(1500)
            .addAddress("172.19.0.1", 30)
            .addDnsServer("1.1.1.1")
            .addRoute("0.0.0.0", 0)
        // (split-tunneling по приложениям — addAllowedApplication/addDisallowedApplication)
        val tunFd: ParcelFileDescriptor = builder.establish() ?: return

        // 2) ОТДАЁМ fd готовому sing-box; он сам делает туннель/протоколы/пробой
        boxService = libbox.Libbox.newService(singboxConfigJson, PlatformTunImpl(tunFd))
        boxService?.start()
        emitStatus("on")
    }

    fun stopTunnel() { boxService?.close(); boxService = null; emitStatus("off") }
}
```
> `PlatformTunImpl` — тонкий адаптер из примеров libbox, передающий fd. Логику
> туннеля мы не реализуем — она в `Libbox.newService(...)`.

### 1.3. MethodChannel-обработчик
`MainActivity.kt`:
```kotlin
class MainActivity : FlutterActivity() {
    override fun configureFlutterEngine(engine: FlutterEngine) {
        super.configureFlutterEngine(engine)
        val cmd = MethodChannel(engine.dartExecutor.binaryMessenger, "symbiont/engine")
        cmd.setMethodCallHandler { call, result ->
            when (call.method) {
                "connect" -> {
                    // (1) если нужно разрешение VPN — VpnService.prepare(this)
                    val cfg = SingboxConfig.fromManifest(   // наш сборщик конфига
                        nodeId = call.argument("nodeId"),
                        mode   = call.argument("mode"))
                    startService(Intent(this, SymbiontVpnService::class.java))
                    // прокинуть cfg в сервис (binder/Intent extra) и вызвать startTunnel(cfg)
                    result.success(null)
                }
                "disconnect" -> { /* stopTunnel() */ result.success(null) }
                "applyRules" -> { /* перезапуск с новым route в конфиге */ result.success(null) }
                "listNodes", "runAnalysis", "diagnose", "setProtection", "setCoverage"
                    -> result.success(/* данные из манифеста / локальных проб */ null)
                else -> result.notImplemented()
            }
        }
        // статус → Dart
        EventChannel(engine.dartExecutor.binaryMessenger, "symbiont/engine/status")
            .setStreamHandler(StatusStreamHandler)  // шлёт {phase,pingMs,...}
    }
}
```

### 1.4. Сборка конфига из манифеста
`SingboxConfig.fromManifest(...)` формирует штатный JSON sing-box: `outbounds`
(выбранный узел/протокол из манифеста) + `route.rules` (наши `RoutingRule` →
domain_suffix/domain/process). Это **конфиг для готового движка**, не наш протокол.

---

## 2. iOS / macOS — NetworkExtension (Packet Tunnel)

### 2.1. Таргет-расширение и entitlements
- Добавить таргет **Network Extension → Packet Tunnel Provider** (напр. `SymbiontTunnel`).
- Entitlements у приложения и расширения:
  `com.apple.developer.networking.networkextension = [packet-tunnel-provider]`.
- Общий **App Group** (напр. `group.app.symbiont`) — для передачи конфига в расширение.
- Подключить `Libbox.xcframework` к таргету расширения.

> Память расширения Packet Tunnel ограничена (~50 MiB) — sing-box в режиме
> расширения это учитывает; не держим тяжёлых аллокаций на нашей стороне.

### 2.2. PacketTunnelProvider — запускает sing-box
`PacketTunnelProvider.swift` (СКЕЛЕТ):
```swift
import NetworkExtension
import Libbox   // готовый xcframework

class PacketTunnelProvider: NEPacketTunnelProvider {
    private var box: LibboxBoxService?

    override func startTunnel(options: [String : NSObject]?,
                              completionHandler: @escaping (Error?) -> Void) {
        // 1) сетевые настройки TUN
        let settings = NEPacketTunnelNetworkSettings(tunnelRemoteAddress: "127.0.0.1")
        settings.ipv4Settings = NEIPv4Settings(addresses: ["172.19.0.1"], subnetMasks: ["255.255.255.252"])
        settings.ipv4Settings?.includedRoutes = [NEIPv4Route.default()]
        settings.dnsSettings = NEDNSSettings(servers: ["1.1.1.1"])
        setTunnelNetworkSettings(settings) { _ in
            // 2) читаем конфиг из App Group и СТАРТУЕМ готовый sing-box
            let cfg = SharedConfig.read()           // JSON sing-box из манифеста
            self.box = LibboxNewService(cfg, PlatformTun(self.packetFlow))
            try? self.box?.start()
            completionHandler(nil)
        }
    }

    override func stopTunnel(with reason: NEProviderStopReason,
                             completionHandler: @escaping () -> Void) {
        box?.close(); box = nil; completionHandler()
    }
}
```
> `PlatformTun(packetFlow)` — адаптер из примеров libbox. Туннель/протоколы — в libbox.

### 2.3. Управление из приложения (MethodChannel)
В iOS-части Flutter (`AppDelegate.swift`) `connect/disconnect` поднимают/гасят
профиль через `NETunnelProviderManager` (`saveToPreferences` → `startVPNTunnel`),
а конфиг кладут в App Group перед стартом:
```swift
let mgr = NETunnelProviderManager()
let proto = NETunnelProviderProtocol()
proto.providerBundleIdentifier = "app.symbiont.SymbiontTunnel"
proto.serverAddress = "Симбионт"
mgr.protocolConfiguration = proto
mgr.isEnabled = true
mgr.saveToPreferences { _ in try? mgr.connection.startVPNTunnel() }
```

> **Важно про iOS (из томов II–III):** per-app split tunneling обычному приложению
> недоступен (только MDM) — на iPhone банки уводим напрямую по доменам/IP, а не по
> приложению. Встроенный анти-фингерпринт-браузер на iOS ограничен WebKit.

---

## 3. Поток данных (как всё соединяется)

```
Бэкенд: GET /v1/manifest (подписан Ed25519)
        │  узлы + правила
        ▼
Клиент(Dart): проверка подписи → SingboxEngine.connect(nodeId, mode)
        │  MethodChannel "connect"
        ▼
Натив: SingboxConfig.fromManifest → JSON sing-box
        │  TUN fd (VpnService / NEPacketTunnelProvider)
        ▼
libbox (готовый sing-box): держит туннель, протоколы, пробой
        │  статус/метрики
        ▼
EventChannel → Dart → ConnStatus → экран «Защита»
```

---

## 4. Порядок реализации (минимум до рабочего туннеля)

1. Android: VpnService + MethodChannel + один узел Reality из манифеста → connect/disconnect/статус.
2. iOS: Packet Tunnel Provider + App Group + тот же конфиг.
3. Kill-switch: Android — `Builder` без fallback + блок при разрыве; iOS — `includeAllNetworks`/правила.
4. Split-tunneling: Android/desktop — per-app (`addAllowed/Disallowed`); iOS — по адресам из правил.
5. Каскад протоколов и «умный охват» — добавляются как разные `outbounds`/`route.rules` в конфиге.

Везде, где написано «libbox делает X», — это **готовый Sing-box**. Наш код — только
обвязка ОС, канал и сборка конфига из подписанного манифеста.
