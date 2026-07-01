package main

import "encoding/binary"

// Минимальный разбор IPv4 + TCP, чтобы найти начало TCP-пейлоада,
// прочитать/изменить sequence number и пересобрать пакет для переотправки.

type ipv4tcp struct {
	ok         bool
	ihl        int // длина IP-заголовка в байтах
	tcpOffset  int // смещение начала TCP-заголовка
	dataOffset int // смещение начала TCP-пейлоада (после TCP-заголовка)
	totalLen   int
	srcPort    int
	dstPort    int
	seq        uint32
	payloadLen int
}

// parseIPv4TCP разбирает пакет (как его отдаёт WinDivert на network-слое: с IP-заголовка).
func parseIPv4TCP(pkt []byte) ipv4tcp {
	var r ipv4tcp
	if len(pkt) < 20 {
		return r
	}
	version := pkt[0] >> 4
	if version != 4 {
		return r // пока только IPv4 (IPv6 добавим позже)
	}
	r.ihl = int(pkt[0]&0x0f) * 4
	if r.ihl < 20 || len(pkt) < r.ihl+20 {
		return r
	}
	proto := pkt[9]
	if proto != 6 { // 6 = TCP
		return r
	}
	// Фрагментированный IP-пакет (MF=0x2000 или ненулевой offset): у фрагмента
	// нет полного TCP-заголовка в начале → НЕ трогаем, чтобы не сломать. ok=false
	// → главный цикл отправит как есть.
	fragField := binary.BigEndian.Uint16(pkt[6:8])
	if fragField&0x2000 != 0 || fragField&0x1FFF != 0 {
		return r
	}
	r.totalLen = int(binary.BigEndian.Uint16(pkt[2:4]))
	r.tcpOffset = r.ihl
	tcp := pkt[r.tcpOffset:]
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
	r.dataOffset = r.tcpOffset + dataOff
	if r.dataOffset > len(pkt) {
		return r
	}
	r.payloadLen = len(pkt) - r.dataOffset
	r.ok = true
	return r
}

// setSeq записывает новый sequence number в TCP-заголовок пакета.
func setSeq(pkt []byte, tcpOffset int, seq uint32) {
	binary.BigEndian.PutUint32(pkt[tcpOffset+4:tcpOffset+8], seq)
}

// setIPTotalLen обновляет поле Total Length в IP-заголовке.
func setIPTotalLen(pkt []byte, n int) {
	binary.BigEndian.PutUint16(pkt[2:4], uint16(n))
}

// setIPID меняет идентификатор IP-пакета (чтобы две части не имели одинаковый ID).
func setIPID(pkt []byte, id uint16) {
	binary.BigEndian.PutUint16(pkt[4:6], id)
}

// setTTL ставит IP TTL (поле на смещении 8). Низкий TTL → пакет умрёт по дороге
// (дойдёт до DPI рядом, не дойдёт до сервера) — основа fake-пакетов.
func setTTL(pkt []byte, ttl byte) {
	if len(pkt) > 8 {
		pkt[8] = ttl
	}
}

// corruptTCPChecksum портит контрольную сумму TCP, чтобы СЕРВЕР отбросил пакет,
// а DPI (часто не проверяет суммы) — принял. Используется в fake-пакетах.
// ВАЖНО: вызывать ПОСЛЕ отправки настоящих, и НЕ пересчитывать суммы при send.
func corruptTCPChecksum(pkt []byte, tcpOffset int) {
	if len(pkt) >= tcpOffset+18 {
		// TCP checksum — на смещении 16..18 от начала TCP-заголовка
		pkt[tcpOffset+16] ^= 0xFF
		pkt[tcpOffset+17] ^= 0xFF
	}
}

// setSeqOffset сдвигает sequence number на delta (может быть отрицательным как uint32).
func setSeqOffset(pkt []byte, tcpOffset int, base uint32, delta int) {
	setSeq(pkt, tcpOffset, base+uint32(delta))
}

// --- UDP / QUIC ---

type ipv4udp struct {
	ok         bool
	ihl        int
	udpOffset  int
	dataOffset int // начало UDP-данных (QUIC payload)
	srcPort    int
	dstPort    int
	payloadLen int
}

// parseIPv4UDP разбирает IPv4+UDP пакет.
func parseIPv4UDP(pkt []byte) ipv4udp {
	var r ipv4udp
	if len(pkt) < 20 {
		return r
	}
	if pkt[0]>>4 != 4 {
		return r
	}
	r.ihl = int(pkt[0]&0x0f) * 4
	if r.ihl < 20 || len(pkt) < r.ihl+8 {
		return r
	}
	if pkt[9] != 17 { // 17 = UDP
		return r
	}
	r.udpOffset = r.ihl
	udp := pkt[r.udpOffset:]
	r.srcPort = int(binary.BigEndian.Uint16(udp[0:2]))
	r.dstPort = int(binary.BigEndian.Uint16(udp[2:4]))
	r.dataOffset = r.udpOffset + 8 // UDP-заголовок = 8 байт
	if r.dataOffset > len(pkt) {
		return r
	}
	r.payloadLen = len(pkt) - r.dataOffset
	r.ok = true
	return r
}

// isQUICInitial грубо определяет QUIC Initial-пакет (long header, type Initial).
// QUIC: первый байт, бит 0x80 = long header; бит 0x40 = fixed; тип в битах 0x30.
// Для QUIC v1 Initial: (b & 0xF0) == 0xC0 (long header + Initial type 00).
func isQUICInitial(payload []byte) bool {
	if len(payload) < 5 {
		return false
	}
	b := payload[0]
	if b&0x80 == 0 {
		return false // не long header (short header = уже установленное соединение)
	}
	ver := binary.BigEndian.Uint32(payload[1:5])
	if ver == 0 {
		return false // 0 = Version Negotiation
	}
	typeBits := b & 0x30
	// QUIC v2 (RFC 9369, ver=0x6b3343cf) ПЕРЕКОДИРОВАЛ типы: Initial = 01, а не 00.
	// Chrome давно умеет v2 — раньше мы его Initial НЕ узнавали и пропускали как
	// данные. Теперь ловим оба.
	if ver == 0x6b3343cf {
		return typeBits == 0x10 // v2: Initial = 01
	}
	// QUIC v1 (0x00000001) и черновики: Initial = 00.
	return typeBits == 0x00
}

// --- Метод 3: IP-фрагментация ---

// ipFragmentTCP делит IPv4-пакет на два IP-фрагмента по границе TCP-данных.
// Возвращает два готовых пакета (или nil, если не получилось).
// ВНИМАНИЕ: IP-фрагменты часто режутся роутерами — это эксперимент.
func ipFragmentTCP(pkt []byte, meta ipv4tcp, splitAt int) ([]byte, []byte) {
	if splitAt <= 0 || splitAt >= meta.payloadLen {
		return nil, nil
	}
	// данные TCP (заголовок TCP идёт только в первом фрагменте)
	tcpHdrLen := meta.dataOffset - meta.tcpOffset
	// первый фрагмент: IP + TCP-заголовок + первая часть данных
	firstDataLen := tcpHdrLen + splitAt
	if firstDataLen%8 != 0 {
		// смещение фрагмента должно быть кратно 8 — подгоняем splitAt
		firstDataLen -= firstDataLen % 8
		if firstDataLen <= tcpHdrLen {
			return nil, nil
		}
	}
	ihl := meta.ihl
	frag1 := make([]byte, ihl+firstDataLen)
	copy(frag1, pkt[:ihl+firstDataLen])
	setIPTotalLen(frag1, len(frag1))
	// флаг MF (More Fragments) = бит 0x2000 в поле flags/offset (байты 6-7)
	frag1[6] = (frag1[6] & 0x1f) | 0x20 // ставим MF
	frag1[7] = 0x00                     // offset = 0

	// второй фрагмент: IP-заголовок + остаток данных
	restLen := (ihl + meta.payloadLen + tcpHdrLen) - (ihl + firstDataLen)
	_ = restLen
	secondData := pkt[ihl+firstDataLen:]
	frag2 := make([]byte, ihl+len(secondData))
	copy(frag2[:ihl], pkt[:ihl])
	copy(frag2[ihl:], secondData)
	setIPTotalLen(frag2, len(frag2))
	// offset второго фрагмента в 8-байтных блоках
	fragOff := firstDataLen / 8
	frag2[6] = byte((fragOff >> 8) & 0x1f) // MF=0 (последний)
	frag2[7] = byte(fragOff & 0xff)

	return frag1, frag2
}
