//go:build windows

package main

// Точечный разрыв УЖЕ УСТАНОВЛЕННЫХ соединений — ПОЛНОСТЬЮ АВТОМАТИЧЕСКИ, без
// предзаписанных списков. Принцип тот же, что у всего движка: рвём соединение к
// адресу ТОЛЬКО если движок САМ доказал блокировку и обход ещё НЕ работает.
//
// Зачем: у приложений (Discord, Telegram-десктоп) соединение устанавливается при
// запуске и держится; «обновить» его как вкладку нельзя. DPI-обход действует лишь
// на рукопожатии, а оно уже прошло. Решение — аккуратно разорвать (DELETE_TCB)
// такое соединение, чтобы приложение переподключилось и НОВОЕ рукопожатие пошло
// через обход.
//
// Две гарантии безопасности:
//  1. НЕ по списку. Рвём только то, что цикл самоподбора пометил vBlocked на
//     РЕАЛЬНОМ трафике (рукопожатие → RST/нет ответа → доказана блокировка).
//  2. НЕ рвём, если обход УЖЕ успешен (techConfirmed) или домен рабочий.

import (
	"encoding/binary"
	"net"
	"sync"
	"syscall"
	"time"
	"unsafe"
)

var (
	iphlpapi           = syscall.NewLazyDLL("iphlpapi.dll")
	procGetExtTCPTable = iphlpapi.NewProc("GetExtendedTcpTable")
	procSetTCPEntry    = iphlpapi.NewProc("SetTcpEntry")
)

const (
	afInet               = 2
	tcpTableOwnerPIDAll  = 5
	mibTCPStateEstab     = 5
	mibTCPStateDeleteTCB = 12
)

// ipToHost — ФАКТИЧЕСКАЯ карта IP→домен (SLD) из реально увиденных рукопожатий.
// Это НЕ список заблокированных — просто «какой адрес какому домену принадлежит».
// Решение рвать/не рвать берётся из вердикта домена, а не из факта наличия здесь.
var (
	ipToHost      = map[uint32]string{}
	ipToHostMu    sync.Mutex
	killedConns   = map[uint64]int64{} // 4-tuple → unixnano последнего разрыва (анти-долбёж)
	killedConnsMu sync.Mutex
	resetEnabled  = true
)

func setResetEnabled(v bool) { resetEnabled = v }

// recordHostIP — запомнить, что IP принадлежит домену (из увиденного рукопожатия).
func recordHostIP(ip uint32, sni string) {
	if ip == 0 || isPrivateIP(ip) || sni == "" {
		return
	}
	sld := reduceToSLD(sni)
	ipToHostMu.Lock()
	ipToHost[ip] = sld
	ipToHostMu.Unlock()
}

func hostForIP(ip uint32) string {
	ipToHostMu.Lock()
	defer ipToHostMu.Unlock()
	return ipToHost[ip]
}

func ourTargetPort(p uint16) bool {
	switch p {
	case 443, 2053, 2083, 2087, 2096, 8443:
		return true
	}
	return false
}

// resetBlockedConnections разрывает УСТАНОВЛЕННЫЕ соединения к адресам, которые
// движок САМ признал заблокированными и пока НЕ пробил. Одно соединение — не чаще
// раза в 30с (анти-долбёж, без петли).
// PID-резет Discord: при старте движка Discord обычно УЖЕ запущен и держит
// соединения, открытые ДО обхода. Их надо разорвать (по владельцу-PID, точно), и
// ровно стартовый набор — чтобы свежие, уже обойдённые соединения не трогать.
var (
	discordStaleCaptured bool                // первый проход зафиксировал стартовые соединения Discord
	discordStaleTuples   = map[uint64]bool{} // 4-tuple стартовых соединений процесса Discord
)

func resetBlockedConnections() int {
	if !resetEnabled || !behaviorEnabled() {
		return 0
	}
	var size uint32
	procGetExtTCPTable.Call(0, uintptr(unsafe.Pointer(&size)), 0, afInet, tcpTableOwnerPIDAll, 0)
	if size == 0 {
		return 0
	}
	buf := make([]byte, size)
	r, _, _ := procGetExtTCPTable.Call(
		uintptr(unsafe.Pointer(&buf[0])), uintptr(unsafe.Pointer(&size)),
		0, afInet, tcpTableOwnerPIDAll, 0)
	if r != 0 || len(buf) < 4 {
		return 0
	}
	n := binary.LittleEndian.Uint32(buf[0:4])
	rows := buf[4:]
	const rowSize = 24 // MIB_TCPROW_OWNER_PID
	// Санити раскладки: строки должны укладываться ровно по rowSize. Если структура
	// иной длины (другая версия Windows) — НЕ трогаем таблицу вовсе, иначе рискуем
	// оборвать ЧУЖОЕ живое соединение по неверно разобранной строке.
	if n > 0 && len(rows)/int(n) != rowSize {
		return 0
	}
	now := time.Now().UnixNano()
	killed := 0
	discPIDs := discordProcessPIDs() // PID процессов Discord — для точного резета их старых соединений
	for i := uint32(0); i < n; i++ {
		off := int(i) * rowSize
		if off+rowSize > len(rows) {
			break
		}
		row := rows[off : off+rowSize]
		if binary.LittleEndian.Uint32(row[0:4]) != mibTCPStateEstab {
			continue // только УСТАНОВЛЕННЫЕ
		}
		remoteAddr := binary.BigEndian.Uint32(row[12:16]) // network order = как ipv4DstIP
		remotePort := binary.BigEndian.Uint16(row[16:18])
		if !ourTargetPort(remotePort) {
			continue
		}
		localAddr := binary.BigEndian.Uint32(row[4:8])
		localPort := binary.BigEndian.Uint16(row[8:10])
		if remoteAddr == 0 || localAddr == 0 {
			continue // мусорная/нулевая строка — не разрываем «в никуда»
		}
		tupleHash := uint64(localAddr)<<32 ^ uint64(localPort)<<16 ^ uint64(remoteAddr) ^ uint64(remotePort)
		ownerPID := binary.LittleEndian.Uint32(row[20:24]) // MIB_TCPROW_OWNER_PID: PID в конце строки
		host := hostForIP(remoteAddr)
		// решаем, надо ли рвать это соединение:
		//  • host известен (видели рукопожатие) → по вердикту hostNeedsReset;
		//  • host НЕизвестен, но IP в blockedDirectIPs (DNS-резолв заблокированного
		//    домена ИЛИ Telegram DC) → рвём (соединение жило до старта движка);
		//  • IP принадлежит Telegram DC → рвём (Telegram-десктоп без SNI, соединение
		//    часто висит установленным с до-старта движка; после разрыва Telegram
		//    переподключится и MTProto-хендшейк пойдёт через обход).
		needReset := false
		switch {
		case host != "":
			needReset = behavior.hostNeedsReset(host)
		case isBlockedDirectIP(remoteAddr) && !isSharedCDNIP(remoteAddr):
			needReset = true
			host = ipToStr(remoteAddr)
		case mtprotoEnabled() && isTelegramDC(remoteAddr) && telegramStuck(remoteAddr):
			needReset = true
			host = "Telegram DC " + ipToStr(remoteAddr) + " (зависло)"
		}
		if !needReset {
			// DISCORD по PID — главный фикс «гейтвей не достреливает / сообщения не
			// грузятся». При старте движка Discord уже запущен и держит соединения,
			// открытые ДО обхода (на мёртвом пути): гейтвей сидит на таком, READY не
			// доходит. Находим соединения процесса Discord по PID и рвём ИМЕННО
			// стартовый набор (захваченный в ПЕРВЫЙ проход) — Discord переподключает
			// всё свежим, через обход, READY достреливается. Соединения, появившиеся
			// ПОСЛЕ захвата, уже свежие/обойдённые — их НЕ трогаем (без мигания).
			if discPIDs[ownerPID] {
				if !discordStaleCaptured {
					discordStaleTuples[tupleHash] = true
				}
				if discordStaleTuples[tupleHash] {
					needReset = true
					if host == "" {
						host = "Discord-процесс " + ipToStr(remoteAddr) + " (старое соединение до старта движка)"
					} else {
						host = host + " [Discord-процесс, переподключение]"
					}
				} else {
					continue
				}
			} else {
				continue
			}
		}
		// лимит: если к этому IP уже рвали 5 раз и не помогло — перестаём (анти-churn)
		if resetCapReached(remoteAddr) {
			continue
		}
		killedConnsMu.Lock()
		if now-killedConns[tupleHash] < int64(90*time.Second) { // одно соединение не чаще раза в 90с
			killedConnsMu.Unlock()
			continue
		}
		killedConns[tupleHash] = now
		killedConnsMu.Unlock()

		mibRow := make([]byte, 20)
		copy(mibRow, row[0:20])
		binary.LittleEndian.PutUint32(mibRow[0:4], mibTCPStateDeleteTCB)
		if ret, _, _ := procSetTCPEntry.Call(uintptr(unsafe.Pointer(&mibRow[0]))); ret == 0 {
			killed++
			bumpResetCount(remoteAddr)
			statReset(reduceToSLD(host)) // диагностика: разрыв соединения к домену
			logStepf("reset", "разорвал соединение к %s (движок доказал блок, обход ещё не пробил) → переподключится через обход", host)
		}
	}
	// первый проход зафиксировал стартовый набор соединений Discord — дальше новые
	// соединения процесса уже свежие/обойдённые, их не захватываем и не рвём.
	discordStaleCaptured = true
	return killed
}

func purgeKilledConns() {
	cutoff := time.Now().Add(-5 * time.Minute).UnixNano()
	killedConnsMu.Lock()
	for k, t := range killedConns {
		if t < cutoff {
			delete(killedConns, k)
		}
	}
	killedConnsMu.Unlock()

	// ограничение размера карт-кэшей (защита от роста памяти за долгую сессию;
	// это кэши — очистка дешёвая, данные переучиваются из нового трафика)
	ipToHostMu.Lock()
	if len(ipToHost) > 4000 {
		ipToHost = map[uint32]string{}
	}
	ipToHostMu.Unlock()

	blockedDirectIPsMu.Lock()
	if len(blockedDirectIPs) > 2000 {
		blockedDirectIPs = map[uint32]int64{}
	}
	blockedDirectIPsMu.Unlock()

	quicForceMu.Lock()
	if len(quicAttemptedIPs) > 4000 {
		quicAttemptedIPs = map[uint32]bool{}
	}
	quicForceMu.Unlock()

	resetCountByIPMu.Lock()
	if len(resetCountByIP) > 2000 {
		resetCountByIP = map[uint32]int{}
	}
	resetCountByIPMu.Unlock()
}

// ─────────────────────────────────────────────────────────────────────────────
// ПРОАКТИВНЫЙ обход УСТАНОВЛЕННЫХ соединений (без перезапуска приложений).
// Идея: когда движок САМ признал домен заблокированным, мы резолвим ВСЕ его IP
// через DNS и помечаем их. Тогда можно разорвать установленные соединения к этим
// IP, даже если их рукопожатие движок не видел (соединение жило до старта движка).
// Приложение переподключится — и новое рукопожатие пойдёт через обход.
// ─────────────────────────────────────────────────────────────────────────────

var (
	// blockedDirectIPs — IP, полученные DNS-резолвом заблокированных доменов
	// ИЛИ помеченные напрямую (Telegram DC). Это «куда точно блок».
	blockedDirectIPs   = map[uint32]int64{}
	blockedDirectIPsMu sync.Mutex
)

// markBlockedDirectIP — пометить IP как принадлежащий заблокированному сервису
// (из DNS-резолва или по факту, напр. Telegram DC). Рабочее сюда не попадает.
func markBlockedDirectIP(ip uint32) {
	if ip == 0 || isPrivateIP(ip) {
		return
	}
	blockedDirectIPsMu.Lock()
	blockedDirectIPs[ip] = time.Now().UnixNano()
	blockedDirectIPsMu.Unlock()
}

func isBlockedDirectIP(ip uint32) bool {
	blockedDirectIPsMu.Lock()
	defer blockedDirectIPsMu.Unlock()
	_, ok := blockedDirectIPs[ip]
	return ok
}

// refreshBlockedIPs — резолвит DNS всех доменов, которые движок признал
// заблокированными, и помечает их IP. Вызывается периодически в фоне. Так список
// «заблокированных IP» наполняется автоматически, без предзаписанных списков.
func refreshBlockedIPs() {
	if !behaviorEnabled() {
		return
	}
	for _, sld := range behavior.blockedDomains() {
		ips, err := net.LookupIP(sld)
		if err != nil {
			continue
		}
		for _, ip := range ips {
			if v4 := ip.To4(); v4 != nil {
				ipu := uint32(v4[0])<<24 | uint32(v4[1])<<16 | uint32(v4[2])<<8 | uint32(v4[3])
				if isSharedCDNIP(ipu) {
					continue // Cloudflare и пр. общие CDN — НЕ рвём по IP (там же gateway/CDN Discord)
				}
				markBlockedDirectIP(ipu)
			}
		}
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// ФОРС переподключения QUIC (для established QUIC, который браузер переиспользует).
// established QUIC-соединение нельзя «разорвать» как TCP. Но можно ДРОПАТЬ его
// короткие пакеты к заблокированному IP — тогда браузер сочтёт соединение мёртвым
// и откроет НОВОЕ (новый Initial мы поймаем и обойдём). Делаем это ТОЛЬКО для IP,
// к которым ещё НЕ видели свежего рукопожатия (значит соединение жило до движка).
// Самоограничение: как только поймали Initial к IP — дропать перестаём.
// ─────────────────────────────────────────────────────────────────────────────

var (
	quicAttemptedIPs = map[uint32]bool{} // IP, к которым видели свежий QUIC Initial (обход уже идёт)
	quicDropCount    = map[uint32]int{}  // сколько коротких пакетов дропнули к IP (анти-вечный-дроп)
	quicForceMu      sync.Mutex
	quicForceEnabled = true

	resetCountByIP   = map[uint32]int{} // сколько раз рвали соединения к IP (анти-вечный-churn)
	resetCountByIPMu sync.Mutex
)

// resetCapReached — исчерпан ли лимит разрывов к этому IP. Если обход за 5 попыток
// не закрепился — перестаём рвать (иначе бесконечный churn приложения).
func resetCapReached(ip uint32) bool {
	resetCountByIPMu.Lock()
	defer resetCountByIPMu.Unlock()
	return resetCountByIP[ip] >= 5
}

func bumpResetCount(ip uint32) {
	resetCountByIPMu.Lock()
	resetCountByIP[ip]++
	resetCountByIPMu.Unlock()
}

func setQUICForceEnabled(v bool) { quicForceEnabled = v }

// markQUICAttempted — поймали свежий Initial к IP → обход пошёл, дропать больше не надо.
func markQUICAttempted(ip uint32) {
	quicForceMu.Lock()
	quicAttemptedIPs[ip] = true
	quicForceMu.Unlock()
}

// shouldForceQUICReconnect — дропнуть ли этот короткий QUIC-пакет, чтобы заставить
// браузер переоткрыть соединение. ДА только если: форс включён, IP достоверно
// заблокирован, к нему ЕЩЁ не видели свежего Initial, и лимит дропов не исчерпан.
func shouldForceQUICReconnect(ip uint32) bool {
	if !quicForceEnabled || !isBlockedDirectIP(ip) {
		return false
	}
	quicForceMu.Lock()
	defer quicForceMu.Unlock()
	if quicAttemptedIPs[ip] {
		return false // уже ловим его Initial — обход идёт, не мешаем
	}
	if quicDropCount[ip] >= 60 {
		return false // лимит: браузер не переоткрывает — перестаём дропать (фолбэк)
	}
	quicDropCount[ip]++
	return true
}

// purgeQUICForce — сброс счётчиков дропа (даёт повторный шанс форса позже).
func purgeQUICForce() {
	quicForceMu.Lock()
	if len(quicDropCount) > 2000 {
		quicDropCount = map[uint32]int{}
	}
	quicForceMu.Unlock()
}

// ─────────────────────────────────────────────────────────────────────────────
// Telegram DC: рвём ТОЛЬКО зависшие соединения (без свежей MTProto-активности),
// чтобы передёрнуть Telegram-десктоп, но НЕ churnить те, что обходятся прямо сейчас.
// ─────────────────────────────────────────────────────────────────────────────
var (
	tgActivity   = map[uint32]int64{} // Telegram DC IP → unixnano последнего MTProto-хендшейка
	tgActivityMu sync.Mutex
)

// noteTelegramActivity — зафиксировать свежий MTProto-хендшейк к DC (соединение живо).
func noteTelegramActivity(ip uint32) {
	tgActivityMu.Lock()
	tgActivity[ip] = time.Now().UnixNano()
	if len(tgActivity) > 1000 {
		tgActivity = map[uint32]int64{ip: time.Now().UnixNano()}
	}
	tgActivityMu.Unlock()
}

// telegramStuck — соединение к этому DC «зависшее» (нет свежей активности >20с) →
// можно рвать, чтобы Telegram переподключился. Если активность свежая — НЕ рвём.
func telegramStuck(ip uint32) bool {
	tgActivityMu.Lock()
	defer tgActivityMu.Unlock()
	last, ok := tgActivity[ip]
	if !ok {
		return true // вообще не видели свежих хендшейков → висит мёртвым
	}
	return time.Now().UnixNano()-last > int64(20*time.Second)
}
