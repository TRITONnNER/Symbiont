package main

import "encoding/binary"

// ── Поддержка IPv6 (безопасный десинк через fake-decoy) ────────────────────
//
// IPv6-заголовок фиксированный, 40 байт:
//   [0]   версия(4 бита)+класс
//   [4:6] payload length (длина ПОСЛЕ 40-байтного заголовка)
//   [6]   next header (6 = TCP)
//   [7]   hop limit (аналог TTL)
//   [8:24]  src addr (16 б)   [24:40] dst addr (16 б)
//   [40]  начало TCP
// Десинк делаем максимально безопасно: впрыскиваем fake-decoy (реальный google
// ClientHello, низкий hop limit), а РЕАЛЬНЫЙ IPv6-пакет НЕ трогаем — он всегда
// уходит как есть. Поэтому IPv6-связность не сломается даже при ошибке.

// parseIPv6TCP разбирает IPv6+TCP. Расширенные заголовки не обрабатываем —
// если next header не TCP, возвращаем ok=false (пакет уйдёт как есть).
func parseIPv6TCP(pkt []byte) ipv4tcp {
	var r ipv4tcp
	if len(pkt) < 40 {
		return r
	}
	if pkt[0]>>4 != 6 {
		return r
	}
	if pkt[6] != 6 { // next header: 6 = TCP (расширенные заголовки пропускаем = passthrough)
		return r
	}
	r.ihl = 40 // длина IPv6-заголовка
	r.tcpOffset = 40
	tcp := pkt[40:]
	if len(tcp) < 20 {
		return r
	}
	r.srcPort = int(binary.BigEndian.Uint16(tcp[0:2]))
	r.dstPort = int(binary.BigEndian.Uint16(tcp[2:4]))
	r.seq = binary.BigEndian.Uint32(tcp[4:8])
	dataOff := int(tcp[12]>>4) * 4
	if dataOff < 20 || len(tcp) < dataOff {
		return r
	}
	r.dataOffset = 40 + dataOff
	if r.dataOffset > len(pkt) {
		return r
	}
	r.payloadLen = len(pkt) - r.dataOffset
	r.totalLen = len(pkt)
	r.ok = true
	return r
}

// setHopLimit ставит IPv6 hop limit (байт 7) — низкий, чтобы фейк умер до сервера.
func setHopLimit(pkt []byte, h byte) {
	if len(pkt) > 7 {
		pkt[7] = h
	}
}

// buildSegment6 собирает IPv6-TCP сегмент: заголовки из оригинала + payload,
// с пересчётом IPv6 payload length и нового seq. Контрольную сумму TCP посчитает
// wd.send (WinDivertHelperCalcChecksums).
func buildSegment6(orig []byte, meta ipv4tcp, data []byte, seq uint32) []byte {
	if meta.dataOffset < 40 || meta.dataOffset > len(orig) {
		return nil
	}
	seg := make([]byte, meta.dataOffset+len(data))
	copy(seg, orig[:meta.dataOffset])
	copy(seg[meta.dataOffset:], data)
	// IPv6 payload length = всё после 40-байтного заголовка
	binary.BigEndian.PutUint16(seg[4:6], uint16(len(seg)-40))
	setSeq(seg, meta.tcpOffset, seq)
	return seg
}

// ipv6DstKey — ключ соединения по IPv6 dst-адресу (16 байт) + порт. Простой хеш
// (FNV-подобный), чтобы поддержать вердикты варианта C и для IPv6.
func ipv6DstKey(pkt []byte, port int) uint64 {
	if len(pkt) < 40 {
		return 0
	}
	var h uint64 = 1469598103934665603
	for i := 24; i < 40; i++ { // dst addr
		h ^= uint64(pkt[i])
		h *= 1099511628211
	}
	h ^= uint64(port)
	return h
}

// applyDesync6 — безопасный IPv6-десинк. Впрыскивает fake-decoy для заблокированных
// доменов; реальный пакет НЕ трогает (его отправит главный цикл). Возвращает true,
// если впрыснул фейк (для лога).
func applyDesync6(wd *winDivert, pkt []byte, addr *winDivertAddress, hl *hostlist) bool {
	meta := parseIPv6TCP(pkt)
	if !meta.ok || meta.payloadLen <= 0 {
		return false
	}
	info := parseTLSClientHello(pkt[meta.dataOffset:])
	if !info.isClientHello {
		return false
	}
	noteSNI(info.sni)
	key := ipv6DstKey(pkt, meta.dstPort)
	if behaviorEnabled() {
		behavior.onOutboundData(key, info.sni, meta.seq, true)
	}
	hostHit := hl.match(info.sni)
	blocked := hostHit || (behaviorEnabled() && behavior.shouldDesync(info.sni, "", hostHit))
	if !blocked {
		return false
	}
	// видимость + цикл самоподбора (как в IPv4)
	logClientHelloSeen(info.sni, true, info.fragmented)
	if behaviorEnabled() {
		behavior.markDesynced(key, behavior.techIdxFor(info.sni))
	}
	payload := pkt[meta.dataOffset:]
	// позиция разреза — у SNI (рвём имя сайта между TCP-сегментами)
	cut := info.sniOffset + info.sniLength/2
	if cut <= 0 || cut >= len(payload) {
		cut = len(payload) / 2
	}
	// 1) fake-decoy: реальный google ClientHello, низкий hop limit (умрёт не дойдя
	// до сервера), отравляет DPI. Реальные данные целы.
	if len(fakeTLSGoogle) > 0 {
		for r := 0; r < 2; r++ {
			if fake := buildSegment6(pkt, meta, fakeTLSGoogle, meta.seq); fake != nil {
				setHopLimit(fake, 4)
				if foolingMode() == corruptHopByHop {
					if h := addHopByHop6(fake); h != nil {
						fake = h
					}
				}
				_ = wd.sendRaw(fake, addr)
			}
		}
	}
	// 2) РЕАЛЬНЫЙ SPLIT (это и был пропущенный шаг!): рвём ClientHello на 2 TCP-
	// сегмента по SNI и шлём в обратном порядке (disorder). Сервер соберёт по TCP,
	// а DPI не увидит цельного имени сайта. Раньше тут была только заглушка —
	// поэтому SNI оставался виден и ТСПУ резал по IPv6.
	part1 := buildSegment6(pkt, meta, payload[:cut], meta.seq)
	part2 := buildSegment6(pkt, meta, payload[cut:], meta.seq+uint32(cut))
	if part1 == nil || part2 == nil {
		return false // не вышло собрать — пусть уйдёт как есть (главный цикл)
	}
	_ = wd.send(part2, addr) // disorder: сначала вторую часть
	_ = wd.send(part1, addr) // потом первую
	if kyberLogOnce() {
		logStepf("sni", "IPv6: %q заблокирован → разрезал ClientHello по SNI (split+fake-decoy)", info.sni)
	}
	return true
}

// parseIPv6UDP разбирает IPv6+UDP (без расширенных заголовков). dataOffset=48.
func parseIPv6UDP(pkt []byte) ipv4udp {
	var r ipv4udp
	if len(pkt) < 48 {
		return r
	}
	if pkt[0]>>4 != 6 || pkt[6] != 17 { // 17 = UDP
		return r
	}
	r.ihl = 40
	r.udpOffset = 40
	r.dataOffset = 48
	r.srcPort = int(binary.BigEndian.Uint16(pkt[40:42]))
	r.dstPort = int(binary.BigEndian.Uint16(pkt[42:44]))
	r.payloadLen = len(pkt) - 48
	if r.payloadLen < 0 {
		return r
	}
	r.ok = true
	return r
}

// ipv6SrcKey — ключ соединения по IPv6 SRC-адресу (для входящих от сервера).
// Симметричен ipv6DstKey: dst(исх) == src(вх), порт dst(исх) == src(вх).
func ipv6SrcKey(pkt []byte, port int) uint64 {
	if len(pkt) < 40 {
		return 0
	}
	var h uint64 = 1469598103934665603
	for i := 8; i < 24; i++ { // src addr
		h ^= uint64(pkt[i])
		h *= 1099511628211
	}
	h ^= uint64(port)
	return h
}

// handleInbound6 — наблюдение за входящими IPv6-TCP ответами (RST/данные) для
// варианта C. Только наблюдаем, пакет не трогаем.
func handleInbound6(pkt []byte) {
	if len(pkt) < 40 || pkt[0]>>4 != 6 || pkt[6] != 6 { // TCP
		return
	}
	meta := parseIPv6TCP(pkt)
	if !meta.ok {
		return
	}
	key := ipv6SrcKey(pkt, meta.srcPort)
	if tcpHasRST(pkt, meta) {
		if behaviorEnabled() {
			behavior.onInboundRST(key)
		}
		atomicAddInRST()
		return
	}
	if meta.payloadLen > 0 && behaviorEnabled() {
		behavior.onInboundData(key)
		atomicAddInData()
	}
}

// addHopByHop6 вставляет в IPv6-fake пустой Hop-by-Hop extension header (8 байт).
// Структура: после 40-байтного IPv6-заголовка идёт [next_hdr=6(TCP)][hdr_ext_len=0]
// [6 байт PadN-опции], а в основном заголовке next header меняется на 0 (Hop-by-Hop).
// DPI, не идущий по цепочке расширенных заголовков, не понимает, что внутри TCP,
// и пропускает фейк без анализа. Сервер фейк всё равно отбросит (низкий hop limit).
// Это IPv6-аналог fooling. Возвращает новый пакет; nil при ошибке.
func addHopByHop6(pkt []byte) []byte {
	if len(pkt) < 40 || pkt[0]>>4 != 6 {
		return nil
	}
	origNext := pkt[6] // что было следующим (обычно 6 = TCP)
	// Hop-by-Hop заголовок: [next=origNext][len=0][PadN opt: type=1,len=4,4 байта 0]
	hbh := []byte{origNext, 0, 0x01, 0x04, 0, 0, 0, 0}
	out := make([]byte, 0, len(pkt)+8)
	out = append(out, pkt[:40]...) // основной IPv6-заголовок
	out = append(out, hbh...)      // вставляем расширенный заголовок
	out = append(out, pkt[40:]...) // дальше TCP/данные
	out[6] = 0                     // next header основного = 0 (Hop-by-Hop)
	// payload length += 8
	binary.BigEndian.PutUint16(out[4:6], uint16(len(out)-40))
	return out
}

// buildUDPFake6 — валидный IPv6 UDP-пакет с фейк-нагрузкой и низким hop limit
// (для QUIC-фейка по IPv6). Суммы пересчитает wd.send.
func buildUDPFake6(orig []byte, meta ipv4udp, payload []byte, hop byte) []byte {
	if meta.dataOffset < 48 || meta.dataOffset > len(orig) {
		return nil
	}
	out := make([]byte, meta.dataOffset+len(payload))
	copy(out, orig[:meta.dataOffset])
	copy(out[meta.dataOffset:], payload)
	binary.BigEndian.PutUint16(out[4:6], uint16(len(out)-40))      // IPv6 payload length
	binary.BigEndian.PutUint16(out[44:46], uint16(8+len(payload))) // UDP length
	out[7] = hop                                                   // hop limit
	out[46], out[47] = 0, 0                                        // обнулить UDP checksum для пересчёта
	return out
}

// applyQUICDesync6 — QUIC-фейк по IPv6 (как IPv4 applyQUICDesync): на QUIC Initial
// шлём фейк google-бином с низким hop limit (отравляет DPI, до сервера не дойдёт),
// реальный пакет ВСЕГДА пропускаем. Без этого YouTube по QUIC-over-IPv6 не брался.
func applyQUICDesync6(wd *winDivert, pkt []byte, addr *winDivertAddress, hop byte) bool {
	meta := parseIPv6UDP(pkt)
	if !meta.ok || meta.payloadLen <= 0 {
		return false
	}
	payload := pkt[meta.dataOffset:]
	if !isQUICInitial(payload) {
		return false
	}
	if len(fakeQUICGoogle) > 0 {
		for r := 0; r < 6; r++ {
			if f := buildUDPFake6(pkt, meta, fakeQUICGoogle, hop); f != nil {
				_ = wd.send(f, addr)
			}
		}
	}
	if err := wd.send(pkt, addr); err != nil {
		return false
	}
	return true
}
