package main

// Разбор TLS ClientHello внутри TCP-пейлоада, чтобы найти имя сайта (SNI)
// и его позицию — именно по ней мы будем «резать» пакет, чтобы DPI не склеил.

// tlsInfo — что нашли в пакете.
type tlsInfo struct {
	isClientHello bool
	sni           string
	sniOffset     int // смещение начала значения SNI в пределах payload
	sniLength     int
	fragmented    bool // TLS-запись не влезла в пакет (kyber ClientHello на 2+ пакета)
}

// parseTLSClientHello разбирает TCP-пейлоад. Возвращает tlsInfo.
// Формат TLS record: [ContentType(1)][Version(2)][Length(2)][Handshake...]
// Handshake: [HandshakeType(1)][Length(3)][Version(2)][Random(32)][SessionIDLen(1)][SessionID]
//
//	[CipherSuitesLen(2)][CipherSuites][CompLen(1)][Comp][ExtensionsLen(2)][Extensions]
//
// Extension SNI: type=0x0000, внутри список имён, тип 0 = hostname.
func parseTLSClientHello(payload []byte) tlsInfo {
	var info tlsInfo
	p := payload
	// минимальная длина TLS record header
	if len(p) < 5 {
		return info
	}
	// ContentType 0x16 = Handshake
	if p[0] != 0x16 {
		return info
	}
	// p[1],p[2] = версия (0x0301..0x0303); не критично проверять строго
	recLen := int(p[3])<<8 | int(p[4])
	if len(p) < 5+recLen {
		info.fragmented = true // ClientHello продолжается в следующем пакете (kyber)
	}
	hs := p[5:]
	if len(hs) < 4 {
		return info
	}
	// HandshakeType 0x01 = ClientHello
	if hs[0] != 0x01 {
		return info
	}
	info.isClientHello = true
	// hs[1..3] = длина handshake (3 байта)
	idx := 4 // после type(1)+len(3)
	// version(2)
	if len(hs) < idx+2 {
		return info
	}
	idx += 2
	// random(32)
	if len(hs) < idx+32 {
		return info
	}
	idx += 32
	// session id
	if len(hs) < idx+1 {
		return info
	}
	sidLen := int(hs[idx])
	idx += 1 + sidLen
	// cipher suites
	if len(hs) < idx+2 {
		return info
	}
	csLen := int(hs[idx])<<8 | int(hs[idx+1])
	idx += 2 + csLen
	// compression methods
	if len(hs) < idx+1 {
		return info
	}
	compLen := int(hs[idx])
	idx += 1 + compLen
	// extensions
	if len(hs) < idx+2 {
		return info
	}
	extTotal := int(hs[idx])<<8 | int(hs[idx+1])
	idx += 2
	extEnd := idx + extTotal
	if extEnd > len(hs) {
		extEnd = len(hs)
	}
	// перебираем расширения
	for idx+4 <= extEnd {
		extType := int(hs[idx])<<8 | int(hs[idx+1])
		extLen := int(hs[idx+2])<<8 | int(hs[idx+3])
		extData := idx + 4
		if extData+extLen > len(hs) {
			break
		}
		if extType == 0x0000 { // SNI
			// внутри: ServerNameList Length(2), затем [type(1)][len(2)][name]
			d := hs[extData : extData+extLen]
			if len(d) >= 5 {
				// d[0],d[1] = list len; d[2] = type (0=hostname); d[3],d[4]=name len
				nameLen := int(d[3])<<8 | int(d[4])
				nameStart := 5
				if nameStart+nameLen <= len(d) {
					info.sni = string(d[nameStart : nameStart+nameLen])
					// смещение SNI в пределах ВСЕГО payload:
					// payload -> +5 (tls header) -> +extData -> +nameStart
					info.sniOffset = 5 + extData + nameStart
					info.sniLength = nameLen
				}
			}
			break
		}
		idx = extData + extLen
	}
	return info
}
