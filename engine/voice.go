//go:build windows

package main

// Обход ГОЛОСА Discord. Голос идёт по UDP (порты 50000-65535) на отдельную
// инфраструктуру (discord.media / голосовые серверы), а текст/подключение — по
// TCP/443 (это уже обходим). ТСПУ душит именно голосовой UDP, поэтому входящий
// звук молчит. Рецепт (как zapret): fake-десинк UDP-пакетов к голосовым серверам.

import (
	"sync"
	"sync/atomic"
)

// voiceIPs — IP голосовых серверов Discord (узнаём из ClientHello discord.media
// и из gateway.discord.gg). На UDP к этим IP применяем десинк.
var (
	voiceIPs   = map[uint32]bool{}
	voiceIPsMu sync.RWMutex
)

func markVoiceIP(ip uint32) {
	voiceIPsMu.Lock()
	voiceIPs[ip] = true
	voiceIPsMu.Unlock()
}
func isVoiceIP(ip uint32) bool {
	voiceIPsMu.RLock()
	v := voiceIPs[ip]
	voiceIPsMu.RUnlock()
	return v
}

// глобальный переключатель голосового обхода и параметры
var (
	voiceOn      int32 = 0
	voiceTTL     byte  = 3
	voiceRepeats       = 6
)

func setVoiceEnabled(v bool) {
	if v {
		atomic.StoreInt32(&voiceOn, 1)
	} else {
		atomic.StoreInt32(&voiceOn, 0)
	}
}
func voiceEnabled() bool { return atomic.LoadInt32(&voiceOn) == 1 }

// isDiscordVoiceSNI — домен голосовой инфраструктуры Discord?
func isDiscordVoiceSNI(sni string) bool {
	for _, suf := range []string{"discord.media", "discord.gg", "discordapp.net"} {
		if sni == suf || hasSuffix(sni, "."+suf) {
			return true
		}
	}
	return false
}

func hasSuffix(s, suf string) bool {
	return len(s) >= len(suf) && s[len(s)-len(suf):] == suf
}

// applyVoiceDesync обрабатывает исходящий UDP-пакет к голосовому серверу Discord:
// шлёт поддельные UDP-пакеты (низкий TTL + repeats) перед настоящим, чтобы сбить
// DPI, который душит голосовой поток. Возвращает true, если обработали.
func applyVoiceDesync(wd *winDivert, pkt []byte, addr *winDivertAddress) bool {
	meta := parseIPv4UDP(pkt)
	if !meta.ok {
		return false
	}
	dstIP := ipv4DstIP(pkt)
	// УЗКИЕ диапазоны голоса Discord (как zapret: 19294-19344 и 50000-50100),
	// а не весь 50000-65535 — чтобы не задевать игры и прочий UDP.
	isVoicePort := (meta.dstPort >= 19294 && meta.dstPort <= 19344) ||
		(meta.dstPort >= 50000 && meta.dstPort <= 50100)
	if !isVoiceIP(dstIP) && !isVoicePort {
		return false
	}

	// fake = РЕАЛЬНЫЙ захваченный bin (Discord IP Discovery / STUN), отправленный
	// как валидный UDP-пакет к тому же адресу с низким TTL и repeats. ТСПУ примет
	// его как настоящий голосовой хендшейк (структурно валиден) и собьётся, а до
	// сервера фейк не дойдёт (TTL умрёт). Это рецепт zapret --dpi-desync-fake-stun.
	binFake := fakeVoiceDiscord
	if len(binFake) == 0 {
		binFake = fakeSTUN
	}
	if len(binFake) > 0 {
		for r := 0; r < voiceRepeats; r++ {
			if f := buildVoiceFake(pkt, meta, binFake, voiceTTL); f != nil {
				wd.send(f, addr) // send → пересчёт сумм (фейк валиден, но умрёт по TTL)
			}
		}
	}
	// настоящий голосовой пакет — как есть
	if err := wd.send(pkt, addr); err != nil {
		return false
	}
	return true
}

// buildVoiceFake собирает валидный UDP-пакет: IP+UDP заголовки из оригинала
// (тот же src/dst, порты), payload = реальный bin, низкий TTL. Поля длины
// IP/UDP пересчитываются; контрольные суммы посчитает wd.send.
func buildVoiceFake(orig []byte, meta ipv4udp, payload []byte, ttl byte) []byte {
	if meta.dataOffset < 28 || meta.dataOffset > len(orig) {
		return nil
	}
	ihl := meta.dataOffset - 8 // длина IP-заголовка (UDP-заголовок = 8 байт)
	out := make([]byte, meta.dataOffset+len(payload))
	copy(out, orig[:meta.dataOffset]) // IP+UDP заголовки
	copy(out[meta.dataOffset:], payload)
	// IP total length (байты 2..3, big-endian)
	total := len(out)
	out[2] = byte(total >> 8)
	out[3] = byte(total)
	// UDP length (ihl+4 .. ihl+5)
	udpLen := 8 + len(payload)
	out[ihl+4] = byte(udpLen >> 8)
	out[ihl+5] = byte(udpLen)
	setTTL(out, ttl)
	setIPID(out, 0)
	// обнулить суммы перед пересчётом (IP checksum 10..11, UDP checksum ihl+6..7)
	out[10], out[11] = 0, 0
	out[ihl+6], out[ihl+7] = 0, 0
	return out
}
