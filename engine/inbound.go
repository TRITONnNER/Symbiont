package main

import (
	"sync"
	"sync/atomic"
	"time"
)

// ── Обработка ВХОДЯЩИХ пакетов (ответы сервера/ТСПУ) ───────────────────────
//
// Раньше движок видел только исходящие. Теперь фильтр захватывает и входящие
// (порт источника = наш целевой), что даёт варианту C недостающие сигналы:
//   - входящий RST после нашего ClientHello = ТСПУ сбросил соединение (блок);
//   - входящие данные = соединение работает;
//   - TTL входящего RST = где стоит ТСПУ (для autottl фейка).
// Входящие пакеты НЕ изменяются — только наблюдаем и переотправляем как есть
// (петли невозможны: WinDivert не перехватывает свои инъекции).

// ── помощники по пакету ──
func ipv4SrcIP(pkt []byte) uint32 {
	if len(pkt) < 20 {
		return 0
	}
	return uint32(pkt[12])<<24 | uint32(pkt[13])<<16 | uint32(pkt[14])<<8 | uint32(pkt[15])
}

func getTTL(pkt []byte) byte {
	if len(pkt) < 9 {
		return 0
	}
	return pkt[8]
}

// tcpFlags возвращает байт флагов TCP (FIN=0x01 SYN=0x02 RST=0x04 PSH=0x08 ACK=0x10).
func tcpFlags(pkt []byte, meta ipv4tcp) byte {
	off := meta.tcpOffset + 13
	if off >= len(pkt) {
		return 0
	}
	return pkt[off]
}
func tcpHasRST(pkt []byte, meta ipv4tcp) bool { return tcpFlags(pkt, meta)&0x04 != 0 }
func tcpHasSYN(pkt []byte, meta ipv4tcp) bool { return tcpFlags(pkt, meta)&0x02 != 0 }

// ── autottl: наблюдаемый TTL ответа per-IP → подбор TTL фейка ──
type ttlInfo struct {
	observedReplyTTL byte // TTL входящего пакета (RST/данные) от этого IP
	suggestedFakeTTL byte // вычисленный TTL для fake (умереть между ТСПУ и сервером)
	at               time.Time
	loggedTTL        byte // последнее залогированное значение (анти-спам)
}

var (
	ttlMu     sync.Mutex
	ttlByIP         = map[uint32]*ttlInfo{}
	autottlOn int32 = 0
)

func setAutoTTLEnabled(v bool) {
	if v {
		atomic.StoreInt32(&autottlOn, 1)
	} else {
		atomic.StoreInt32(&autottlOn, 0)
	}
}
func autottlEnabled() bool { return atomic.LoadInt32(&autottlOn) == 1 }

// guessStartTTL — ближайший «стартовый» TTL (ОС шлют 64/128/255).
func guessStartTTL(observed byte) int {
	switch {
	case observed > 128:
		return 255
	case observed > 64:
		return 128
	default:
		return 64
	}
}

// recordReplyTTL запоминает TTL ответа от IP и вычисляет TTL для fake.
// Логика (как zapret autottl): хопы до отправителя = старт - observed.
// Фейк должен умереть ПОСЛЕ ТСПУ (он близко, ~2 хопа), но ДО сервера. Берём
// небольшое значение = max(hops_to_server - запас, минимум). Это эвристика —
// доводим по логам.
func recordReplyTTL(ip uint32, ttl byte, isRST bool) {
	if ttl == 0 {
		return
	}
	// ИГНОРИРУЕМ приватные/локальные IP: RST от роутера (192.168.x, 10.x,
	// 172.16-31.x) — это НЕ ТСПУ, на его TTL ориентироваться нельзя (был баг:
	// фейк с TTL=2 умирал в локальной сети).
	if isPrivateIP(ip) {
		return
	}
	ttlMu.Lock()
	defer ttlMu.Unlock()
	ti := ttlByIP[ip]
	if ti == nil {
		ti = &ttlInfo{}
		ttlByIP[ip] = ti
	}
	ti.observedReplyTTL = ttl
	start := guessStartTTL(ttl)
	hops := start - int(ttl)
	if hops < 1 {
		hops = 1
	}
	// фейк: дойти примерно до середины пути (точно за ТСПУ, не до сервера)
	ft := hops/2 + 1
	if ft < 2 {
		ft = 2
	}
	if ft > 12 {
		ft = 12
	}
	ti.suggestedFakeTTL = byte(ft)
	prevLogged := ti.loggedTTL
	ti.loggedTTL = byte(ft)
	ti.at = time.Now()
	// логируем ТОЛЬКО при первом появлении IP или смене значения (без спама)
	if isRST && prevLogged != byte(ft) {
		logStepf("autottl", "RST от %s: TTL=%d (старт≈%d, хопов≈%d) → TTL фейка≈%d [ТСПУ виден по TTL ответного RST]",
			ipToStr(ip), ttl, start, hops, ft)
	}
}

// suggestedFakeTTL — рекомендованный TTL фейка для IP (0 если неизвестно).
func suggestedFakeTTL(ip uint32) byte {
	ttlMu.Lock()
	defer ttlMu.Unlock()
	if ti := ttlByIP[ip]; ti != nil {
		return ti.suggestedFakeTTL
	}
	return 0
}

// ── главная обработка входящего пакета (только наблюдение) ──
func handleInbound(pkt []byte) {
	if len(pkt) < 20 || pkt[9] != 6 { // только TCP
		return
	}
	meta := parseIPv4TCP(pkt)
	if !meta.ok {
		return
	}
	srcIP := ipv4SrcIP(pkt) // сервер/ТСПУ
	// RST/данные от приватных IP (роутер, локалка) — НЕ сигнал ТСПУ, игнорируем.
	if isPrivateIP(srcIP) {
		return
	}
	key := connKey(srcIP, meta.srcPort)
	ttl := getTTL(pkt)

	if tcpHasRST(pkt, meta) {
		// RST после нашего ClientHello = классический признак SNI-блокировки ТСПУ
		recordReplyTTL(srcIP, ttl, true)
		if behaviorEnabled() {
			behavior.onInboundRST(key)
		}
		atomic.AddInt64(&cntInRST, 1)
		return
	}
	// SYN-ACK или данные = соединение живёт
	if tcpHasSYN(pkt, meta) {
		recordReplyTTL(srcIP, ttl, false) // TTL «честного» ответа для калибровки
	}
	if meta.payloadLen > 0 {
		if behaviorEnabled() {
			behavior.onInboundData(key)
		}
		atomic.AddInt64(&cntInData, 1)
	}
}

// счётчики входящих (для сводки в логе)
var (
	cntInRST  int64
	cntInData int64
)

func ipToStr(ip uint32) string {
	return itoa(int(ip>>24&0xff)) + "." + itoa(int(ip>>16&0xff)) + "." + itoa(int(ip>>8&0xff)) + "." + itoa(int(ip&0xff))
}

// kyberLogOnce ограничивает частоту лога про kyber (раз в ~500 событий).
var kyberCnt int64

func kyberLogOnce() bool {
	n := atomic.AddInt64(&kyberCnt, 1)
	return n == 1 || n%500 == 0
}

var statefulCnt int64

func statefulLogOnce() bool {
	n := atomic.AddInt64(&statefulCnt, 1)
	return n == 1 || n%200 == 0
}

var silentCnt int64

func silentLogOnce() bool {
	n := atomic.AddInt64(&silentCnt, 1)
	return n == 1 || n%200 == 0
}

func atomicAddInRST()  { atomic.AddInt64(&cntInRST, 1) }
func atomicAddInData() { atomic.AddInt64(&cntInData, 1) }

// isPrivateIP — приватный/локальный IPv4 (RFC1918 + loopback + link-local + CGNAT).
func isPrivateIP(ip uint32) bool {
	a := byte(ip >> 24)
	b := byte(ip >> 16)
	switch {
	case a == 10: // 10.0.0.0/8
		return true
	case a == 127: // 127.0.0.0/8 loopback
		return true
	case a == 169 && b == 254: // 169.254.0.0/16 link-local
		return true
	case a == 172 && b >= 16 && b <= 31: // 172.16.0.0/12
		return true
	case a == 192 && b == 168: // 192.168.0.0/16
		return true
	case a == 100 && b >= 64 && b <= 127: // 100.64.0.0/10 CGNAT
		return true
	}
	return false
}
