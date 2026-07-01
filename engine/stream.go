//go:build windows

package main

// Метод 1+4: десинхронизация ПОТОКА данных видео (не только ClientHello).
// ИСПРАВЛЕНО: помечаем не весь IP, а конкретное СОЕДИНЕНИЕ (IP+порт), и дробим
// ТОЛЬКО TLS application-data сегменты (record type 0x17) — это и есть видео-чанки.
// Раньше дробили весь трафик к общему Google-IP → ломали посторонние потоки (offline).

import (
	"sync"
	"sync/atomic"
)

// videoConns: ключ = "dstIP:dstPort" соединения, опознанного как видео.
var (
	videoConns   = map[uint64]bool{}
	videoConnsMu sync.RWMutex
)

// connKey упаковывает dstIP(32) + dstPort(16) в один ключ.
func connKey(ip uint32, port int) uint64 {
	return uint64(ip)<<16 | uint64(uint16(port))
}

// markVideoConn помечает конкретное соединение (IP+порт) как видео.
func markVideoConn(ip uint32, port int) {
	videoConnsMu.Lock()
	videoConns[connKey(ip, port)] = true
	videoConnsMu.Unlock()
}

func isVideoConn(ip uint32, port int) bool {
	videoConnsMu.RLock()
	v := videoConns[connKey(ip, port)]
	videoConnsMu.RUnlock()
	return v
}

// streamSplitParts/streamOn/ipfragOn — потокобезопасные (меняет prober, читает loop)
var (
	streamOn         int32 = 0
	ipfragOn         int32 = 0
	streamSplitParts int32 = 3
	tlsrecOn         int32 = 0
)

func setTLSRecEnabled(v bool) {
	if v {
		atomic.StoreInt32(&tlsrecOn, 1)
	} else {
		atomic.StoreInt32(&tlsrecOn, 0)
	}
}
func tlsrecEnabled() bool { return atomic.LoadInt32(&tlsrecOn) == 1 }

func setStreamEnabled(v bool) {
	if v {
		atomic.StoreInt32(&streamOn, 1)
	} else {
		atomic.StoreInt32(&streamOn, 0)
	}
}
func streamEnabled() bool { return atomic.LoadInt32(&streamOn) == 1 }
func setIPFragEnabled(v bool) {
	if v {
		atomic.StoreInt32(&ipfragOn, 1)
	} else {
		atomic.StoreInt32(&ipfragOn, 0)
	}
}
func ipfragEnabled() bool  { return atomic.LoadInt32(&ipfragOn) == 1 }
func setStreamParts(n int) { atomic.StoreInt32(&streamSplitParts, int32(n)) }
func getStreamParts() int  { return int(atomic.LoadInt32(&streamSplitParts)) }

// isTLSAppData: payload начинается с TLS application_data (0x17) — это видео-чанк,
// а не handshake/служебное. Дробим только их.
func isTLSAppData(payload []byte) bool {
	return len(payload) >= 3 && payload[0] == 0x17
}

// applyStreamDesync дробит крупный TLS-app-data сегмент видео-СОЕДИНЕНИЯ.
// Возвращает true, если обработали (сами отправили).
func applyStreamDesync(wd *winDivert, pkt []byte, addr *winDivertAddress) bool {
	meta := parseIPv4TCP(pkt)
	if !meta.ok || meta.payloadLen < 400 {
		return false // мелкие — не трогаем (ACK, служебное)
	}
	dstIP := ipv4DstIP(pkt)
	if !isVideoConn(dstIP, meta.dstPort) {
		return false // не видео-соединение — НЕ трогаем (исправляет offline-баг)
	}
	payload := pkt[meta.dataOffset:]
	if !isTLSAppData(payload) {
		return false // не видео-данные (handshake и пр.) — пропускаем как есть
	}
	total := len(payload)
	parts := getStreamParts()
	if parts < 2 {
		parts = 2
	}
	chunk := total / parts
	if chunk < 1 {
		return false
	}
	logStepf("stream", "дроблю видео-чанк %d байт на %d частей (соед.%s:%d)", total, parts, ipString(dstIP), meta.dstPort)

	// Метод 3 для ПОТОКА (не пробовали!): если включён ipfrag — режем видео-чанк
	// на IP-фрагменты, а не TCP-сегменты. DPI иначе обрабатывает фрагменты.
	if ipfragEnabled() {
		f1, f2 := ipFragmentTCP(pkt, meta, total/2)
		if f1 != nil && f2 != nil {
			wd.sendRaw(f1, addr)
			wd.sendRaw(f2, addr)
			logStep("stream", "видео-чанк разбит на IP-фрагменты (метод 3)")
			return true
		}
	}

	seq := meta.seq
	off := 0
	idx := uint16(0x5000)
	for off < total {
		end := off + chunk
		if end > total {
			end = total
		}
		seg := buildSegment(pkt, meta, payload[off:end], seq, idx)
		wd.send(seg, addr)
		seq += uint32(end - off)
		off = end
		idx++
	}
	return true
}

// ipv4DstIP читает адрес назначения из IPv4-заголовка (offset 16..20).
func ipv4DstIP(pkt []byte) uint32 {
	if len(pkt) < 20 {
		return 0
	}
	return uint32(pkt[16])<<24 | uint32(pkt[17])<<16 | uint32(pkt[18])<<8 | uint32(pkt[19])
}

func ipString(ip uint32) string {
	return itoa(int(ip>>24&0xff)) + "." + itoa(int(ip>>16&0xff)) + "." +
		itoa(int(ip>>8&0xff)) + "." + itoa(int(ip&0xff))
}
