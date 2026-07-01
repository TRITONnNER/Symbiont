package main

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/binary"
	"strings"
	"sync"
	"sync/atomic"
)

// ── QUIC Initial: крипто + дробление CRYPTO-фреймов ────────────────────────
//
// QUIC Initial зашифрован, но ключи Initial выводятся из ПУБЛИЧНОГО DCID и
// фиксированного salt (RFC 9001) — значит мы (как и сервер, и DPI) можем их
// вывести. Дробление: расшифровываем → ClientHello лежит в CRYPTO-фрейме →
// режем его на ДВА CRYPTO-фрейма внутри SNI → заново шифруем. Сервер собирает
// фреймы по offset (получает целый ClientHello), а DPI, не собирающий CRYPTO-
// фреймы, не видит SNI. Деривация ключей и расшифровка СВЕРЕНЫ с тест-векторами
// RFC 9001 и проверены на реальном QUIC Initial.
//
// Безопасность: при ЛЮБОЙ ошибке (не Initial, не расшифровалось, нет SNI и т.п.)
// функция возвращает false → исходный пакет уходит как есть. QUIC не сломается.

var quicInitialSalt = []byte{
	0x38, 0x76, 0x2c, 0xf7, 0xf5, 0x59, 0x34, 0xb3, 0x4d, 0x17,
	0x9a, 0xe6, 0xa4, 0xc8, 0x0c, 0xad, 0xcc, 0xbb, 0x7f, 0x0a,
}

func quicHkdfExtract(salt, ikm []byte) []byte {
	h := hmac.New(sha256.New, salt)
	h.Write(ikm)
	return h.Sum(nil)
}

func quicHkdfExpand(prk, info []byte, l int) []byte {
	var out, t []byte
	for i := 1; len(out) < l; i++ {
		h := hmac.New(sha256.New, prk)
		h.Write(t)
		h.Write(info)
		h.Write([]byte{byte(i)})
		t = h.Sum(nil)
		out = append(out, t...)
	}
	return out[:l]
}

func quicExpandLabel(secret []byte, label string, l int) []byte {
	full := "tls13 " + label
	info := []byte{byte(l >> 8), byte(l), byte(len(full))}
	info = append(info, full...)
	info = append(info, 0)
	return quicHkdfExpand(secret, info, l)
}

// quicClientKeys выводит key/iv/hp для клиентского Initial из DCID.
func quicClientKeys(dcid []byte) (key, iv, hp []byte) {
	initial := quicHkdfExtract(quicInitialSalt, dcid)
	cis := quicExpandLabel(initial, "client in", 32)
	return quicExpandLabel(cis, "quic key", 16),
		quicExpandLabel(cis, "quic iv", 12),
		quicExpandLabel(cis, "quic hp", 16)
}

// quic varint
func quicReadVarint(b []byte) (uint64, int) {
	if len(b) == 0 {
		return 0, 0
	}
	pre := b[0] >> 6
	l := 1 << pre
	if len(b) < l {
		return 0, 0
	}
	v := uint64(b[0] & 0x3f)
	for i := 1; i < l; i++ {
		v = (v << 8) | uint64(b[i])
	}
	return v, l
}

func quicAppendVarint(b []byte, v uint64) []byte {
	switch {
	case v < 64:
		return append(b, byte(v))
	case v < 16384:
		return append(b, byte(v>>8)|0x40, byte(v))
	case v < 1073741824:
		return append(b, byte(v>>24)|0x80, byte(v>>16), byte(v>>8), byte(v))
	default:
		return append(b, byte(v>>56)|0xc0, byte(v>>48), byte(v>>40), byte(v>>32),
			byte(v>>24), byte(v>>16), byte(v>>8), byte(v))
	}
}

// quicInitialHeader — разобранные смещения QUIC Initial.
type quicInitialHeader struct {
	ok     bool
	dcid   []byte
	pnOff  int // смещение packet number (после защищённого заголовка)
	plen   int // payload length (включает pn + ciphertext)
	hdrEnd int // конец «полного» Initial-пакета в датаграмме (pnOff+plen)
}

// parseQUICInitialHeader разбирает long header Initial (без снятия защиты).
func parseQUICInitialHeader(pkt []byte) quicInitialHeader {
	var h quicInitialHeader
	if len(pkt) < 7 || pkt[0]&0x80 == 0 {
		return h // не long header
	}
	// тип Initial: биты 0x30==0x00 в длинном заголовке (после version)
	if binary.BigEndian.Uint32(pkt[1:5]) != 1 {
		return h // только QUIC v1
	}
	if (pkt[0] & 0x30) != 0x00 {
		return h // не Initial
	}
	off := 5
	dl := int(pkt[off])
	off++
	if off+dl > len(pkt) {
		return h
	}
	h.dcid = pkt[off : off+dl]
	off += dl
	if off >= len(pkt) {
		return h
	}
	sl := int(pkt[off])
	off++
	off += sl
	if off >= len(pkt) {
		return h
	}
	tl, n := quicReadVarint(pkt[off:])
	if n == 0 {
		return h
	}
	off += n + int(tl)
	if off >= len(pkt) {
		return h
	}
	plen, n2 := quicReadVarint(pkt[off:])
	if n2 == 0 {
		return h
	}
	off += n2
	h.pnOff = off
	h.plen = int(plen)
	h.hdrEnd = off + int(plen)
	if h.hdrEnd > len(pkt) || h.plen < 20 {
		return h
	}
	h.ok = true
	return h
}

// decryptQUICInitial снимает HP и расшифровывает payload. Возвращает plaintext,
// длину pn, само значение pn-байтов, незащищённый первый байт.
func decryptQUICInitial(pkt []byte, h quicInitialHeader, key, iv, hp []byte) (plain []byte, pnLen int, pnBytes []byte, b0 byte, ok bool) {
	if h.pnOff+4+16 > len(pkt) {
		return nil, 0, nil, 0, false
	}
	blk, err := aes.NewCipher(hp)
	if err != nil {
		return nil, 0, nil, 0, false
	}
	sample := pkt[h.pnOff+4 : h.pnOff+4+16]
	mask := make([]byte, 16)
	blk.Encrypt(mask, sample)
	b0 = pkt[0] ^ (mask[0] & 0x0f)
	pnLen = int(b0&0x03) + 1
	pnBytes = make([]byte, pnLen)
	for i := 0; i < pnLen; i++ {
		pnBytes[i] = pkt[h.pnOff+i] ^ mask[1+i]
	}
	hdr := make([]byte, h.pnOff+pnLen)
	copy(hdr, pkt[:h.pnOff+pnLen])
	hdr[0] = b0
	for i := 0; i < pnLen; i++ {
		hdr[h.pnOff+i] = pnBytes[i]
	}
	ct := pkt[h.pnOff+pnLen : h.hdrEnd]
	kb, err := aes.NewCipher(key)
	if err != nil {
		return nil, 0, nil, 0, false
	}
	aead, err := cipher.NewGCM(kb)
	if err != nil {
		return nil, 0, nil, 0, false
	}
	nonce := make([]byte, 12)
	copy(nonce, iv)
	for i := 0; i < pnLen; i++ {
		nonce[12-pnLen+i] ^= pnBytes[i]
	}
	pt, err := aead.Open(nil, nonce, ct, hdr)
	if err != nil {
		return nil, 0, nil, 0, false
	}
	return pt, pnLen, pnBytes, b0, true
}

// extractCryptoCH собирает данные CRYPTO-фреймов (по offset) в непрерывный
// ClientHello. Возвращает байты ClientHello (с offset 0).
func extractCryptoCH(plain []byte) []byte {
	var ch []byte
	i := 0
	for i < len(plain) {
		ft := plain[i]
		if ft == 0x00 { // PADDING
			i++
			continue
		}
		if ft == 0x06 { // CRYPTO
			i++
			off, n1 := quicReadVarint(plain[i:])
			if n1 == 0 {
				break
			}
			i += n1
			ln, n2 := quicReadVarint(plain[i:])
			if n2 == 0 {
				break
			}
			i += n2
			if i+int(ln) > len(plain) {
				break
			}
			data := plain[i : i+int(ln)]
			i += int(ln)
			// складываем по offset (обычно offset=0, один фрейм)
			need := int(off) + len(data)
			if need > len(ch) {
				nb := make([]byte, need)
				copy(nb, ch)
				ch = nb
			}
			copy(ch[off:], data)
			continue
		}
		break // другой тип фрейма — дальше не идём
	}
	return ch
}

// buildSplitQUICInitial пересобирает Initial: ClientHello режется на 2 CRYPTO-
// фрейма внутри SNI, паддинг до исходной длины, заново шифруется и защищается.
// Возвращает новый пакет той же длины. nil при ошибке.
func buildSplitQUICInitial(pkt []byte, h quicInitialHeader, key, iv, hp, pnBytes []byte, pnLen int, b0 byte, ch []byte, splitPos int) []byte {
	if splitPos <= 0 || splitPos >= len(ch) {
		return nil
	}
	// новый plaintext: CRYPTO(0..split) + CRYPTO(split..end) + PADDING
	var pt []byte
	pt = append(pt, 0x06)
	pt = quicAppendVarint(pt, 0)
	pt = quicAppendVarint(pt, uint64(splitPos))
	pt = append(pt, ch[:splitPos]...)
	pt = append(pt, 0x06)
	pt = quicAppendVarint(pt, uint64(splitPos))
	pt = quicAppendVarint(pt, uint64(len(ch)-splitPos))
	pt = append(pt, ch[splitPos:]...)
	// исходная длина plaintext = ciphertext - 16 (тег GCM)
	origPTLen := (h.hdrEnd - (h.pnOff + pnLen)) - 16
	if len(pt) > origPTLen {
		return nil // не влезли (редко)
	}
	for len(pt) < origPTLen {
		pt = append(pt, 0x00) // PADDING
	}
	// заголовок (незащищённый b0 и pn) — это associated data
	hdr := make([]byte, h.pnOff+pnLen)
	copy(hdr, pkt[:h.pnOff+pnLen])
	hdr[0] = b0
	for i := 0; i < pnLen; i++ {
		hdr[h.pnOff+i] = pnBytes[i]
	}
	kb, err := aes.NewCipher(key)
	if err != nil {
		return nil
	}
	aead, err := cipher.NewGCM(kb)
	if err != nil {
		return nil
	}
	nonce := make([]byte, 12)
	copy(nonce, iv)
	for i := 0; i < pnLen; i++ {
		nonce[12-pnLen+i] ^= pnBytes[i]
	}
	ct := aead.Seal(nil, nonce, pt, hdr)
	// собираем пакет: [hdr с pn] + ct, затем накладываем HP
	out := make([]byte, 0, h.pnOff+pnLen+len(ct))
	out = append(out, hdr...)
	out = append(out, ct...)
	// header protection заново
	blk, err := aes.NewCipher(hp)
	if err != nil {
		return nil
	}
	if h.pnOff+4+16 > len(out) {
		return nil
	}
	sample := out[h.pnOff+4 : h.pnOff+4+16]
	mask := make([]byte, 16)
	blk.Encrypt(mask, sample)
	out[0] = b0 ^ (mask[0] & 0x0f)
	for i := 0; i < pnLen; i++ {
		out[h.pnOff+i] = pnBytes[i] ^ mask[1+i]
	}
	// длина итогового пакета должна совпасть с исходным Initial (plen не менялся)
	if len(out) != h.hdrEnd {
		return nil
	}
	return out
}

// applyQUICSplit — дробит QUIC Initial по CRYPTO-фрейму внутри SNI, если домен
// заблокирован. Возвращает true, если переписал и отправил пакет. Любая ошибка →
// false (исходный пакет отправит вызывающий — QUIC не сломается).
func applyQUICSplit(wd *winDivert, pkt []byte, addr *winDivertAddress, meta ipv4udp, hl *hostlist) bool {
	dgram := pkt[meta.dataOffset:]
	h := parseQUICInitialHeader(dgram)
	if !h.ok {
		return false
	}
	key, iv, hp := quicClientKeys(h.dcid)
	plain, pnLen, pnBytes, b0, ok := decryptQUICInitial(dgram, h, key, iv, hp)
	if !ok {
		return false
	}
	ch := extractCryptoCH(plain)
	if len(ch) < 8 {
		return false
	}
	info := parseTLSClientHello(ch) // ClientHello внутри QUIC = тот же формат, что TLS
	if !info.isClientHello {
		return false
	}
	noteSNI(info.sni)
	hostHit := hl.match(info.sni)
	blocked := hostHit || (behaviorEnabled() && behavior.shouldDesync(info.sni, "", hostHit))
	if !blocked {
		return false
	}
	// позиция разреза — внутри SNI (чтобы имя ушло за границу фрейма)
	splitPos := info.sniOffset + info.sniLength/2
	if splitPos <= 0 || splitPos >= len(ch) {
		splitPos = len(ch) / 2
	}
	newDgram := buildSplitQUICInitial(dgram, h, key, iv, hp, pnBytes, pnLen, b0, ch, splitPos)
	if newDgram == nil {
		return false
	}
	// собираем IP/UDP пакет: заголовки оригинала + новый QUIC-датаграм той же длины
	out := make([]byte, meta.dataOffset+len(newDgram))
	copy(out, pkt[:meta.dataOffset])
	copy(out[meta.dataOffset:], newDgram)
	if err := wd.send(out, addr); err != nil { // send пересчитает UDP-сумму
		return false
	}
	if quicSplitLogOnce() {
		logStepf("quic", "QUIC Initial %q: ClientHello разрезан на 2 CRYPTO-фрейма внутри SNI (DPI не соберёт)", info.sni)
	}
	return true
}

// переключатель и лог-ограничитель для QUIC-splitting
var quicSplitOn int32 = 0

func setQUICSplitEnabled(v bool) {
	if v {
		atomic.StoreInt32(&quicSplitOn, 1)
	} else {
		atomic.StoreInt32(&quicSplitOn, 0)
	}
}
func quicSplitEnabled() bool { return atomic.LoadInt32(&quicSplitOn) == 1 }

var quicSplitCnt int64

func quicSplitLogOnce() bool {
	n := atomic.AddInt64(&quicSplitCnt, 1)
	return n == 1 || n%100 == 0
}

// ─────────────────────────────────────────────────────────────────────────────
// Discord по QUIC под ТСПУ работает нестабильно (API/контент по HTTP/3 виснет на
// таймаутах, чат/настройки не грузятся, голос при этом идёт по своему каналу).
// Лечение, подтверждённое тестом «блок UDP/443 в фаерволе → Discord ожил»:
// принудительно роняем QUIC ИМЕННО Discord — клиент сразу падает на TCP/TLS,
// где наш обход (disorder+seqovl681) уже работает. Точечно по SNI: YouTube и
// прочий QUIC не трогаем, поэтому видео по QUIC не регрессит.
// ─────────────────────────────────────────────────────────────────────────────

func isDiscordSNI(sni string) bool {
	s := strings.ToLower(strings.TrimSpace(sni))
	if s == "" {
		return false
	}
	// ВНИМАНИЕ: discord.media НЕ включаем — это домен голоса/медиа, который у нас
	// уже работает (голосовой UDP десинкается отдельно). Роняем QUIC только у
	// чата/API/CDN, где висит загрузка сообщений и настроек.
	for _, suf := range []string{
		"discord.com", "discord.gg",
		"discordapp.com", "discordapp.net", "discord.dev",
		"discordstatus.com", "discordapp.io",
	} {
		if s == suf || strings.HasSuffix(s, "."+suf) {
			return true
		}
	}
	return false
}

// quicInitialPeekSNI разбирает QUIC Initial и возвращает его SNI, НЕ меняя пакет.
// ok=false, если это не разбираемый Initial (короткий заголовок/фрагмент/ECH).
func quicInitialPeekSNI(pkt []byte, meta ipv4udp) (sni string, ok bool) {
	if !meta.ok || meta.payloadLen <= 0 {
		return "", false
	}
	dgram := pkt[meta.dataOffset:]
	if !isQUICInitial(dgram) {
		return "", false
	}
	h := parseQUICInitialHeader(dgram)
	if !h.ok {
		return "", false
	}
	key, iv, hp := quicClientKeys(h.dcid)
	plain, _, _, _, dok := decryptQUICInitial(dgram, h, key, iv, hp)
	if !dok {
		return "", false
	}
	ch := extractCryptoCH(plain)
	if len(ch) < 8 {
		return "", false
	}
	info := parseTLSClientHello(ch)
	if !info.isClientHello {
		return "", false
	}
	return info.sni, true
}

// набор Discord-IP, к которым уже видели QUIC → роняем и established-датаграммы
// (short-header), чтобы живая до старта движка QUIC-сессия тоже умерла и Discord
// переоткрыл её по TCP. Само-ограничено по размеру.
var (
	discordQUICIPs   = map[uint32]bool{}
	discordQUICIPsMu sync.Mutex
)

func markDiscordQUICIP(ip uint32) {
	discordQUICIPsMu.Lock()
	if len(discordQUICIPs) > 4096 {
		discordQUICIPs = map[uint32]bool{}
	}
	discordQUICIPs[ip] = true
	discordQUICIPsMu.Unlock()
}

func isDiscordQUICIP(ip uint32) bool {
	discordQUICIPsMu.Lock()
	v := discordQUICIPs[ip]
	discordQUICIPsMu.Unlock()
	return v
}

var discordQUICDropCnt int64

// dropDiscordQUIC решает и считает дроп Discord-QUIC. Возвращает true, если пакет
// надо уронить (не отправлять). Ловит Discord-QUIC ТРЕМЯ путями:
//   1) Initial с читаемым Discord-SNI;
//   2) Initial/датаграмма к IP, который мы уже знаем как Discord (выучен из
//      ЧИТАЕМОГО TCP ClientHello — recordHostIP). Это главный путь: QUIC-Initial
//      Discord часто kyber-фрагмент, SNI в одном пакете не виден, но IP тот же,
//      что у TCP-соединения discord.com/discord.gg;
//   3) established short-header к ранее замеченному Discord-QUIC-IP.
var discordQUICDropOn int32 // 0 = выкл (по умолчанию)

func setDiscordQUICDrop(v bool) {
	if v {
		atomic.StoreInt32(&discordQUICDropOn, 1)
	} else {
		atomic.StoreInt32(&discordQUICDropOn, 0)
	}
}

func dropDiscordQUIC(pkt []byte, meta ipv4udp) bool {
	if atomic.LoadInt32(&discordQUICDropOn) == 0 {
		return false // по умолчанию выключено — Discord виснет не из-за QUIC
	}
	if !meta.ok || meta.dstPort != 443 || meta.payloadLen <= 0 {
		return false
	}
	dst := ipv4DstIP(pkt)
	// (2) IP уже известен как Discord (по TCP-рукопожатию discord.com/gg/media/app
	// — набор isDiscordIP, либо по hostForIP) → роняем весь его QUIC, чтобы клиент
	// ушёл на TCP, где обход работает (там грузятся API/сообщения/настройки).
	if isDiscordIP(dst) || isDiscordSNI(hostForIP(dst)) {
		markDiscordQUICIP(dst)
		n := atomic.AddInt64(&discordQUICDropCnt, 1)
		if n <= 3 || n%100 == 0 {
			logStepf("quic", "Discord по QUIC уронен (IP %s = Discord по TCP-CH) → клиент уйдёт на TCP (всего %d)", ipToStr(dst), n)
		}
		return true
	}
	// (1) Initial с читаемым Discord-SNI → запоминаем IP и роняем.
	if sni, ok := quicInitialPeekSNI(pkt, meta); ok && isDiscordSNI(sni) {
		markDiscordQUICIP(dst)
		n := atomic.AddInt64(&discordQUICDropCnt, 1)
		if n <= 3 || n%100 == 0 {
			logStepf("quic", "Discord по QUIC уронен (sni=%s) → клиент уйдёт на TCP, где обход работает (всего %d)", sni, n)
		}
		return true
	}
	// (3) established short-header к ранее замеченному Discord-IP → тоже роняем.
	if isDiscordQUICIP(dst) {
		atomic.AddInt64(&discordQUICDropCnt, 1)
		return true
	}
	return false
}
