package main

import (
	"sync"
	"sync/atomic"
	"time"
)

// ── Десинк MTProto (Telegram-десктоп) ──────────────────────────────────────
//
// У MTProto нет SNI, который можно разрезать. Зато ТСПУ узнаёт MTProto по
// СИГНАТУРЕ первого пакета (обфусцированный 64-байтный handshake) и режет поток.
// Лечим так: ловим соединения к IP дата-центров Telegram (они публичны) и рвём
// ПЕРВЫЙ пакет соединения на части + впрыскиваем fake — ТСПУ не может сматчить
// сигнатуру. Сервер собирает поток по TCP, fake умирает по TTL. Реальные данные
// не трогаем → безопасно. Помогает, если Telegram режут по DPI; если по IP
// дата-центров — локально не лечится (нужен туннель), увидим по логу.

// диапазоны IP дата-центров Telegram (CIDR; публичные, AS62041/62014/59930/44907)
var telegramCIDRs = []struct {
	net  uint32
	bits int
}{
	{ipToU32(91, 108, 4, 0), 22},
	{ipToU32(91, 108, 8, 0), 22},
	{ipToU32(91, 108, 12, 0), 22},
	{ipToU32(91, 108, 16, 0), 22},
	{ipToU32(91, 108, 20, 0), 22},
	{ipToU32(91, 108, 56, 0), 22},
	{ipToU32(91, 105, 232, 0), 22},
	{ipToU32(95, 161, 64, 0), 20},
	{ipToU32(149, 154, 160, 0), 20},
	{ipToU32(185, 76, 151, 0), 24},
}

func ipToU32(a, b, c, d byte) uint32 {
	return uint32(a)<<24 | uint32(b)<<16 | uint32(c)<<8 | uint32(d)
}

// ── Вердикт по Telegram: IP-блок дата-центров vs DPI-сигнатура ───────────────
// Если к DC идут ТОЛЬКО SYN (payloadLen=0) и НИ ОДНОГО data-пакета — рукопожатие
// не завершается (нет SYN-ACK), значит блок по IP. Десинк (он по data) бессилен.
var (
	tgSYNonly     int64
	tgDataEver    int32
	tgVerdictDone int32
)

// noteTelegramDataSeen — отметить, что к Telegram реально ушёл data-пакет
// (рукопожатие завершилось). Снимает подозрение на чистый IP-блок.
func noteTelegramDataSeen() { atomic.StoreInt32(&tgDataEver, 1) }

// noteTelegramSYNOnly — считает SYN-only пакеты и ОДИН раз возвращает true, когда
// их накопилось достаточно без единого data-пакета (вердикт IP-блока DC).
func noteTelegramSYNOnly() bool {
	n := atomic.AddInt64(&tgSYNonly, 1)
	if n >= 10 && atomic.LoadInt32(&tgDataEver) == 0 {
		return atomic.CompareAndSwapInt32(&tgVerdictDone, 0, 1)
	}
	return false
}

// isTelegramDC — принадлежит ли IP дата-центрам Telegram.
func isTelegramDC(ip uint32) bool {
	for _, c := range telegramCIDRs {
		mask := uint32(0xFFFFFFFF) << (32 - c.bits)
		if ip&mask == c.net {
			return true
		}
	}
	return false
}

// трекер «первый пакет соединения уже обработан» (чтобы рвать только handshake).
var (
	mtMu    sync.Mutex
	mtSeen  = map[uint64]time.Time{}
	mtPrune time.Time
)

// mtFirstPacket — true, если это первый раз, когда видим данные этого соединения.
func mtFirstPacket(key uint64) bool {
	mtMu.Lock()
	defer mtMu.Unlock()
	now := time.Now()
	// периодическая чистка старых записей (раз в минуту)
	if now.Sub(mtPrune) > time.Minute {
		for k, t := range mtSeen {
			if now.Sub(t) > 2*time.Minute {
				delete(mtSeen, k)
			}
		}
		mtPrune = now
	}
	if _, ok := mtSeen[key]; ok {
		return false
	}
	mtSeen[key] = now
	return true
}

// setMTProtoEnabled / mtprotoEnabled — переключатель.
var mtprotoOn int32 = 1

func setMTProtoEnabled(v bool) {
	if v {
		atomic.StoreInt32(&mtprotoOn, 1)
	} else {
		atomic.StoreInt32(&mtprotoOn, 0)
	}
}
func mtprotoEnabled() bool { return atomic.LoadInt32(&mtprotoOn) == 1 }

var mtLogCnt int64

// applyMTProtoDesync рвёт сигнатуру MTProto на соединениях к DC Telegram.
// Возвращает true, если обработал (тогда главный цикл НЕ шлёт оригинал — мы сами).
func applyMTProtoDesync(wd *winDivert, pkt []byte, meta ipv4tcp, addr *winDivertAddress) bool {
	if !mtprotoEnabled() {
		return false
	}
	dst := ipv4DstIP(pkt)
	if !isTelegramDC(dst) {
		return false
	}
	// ДИАГНОСТИКА: видим пакет к Telegram DC. Логируем (первые N раз) dst, размер
	// payload и порт — чтобы по логу понять, доходит ли трафик Telegram до обработки
	// и почему обход не применяется (мелкий пакет / не первый / др. порт).
	if tgDiagLogOnce() {
		logStepf("mtproto-diag", "пакет к Telegram DC %s порт=%d payloadLen=%d (вижу трафик Telegram)", ipToStr(dst), meta.dstPort, meta.payloadLen)
	}
	// Фиксируем СВЕЖУЮ MTProto-активность к этому DC: значит соединение живо/
	// переподключается и обходится прямо сейчас — его рвать НЕ надо. Разрыв
	// добивает только ЗАВИСШИЕ Telegram-соединения (без свежей активности),
	// чтобы Telegram-десктоп переподключился. Так нет петли «обошёл→разорвал».
	noteTelegramActivity(dst)
	noteTelegramDataSeen() // к DC реально ушли данные → не чистый IP-блок
	// только первый пакет соединения (handshake-сигнатура), и только с данными
	if meta.payloadLen < 8 {
		return false
	}
	key := connKey(dst, meta.dstPort)
	if !mtFirstPacket(key) {
		return false // не первый пакет — пропускаем как есть (вернём false → отправит цикл)
	}
	payload := pkt[meta.dataOffset:]
	cut := meta.payloadLen / 2
	if cut < 1 {
		cut = 1
	}
	// fake перед разрезом: мусор с низким TTL (ТСПУ «увидит» не то, сервер отбросит)
	ft := byte(4)
	if autottlEnabled() {
		if t := suggestedFakeTTL(dst); t > 0 {
			ft = t
		}
	}
	fakePayload := []byte{0x16, 0x03, 0x01, 0x00, 0x00} // похоже на начало TLS — сбивает классификатор
	if fk := buildSegment(pkt, meta, fakePayload, meta.seq, 0x3a01); fk != nil {
		setTTL(fk, ft)
		wd.sendRaw(fk, addr)
	}
	// рвём первый пакет на две части (сигнатура MTProto не матчится)
	p1 := buildSegment(pkt, meta, payload[:cut], meta.seq, 0x3a02)
	p2 := buildSegment(pkt, meta, payload[cut:], meta.seq+uint32(cut), 0x3a03)
	if p1 == nil || p2 == nil {
		return false // фолбэк: пусть цикл отправит оригинал
	}
	wd.send(p2, addr) // disorder-порядок: вторая часть первой (ломает реассемблер DPI)
	wd.send(p1, addr)
	if mtLogOnce() {
		logStepf("mtproto", "Telegram DC %s: разорвал сигнатуру MTProto (split+fake, первый пакет)", ipToStr(dst))
	}
	return true
}

func mtLogOnce() bool {
	n := atomic.AddInt64(&mtLogCnt, 1)
	return n == 1 || n%50 == 0
}

// telegramFilterClause — генерирует кусок WinDivert-фильтра для перехвата ВСЕГО
// трафика к дата-центрам Telegram (на ЛЮБОМ порту, не только 443). Нужно потому,
// что Telegram-десктоп часто ходит по портам 80/5222/нестандартным — фильтр по
// портам его не ловит, и MTProto-обход не применялся. Возвращает строку вида
// "(ip.DstAddr>=A and ip.DstAddr<=B) or (...)" или "" если MTProto выключен.
func telegramFilterClause() string {
	out := ""
	for _, c := range telegramCIDRs {
		start := c.net
		end := c.net | (uint32(0xFFFFFFFF) >> c.bits)
		clause := "(ip.DstAddr>=" + ipToStr(start) + " and ip.DstAddr<=" + ipToStr(end) + ")"
		if out != "" {
			out += " or "
		}
		out += clause
	}
	return out
}

var tgDiagCnt int64

func tgDiagLogOnce() bool {
	n := atomic.AddInt64(&tgDiagCnt, 1)
	return n <= 15 || n%100 == 0 // первые 15 пакетов + каждый 100-й
}
