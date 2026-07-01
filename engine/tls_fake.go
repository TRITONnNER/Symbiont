//go:build windows

package main

// Поддельный TLS ClientHello (как zapret-овский tls_clienthello_www_google_com.bin).
// Идея: перед НАСТОЯЩИМ ClientHello (или как seqovl-паттерн) шлём ПРАВДОПОДОБНЫЙ
// ClientHello с SNI = www.google.com. DPI видит «легитимный гугл» и/или путается,
// а настоящий запрос проходит. Это сильнее, чем просто битые байты.

import "encoding/binary"

// buildFakeClientHello строит правдоподобный TLS 1.2 ClientHello с заданным SNI.
// Не обязан быть идеально валидным для сервера (сервер его отбросит) — важно,
// чтобы DPI распознал его как ClientHello к этому домену.
func buildFakeClientHello(sni string) []byte {
	sniBytes := []byte(sni)

	// --- extensions ---
	var ext []byte

	// server_name (0x0000)
	serverNameList := make([]byte, 0, len(sniBytes)+5)
	serverNameList = append(serverNameList, 0x00) // type host_name
	hostLen := make([]byte, 2)
	binary.BigEndian.PutUint16(hostLen, uint16(len(sniBytes)))
	serverNameList = append(serverNameList, hostLen...)
	serverNameList = append(serverNameList, sniBytes...)
	snList := make([]byte, 2)
	binary.BigEndian.PutUint16(snList, uint16(len(serverNameList)))
	sniExtBody := append(snList, serverNameList...)
	ext = append(ext, 0x00, 0x00) // extension type server_name
	el := make([]byte, 2)
	binary.BigEndian.PutUint16(el, uint16(len(sniExtBody)))
	ext = append(ext, el...)
	ext = append(ext, sniExtBody...)

	// supported_groups (0x000a)
	ext = append(ext, 0x00, 0x0a, 0x00, 0x08, 0x00, 0x06, 0x00, 0x1d, 0x00, 0x17, 0x00, 0x18)
	// ec_point_formats (0x000b)
	ext = append(ext, 0x00, 0x0b, 0x00, 0x02, 0x01, 0x00)
	// signature_algorithms (0x000d)
	ext = append(ext, 0x00, 0x0d, 0x00, 0x08, 0x00, 0x06, 0x04, 0x03, 0x08, 0x04, 0x04, 0x01)
	// ALPN (0x0010) h2/http1.1
	ext = append(ext, 0x00, 0x10, 0x00, 0x0e, 0x00, 0x0c, 0x02, 0x68, 0x32, 0x08, 0x68, 0x74, 0x74, 0x70, 0x2f, 0x31, 0x2e, 0x31)
	// supported_versions (0x002b) TLS1.3+1.2
	ext = append(ext, 0x00, 0x2b, 0x00, 0x05, 0x04, 0x03, 0x04, 0x03, 0x03)

	extLen := make([]byte, 2)
	binary.BigEndian.PutUint16(extLen, uint16(len(ext)))

	// --- handshake body ---
	var body []byte
	body = append(body, 0x03, 0x03) // client_version TLS1.2
	random := make([]byte, 32)      // random (нули — для fake норм)
	body = append(body, random...)
	body = append(body, 0x20)                                           // session_id len 32
	body = append(body, make([]byte, 32)...)                            // session_id
	body = append(body, 0x00, 0x08)                                     // cipher_suites length
	body = append(body, 0x13, 0x01, 0x13, 0x02, 0x13, 0x03, 0xc0, 0x2f) // 4 suites
	body = append(body, 0x01, 0x00)                                     // compression: null
	body = append(body, extLen...)                                      // extensions length
	body = append(body, ext...)

	// --- handshake header ---
	hs := []byte{0x01} // ClientHello
	bl := make([]byte, 3)
	bl[0] = byte(len(body) >> 16)
	bl[1] = byte(len(body) >> 8)
	bl[2] = byte(len(body))
	hs = append(hs, bl...)
	hs = append(hs, body...)

	// --- TLS record header ---
	rec := []byte{0x16, 0x03, 0x01} // handshake, TLS1.0 record version
	rl := make([]byte, 2)
	binary.BigEndian.PutUint16(rl, uint16(len(hs)))
	rec = append(rec, rl...)
	rec = append(rec, hs...)
	return rec
}

// fakeTLSPattern — РЕАЛЬНЫЙ захваченный google ClientHello (681 байт, из zapret).
// Раньше тут был самодельный (buildFakeClientHello, ~170 байт) — он слабее и
// stateful-ТСПУ его распознавал. Реальный пакет ТСПУ принимает как настоящий.
// Фолбэк на синтетику оставлен на случай пустого embed.
var fakeTLSPattern = func() []byte {
	if len(fakeTLSGoogle) > 0 {
		return fakeTLSGoogle
	}
	return buildFakeClientHello("www.google.com")
}()
