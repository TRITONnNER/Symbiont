package main

// ── TLS-record фрагментация (уязвимость DPI из CCS 2023) ───────────────────
//
// Многие DPI (и ТСПУ) собирают TCP-сегменты, но НЕ собирают TLS-записи. Если
// один ClientHello разбить на ДВЕ TLS-записи так, чтобы SNI оказался на границе,
// DPI не может прочитать имя сервера. Работает на уровне приложения (исходящие),
// не требует видимости входящих. Поддерживается почти всеми TLS-серверами.
//
// Структура TLS-записи: [тип=0x16][версия 0x0301][длина 2 байта][содержимое].
// Берём содержимое (payload[5:]), режем в позиции split (внутри SNI) и заворачиваем
// каждую часть в свой 5-байтный заголовок записи. Итог на 5 байт длиннее.

// buildTLSRecordSplit строит payload из двух TLS-записей, разрезая по splitInContent
// (позиция в СОДЕРЖИМОМ записи, т.е. без первых 5 байт). Возвращает nil, если
// вход не похож на TLS-запись или позиция некорректна.
func buildTLSRecordSplit(payload []byte, splitInContent int) []byte {
	if len(payload) < 6 || payload[0] != 0x16 {
		return nil // не TLS handshake-запись
	}
	content := payload[5:]
	if splitInContent <= 0 || splitInContent >= len(content) {
		return nil
	}
	ver1, ver2 := payload[1], payload[2]
	a := content[:splitInContent]
	b := content[splitInContent:]
	out := make([]byte, 0, len(payload)+5)
	// запись 1
	out = append(out, 0x16, ver1, ver2, byte(len(a)>>8), byte(len(a)))
	out = append(out, a...)
	// запись 2
	out = append(out, 0x16, ver1, ver2, byte(len(b)>>8), byte(len(b)))
	out = append(out, b...)
	return out
}

// tlsrecSplitPos выбирает позицию разреза записи внутри SNI (в координатах
// содержимого записи). info.sniOffset — смещение SNI в payload (включая 5-байтный
// заголовок), поэтому для содержимого вычитаем 5.
func tlsrecSplitPos(info tlsInfo) int {
	if info.sniOffset <= 5 || info.sniLength <= 0 {
		return 0
	}
	mid := info.sniOffset - 5 + info.sniLength/2 // середина SNI в координатах содержимого
	return mid
}

// applyTLSRecordFrag отправляет ClientHello, разбитый на 2 TLS-записи (внутри SNI).
// Возвращает true, если применено.
func applyTLSRecordFrag(wd *winDivert, pkt []byte, addr *winDivertAddress, meta ipv4tcp, info tlsInfo, ipIDZero bool) bool {
	pos := tlsrecSplitPos(info)
	if pos <= 0 {
		return false
	}
	payload := pkt[meta.dataOffset:]
	frag := buildTLSRecordSplit(payload, pos)
	if frag == nil {
		return false
	}
	ipid := uint16(0x4a17)
	if ipIDZero {
		ipid = 0
	}
	seg := buildSegment(pkt, meta, frag, meta.seq, ipid)
	if seg == nil {
		return false
	}
	if err := wd.send(seg, addr); err != nil {
		return false
	}
	return true
}
