//go:build windows

package main

import "sync"

// Техники обхода DPI (Слой 5: перебор МАТРИЦЫ параметров, как blockcheck zapret).
// Вместо 4 фиксированных техник — комбинации: техника × позиция реза × TTL fake ×
// тип порчи fake. Это десятки вариантов; авто-перебор сам находит рабочий.

type strategyName string

const (
	stratSplit         strategyName = "split"
	stratDisorder      strategyName = "disorder"
	stratFake          strategyName = "fake"
	stratFakedDisorder strategyName = "fakeddisorder"
	stratMultisplit    strategyName = "multisplit"    // несколько позиций разреза
	stratMultidisorder strategyName = "multidisorder" // несколько позиций, обратный порядок
	stratOOB           strategyName = "oob"           // out-of-band (URG) фейк
	stratZapretSeqovl  strategyName = "zapret-seqovl"  // faithful zapret: part1@S, потом overlap=паттерн++rest
)

// переключатель экспериментального точного рецепта zapret для общего списка.
// Ставится один раз при старте (до цикла перехвата), поэтому обычный bool безопасен.
var generalZapretOn bool

func setGeneralZapret(v bool)     { generalZapretOn = v }
func generalZapretEnabled() bool { return generalZapretOn }

// ── IP-СЛОЙ обхода Discord (аналог ipset-all у zapret) ─────────────────────────
// Discord часть соединений открывает через Cloudflare с ECH (без видимого SNI).
// Движок по SNI их не видит → они шли мимо обхода и резались ТСПУ (чат/настройки
// не грузились). Решение: IP, опознанные как Discord по ВИДИМОМУ рукопожатию,
// запоминаем; их соединения БЕЗ SNI всё равно обходим рабочим рецептом.
var (
	discordIPset   = map[uint32]bool{}
	discordIPsetMu sync.Mutex
)

func markDiscordIP(ip uint32) {
	if ip == 0 {
		return
	}
	discordIPsetMu.Lock()
	if len(discordIPset) > 8192 {
		discordIPset = map[uint32]bool{}
	}
	discordIPset[ip] = true
	discordIPsetMu.Unlock()
}

func isDiscordIP(ip uint32) bool {
	discordIPsetMu.Lock()
	v := discordIPset[ip]
	discordIPsetMu.Unlock()
	return v
}

// isAnyDiscordSNI — любой домен Discord (com/gg/app/media) для маркировки IP.
func isAnyDiscordSNI(sni string) bool {
	return isDiscordSNI(sni) || isDiscordMediaSNI(sni) || isDiscordVoiceSNI(sni)
}

var (
	ipDiscLogMu sync.Mutex
	ipDiscLogN  int
)

func ipDiscordLogOnce() bool {
	ipDiscLogMu.Lock()
	ipDiscLogN++
	n := ipDiscLogN
	ipDiscLogMu.Unlock()
	return n <= 5 || n%100 == 0
}

var (
	quicDiscLogMu sync.Mutex
	quicDiscLogN  int
)

func quicDiscLogOnce() bool {
	quicDiscLogMu.Lock()
	quicDiscLogN++
	n := quicDiscLogN
	quicDiscLogMu.Unlock()
	return n <= 3 || n%200 == 0
}

// cutMode — как выбирать позицию разреза
type cutMode int

const (
	cutSNImid   cutMode = iota // середина SNI
	cutSNIstart                // прямо перед SNI
	cutFixed2                  // фиксировано на 2 байте
	cutMulti                   // несколько разрезов (multisplit)
)

// fakeCorrupt — чем «портить» fake-пакет, чтобы сервер его отбросил
type fakeCorrupt int

const (
	corruptChecksum fakeCorrupt = iota // битая TCP-сумма (badsum)
	corruptSeq                         // сдвинутый seq (badseq)
	corruptNone                        // только низкий TTL
	corruptNoAck                       // снять флаг ACK (datanoack): data без ACK невалидна
	corruptMD5                         // добавить TCP MD5-опцию (md5sig): сервер без MD5 отбросит
	corruptTS                          // испортить TCP timestamp (ts), если он есть
	corruptHopByHop                    // IPv6 Hop-by-Hop заголовок (hopbyhop, только IPv6)
)

// desyncParams — полный набор параметров одной попытки обхода
type desyncParams struct {
	strat    strategyName
	cut      cutMode
	ttl      byte        // TTL для fake
	corrupt  fakeCorrupt // чем портить fake
	fakes    int         // сколько fake-пакетов слать (старое, для совместимости)
	repeats  int         // СКОЛЬКО РАЗ повторять десинк-пакеты (zapret repeats=8-11)
	seqovl   int         // байт перекрытия sequence (zapret seqovl, против троттла потока)
	ipIDZero bool        // ставить IP ID = 0 на десинк-пакетах (zapret ip-id=zero)
	fakeTLS  bool        // использовать поддельный ClientHello как fake/паттерн
	decoy    decoyKind   // КАКОЙ ClientHello-декой для seqovl/fake (google или 4pda — как в zapret)
}

// decoyKind — выбор декой-ClientHello для seqovl-паттерна и fake (как у zapret:
// google для google-списка/discord.media, 4pda для общих сайтов вкл. discord.com).
type decoyKind int

const (
	decoyGoogle decoyKind = iota // www.google.com ClientHello (681 байт)
	decoy4pda                    // 4pda.to ClientHello (284 байта) — для общих доменов
)

// decoyBytes возвращает байты декой-ClientHello для техники (с фолбэком на google).
func decoyBytes(p desyncParams) []byte {
	if p.decoy == decoy4pda && len(fakeTLS4pda) > 0 {
		return fakeTLS4pda
	}
	if len(fakeTLSGoogle) > 0 {
		return fakeTLSGoogle
	}
	return fakeTLSPattern
}

// generalZapretRecipe — ТОЧНЫЙ рецепт zapret general.bat для list-general
// (discord.com, discord.gg/gateway, соцсети и пр.): multisplit split-pos=1
// split-seqovl=568 pattern=4pda. У нас stratSplit (in-order) + sendSeqovl даёт
// ту же механику перекрытия (проверенно доставляет данные, как на google), но с
// 4pda-паттерном и seqovl=568 — а НЕ google+681, из-за которого Discord проходил
// handshake, но поток душился. Без fake/ttl/ip-id (их в general-правиле zapret нет).
func generalZapretRecipe() desyncParams {
	// КЛЮЧЕВОЙ УРОК: голый seqovl ТСПУ гасит. Рабочая техника #0 пробивает за счёт
	// ВСЕЙ обвязки: фейки с битой контрольной суммой (corrupt), повторы (repeats=6),
	// обнуление ip_id, disorder-порядок. Поэтому берём ТОЧНО рабочий #0 и меняем
	// лишь то, что отличает рецепт zapret для общего списка: декой google→4pda и
	// seqovl 681→568. Всё остальное — как в проверенно-работающем #0.
	return desyncParams{
		strat:    stratDisorder,    // как #0 (рабочий порядок: part2, потом seqovl)
		cut:      cutSNImid,        // как #0
		ttl:      4,
		corrupt:  corruptChecksum,  // как #0 — фейк с битой суммой (DPI видит, сервер отбрасывает)
		fakes:    1,                // как #0
		repeats:  6,                // как #0
		seqovl:   568,              // zapret general
		ipIDZero: true,             // как #0
		fakeTLS:  true,
		decoy:    decoy4pda,        // 4pda.to — единственное смысловое отличие от #0
	}
}

// isDiscordMediaSNI — только discord.media (голос/медиа). Его TCP-путь у нас уже
// работает (#0), поэтому general-рецепт на него НЕ распространяем — не трогаем
// рабочий голос.
func isDiscordMediaSNI(sni string) bool {
	return sni == "discord.media" || hasSuffix(sni, ".discord.media")
}

func (p desyncParams) String() string {
	s := string(p.strat) + "/cut" + itoa(int(p.cut)) + "/ttl" + itoa(int(p.ttl)) +
		"/corr" + itoa(int(p.corrupt)) + "/f" + itoa(p.fakes)
	if p.repeats > 1 {
		s += "/rep" + itoa(p.repeats)
	}
	if p.seqovl > 0 {
		s += "/ovl" + itoa(p.seqovl)
	}
	if p.fakeTLS {
		s += "/ftls"
	}
	if p.ipIDZero {
		s += "/id0"
	}
	return s
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var b [12]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		b[i] = '-'
	}
	return string(b[i:])
}

// computeCut выбирает позицию разреза по режиму.
func computeCut(mode cutMode, info tlsInfo, payloadLen, fallback int) int {
	switch mode {
	case cutSNImid:
		if info.sniOffset > 0 && info.sniLength > 1 {
			return info.sniOffset + info.sniLength/2
		}
	case cutSNIstart:
		if info.sniOffset > 0 {
			return info.sniOffset
		}
	case cutFixed2:
		return 2
	case cutMulti:
		if info.sniOffset > 0 {
			return info.sniOffset + 1
		}
	}
	if fallback > 0 && fallback < payloadLen {
		return fallback
	}
	return payloadLen / 2
}

// addTCPMD5Option вставляет в fake TCP-опцию MD5. ВНИМАНИЕ: применять ТОЛЬКО к
// fake-пакетам (она сдвигает payload на 20 байт и меняет data offset; для
// реального сегмента это сломало бы поток). Сервер без настроенного MD5
// отбрасывает сегмент с этой опцией, а DPI её игнорирует. Возвращает новый
// сегмент (IPv4). nil при ошибке.
func addTCPMD5Option(pkt []byte, tcpOffset int) []byte {
	if tcpOffset+13 >= len(pkt) {
		return nil
	}
	dataOff := int(pkt[tcpOffset+12]>>4) * 4
	ins := tcpOffset + dataOff
	if ins > len(pkt) || dataOff+20 > 60 { // макс. размер TCP-заголовка 60 байт
		return nil
	}
	opt := make([]byte, 20)
	opt[0], opt[1] = 19, 18 // kind=19 (MD5), len=18
	// opt[2:18] — 16-байтный «дайджест» (мусорный), opt[18]=opt[19]=NOP(1)
	opt[18], opt[19] = 1, 1
	out := make([]byte, 0, len(pkt)+20)
	out = append(out, pkt[:ins]...)
	out = append(out, opt...)
	out = append(out, pkt[ins:]...)
	// новый data offset (в 32-битных словах)
	newDO := (dataOff + 20) / 4
	out[tcpOffset+12] = byte(newDO<<4) | (out[tcpOffset+12] & 0x0f)
	// обновить IP total length (IPv4)
	setIPTotalLen(out, len(out))
	return out
}

// clearACK снимает флаг ACK (datanoack): data-сегмент без ACK невалиден → сервер
// отбросит, DPI распарсит.
func clearACK(pkt []byte, tcpOffset int) {
	if tcpOffset+13 < len(pkt) {
		pkt[tcpOffset+13] &^= 0x10
	}
}

// corruptTimestamp ищет TCP-опцию timestamp (kind=8) и портит её значение.
// Если опции нет — ничего не делает (best-effort).
func corruptTimestamp(pkt []byte, tcpOffset int) {
	dataOff := int(pkt[tcpOffset+12]>>4) * 4
	i := tcpOffset + 20
	end := tcpOffset + dataOff
	for i+1 < end && i+1 < len(pkt) {
		kind := pkt[i]
		if kind == 0 { // EOL
			break
		}
		if kind == 1 { // NOP
			i++
			continue
		}
		if i+1 >= len(pkt) {
			break
		}
		olen := int(pkt[i+1])
		if olen < 2 {
			break
		}
		if kind == 8 && i+6 < len(pkt) { // timestamp: портим TSval
			pkt[i+2] ^= 0xFF
			return
		}
		i += olen
	}
}

// applyDesyncP обрабатывает исходящий пакет по параметрам p (только домены из hl).
func applyDesyncP(wd *winDivert, pkt []byte, addr *winDivertAddress, p desyncParams, fallbackCut int, hl *hostlist) bool {
	meta := parseIPv4TCP(pkt)
	if !meta.ok || meta.payloadLen <= 0 {
		return false
	}
	payload := pkt[meta.dataOffset:]
	info := parseTLSClientHello(payload)
	if !info.isClientHello {
		return false
	}
	// ДИАГНОСТИКА: видим ClientHello. Логируем факт + SNI + заблокирован ли (первые
	// 20 раз), чтобы по логу было ясно, ЧТО движок видит и решает.
	logClientHelloSeen(info.sni, hl.match(info.sni), info.fragmented)
	statCH(reduceToSLD(info.sni)) // диагностика: считаем ClientHello к домену (даже если ответа потом нет)
	// IP-СЛОЙ (аналог ipset-all у zapret): SNI не виден (ECH/пустой), но соединение
	// идёт к IP, опознанному как Discord по ВИДИМОМУ рукопожатию → всё равно обходим
	// рабочим рецептом. Это закрывает ECH-соединения Discord, из-за которых чат/
	// настройки/гейтвей не грузились (они шли мимо SNI-обхода и резались ТСПУ).
	ipLayerDiscord := info.sni == "" && isDiscordIP(ipv4DstIP(pkt))
	// KYBER (Chrome 124+): ClientHello разбит на 2+ TCP-сегмента. РАНЬШЕ тут был
	// баг — мы только впрыскивали fake-decoy и НЕ резали, поэтому SNI оставался
	// виден и ТСПУ резал (ERR_CONNECTION_RESET). zapret режет kyber-ClientHello
	// как обычно: это БЕЗОПАСНО, потому что split на УРОВНЕ TCP — сервер соберёт
	// исходные байты по TCP-пересборке. SNI лежит в ПЕРВОМ сегменте (раннее
	// расширение), поэтому режем тут же, по SNI. Только если SNI в этот сегмент
	// НЕ попал — впрыскиваем fake-decoy и пропускаем.
	if info.fragmented && info.sniOffset <= 0 && !ipLayerDiscord {
		hostHit := hl.match(info.sni)
		blocked := hostHit || (behaviorEnabled() && behavior.shouldDesync(info.sni, "", hostHit))
		if blocked && len(fakeTLSGoogle) > 0 {
			ft := byte(4)
			if autottlEnabled() {
				if t := suggestedFakeTTL(ipv4DstIP(pkt)); t > 0 {
					ft = t
				}
			}
			for r := 0; r < 2; r++ {
				if fake := buildSegment(pkt, meta, fakeTLSGoogle, meta.seq, uint16(0x5100+r)); fake != nil {
					setTTL(fake, ft)
					wd.sendRaw(fake, addr)
				}
			}
			if kyberLogOnce() {
				logStepf("kyber", "kyber ClientHello без SNI в сегменте: fake-decoy (реальный пакет не трогаю)")
			}
		}
		return false
	}
	// fragmented НО SNI здесь → режем как обычный ClientHello (ниже по коду).
	hostHit := hl.match(info.sni)
	if ipLayerDiscord {
		// ECH/без-SNI к Discord-IP: форсируем обход рабочим рецептом, минуя SNI-логику.
		noteSNI(info.sni)
		if behaviorEnabled() {
			behavior.onOutboundData(connKey(ipv4DstIP(pkt), meta.dstPort), info.sni, meta.seq, true)
		}
		p = techniqueByIndex(0) // disorder+seqovl681+google — проверенно доставляет данные на Discord
		if ipDiscordLogOnce() {
			logStepf("ip-discord", "ECH/без-SNI к Discord-IP %s → обход рабочим рецептом (аналог ipset-all zapret)", ipToStr(ipv4DstIP(pkt)))
		}
	} else {
		if behaviorEnabled() {
			// Вариант C: наблюдаем за ЭТИМ соединением (ретрансмиссии ClientHello =
			// сигнал блокировки). Решаем — обходить или нет.
			behavior.onOutboundData(connKey(ipv4DstIP(pkt), meta.dstPort), info.sni, meta.seq, true)
			if !behavior.shouldDesync(info.sni, "", hostHit) {
				// ВАЖНО: для ДОМЕНА ИЗ СПИСКА (точно заблокирован, напр. discord.com) НЕ
				// уходим в passthrough даже если движок «сдался» — passthrough известно-
				// заблокированного SNI = гарантированный блок (хуже, чем без движка). Лучше
				// продолжать обходить текущим рецептом. Passthrough только для НЕ-списочных.
				if !hostHit {
					noteSNI(info.sni) // запомним, что видели домен (для лога/обучения)
					return false      // не в списке и пока не доказана блокировка → не трогаем
				}
			}
		} else if !hostHit {
			return false
		}
		noteSNI(info.sni)
		// САМОПОДБОР: берём текущую технику для домена из цикла обратной связи.
		// Она меняется автоматически, если не пробила, и фиксируется, когда пошли
		// входящие данные. Никакого хардкода под конкретную сеть — движок сам ищет.
		curTech := 0
		if behaviorEnabled() {
			curTech = behavior.techIdxFor(info.sni)
			p = techniqueByIndex(curTech)
			// помечаем соединение: применили технику curTech — цикл измерит её успех
			behavior.markDesynced(connKey(ipv4DstIP(pkt), meta.dstPort), curTech)
		}
	}
	// Запоминаем ФАКТ: этот IP принадлежит этому домену (из рукопожатия). Это не
	// «список заблокированных» — решение рвать соединение принимается по вердикту
	// (vBlocked + обход не пробил). Так точечный сброс остаётся автоматическим.
	recordHostIP(ipv4DstIP(pkt), info.sni)
	// IP-СЛОЙ: запоминаем IP как Discord по ВИДИМОМУ рукопожатию (discord.com/gg/
	// media/app) — потом обойдём и его ECH/без-SNI соединения (см. ipLayerDiscord).
	if isAnyDiscordSNI(info.sni) {
		markDiscordIP(ipv4DstIP(pkt))
	}
	// если это видео-CDN — запоминаем СОЕДИНЕНИЕ (IP+порт), чтобы дробить
	// именно его поток (метод 1+4), а не весь трафик к общему Google-IP.
	if isVideoSNI(info.sni) {
		markVideoConn(ipv4DstIP(pkt), meta.dstPort)
	}
	// голосовая инфраструктура Discord → запоминаем IP для UDP-десинка голоса
	if isDiscordVoiceSNI(info.sni) {
		markVoiceIP(ipv4DstIP(pkt))
	}
	// апдейтер Discord капризный — для него может быть СВОЯ техника (если подобрана)
	if isUpdaterSNI(info.sni) {
		if up := getUpdaterParams(); up != nil {
			p = *up
		}
	}
	// ТОЧНЫЙ РЕЦЕПТ zapret для GOOGLE/YouTube (фаза google в general.bat):
	// split + seqovl=681 + ip-id=zero + реальный google-паттерн перекрытия.
	// ВАЖНО: именно stratSplit применяет sendSeqovl() (перекрытие google-
	// паттерном) — это ядро обхода YouTube. stratMultisplit его НЕ применял (был баг).
	// БЕЗ ХАРДКОДА СТРАТЕГИИ. Стратегию выбирает цикл самоподбора (techIdxFor).
	// Здесь только ПАРАМЕТР-подсказка: для google-доменов используем реальный
	// google ClientHello как fake-паттерн и ip-id=zero (это про СОДЕРЖИМОЕ фейка,
	// а не про технику). Какая техника сработает — движок найдёт сам по входящим.
	if isGoogleSNI(info.sni) || isVideoSNI(info.sni) {
		p.fakeTLS = true // google-паттерн (а не 4pda) — он уместнее для google
		p.ipIDZero = true
	} else if generalZapretEnabled() && hl.match(info.sni) && !isDiscordMediaSNI(info.sni) {
		// ЭКСПЕРИМЕНТ (--discord-zapret): общий список (discord.com, gateway.discord.gg)
		// на точном рецепте zapret general. По умолчанию ВЫКЛ — прошлая версия этого
		// рецепта давала silent-drop (неверный порядок сегментов); здесь порядок
		// исправлен (part1 на правильном seq, потом overlap). discord.media не трогаем.
		p = generalZapretRecipe()
	}

	// TLS-record фрагментация (если включена --tlsrec): рвём ClientHello на 2
	// TLS-записи внутри SNI. Отдельная дыра DPI (не собирает записи). Пробуем
	// первой; если не вышло (нет SNI и т.п.) — откат на обычный десинк ниже.
	if tlsrecEnabled() {
		if applyTLSRecordFrag(wd, pkt, addr, meta, info, p.ipIDZero) {
			logStepf("tlsrec", "ClientHello %q разбит на 2 TLS-записи внутри SNI", info.sni)
			return true
		}
	}

	cut := computeCut(p.cut, info, meta.payloadLen, fallbackCut)
	if cut <= 0 || cut >= meta.payloadLen {
		cut = meta.payloadLen / 2
	}
	cut = jitterCut(cut, meta.payloadLen) // рандомизация позиции (если --randomize)

	// Метод 3: IP-фрагментация ClientHello (если включена) — другой уровень,
	// DPI иначе реагирует. Эксперимент (фрагменты часто режут роутеры).
	if ipfragEnabled() {
		f1, f2 := ipFragmentTCP(pkt, meta, cut)
		if f1 != nil && f2 != nil {
			wd.sendRaw(f1, addr)
			wd.sendRaw(f2, addr)
			logStepf("ipfrag", "ClientHello %q разбит на 2 IP-фрагмента (cut=%d)", info.sni, cut)
			return true
		}
		logStep("ipfrag", "IP-фрагментация не удалась — откат на обычный десинк")
	}

	part1 := buildSegment(pkt, meta, payload[:cut], meta.seq, 0x2001)
	part2 := buildSegment(pkt, meta, payload[cut:], meta.seq+uint32(cut), 0x2002)
	if p.ipIDZero {
		setIPID(part1, 0)
		setIPID(part2, 0)
	}

	reps := p.repeats
	if reps < 1 {
		reps = 1
	}
	reps = scaleReps(reps) // A+B самолечение: при перегрузке канала repeats снижается
	// fake-пакеты: либо поддельный google-ClientHello (fakeTLS), либо копия начала.
	sendFakes := func() {
		n := p.fakes
		if n < 1 && (p.strat == stratFake || p.strat == stratFakedDisorder) {
			n = 1
		}
		// autottl: если по ответному RST вычислен TTL для этого IP — берём его.
		// ВАЖНО: пока точный TTL НЕ известен (первое соединение к домену), шлём fake
		// на НЕСКОЛЬКИХ TTL, чтобы покрыть ТСПУ на разном расстоянии (иначе fake с
		// одним TTL не долетит до ТСПУ, если он дальше → блок не обходится с первого
		// раза). Как только autottl узнал точный хоп — шлём только на нём.
		fakeTTLs := []byte{p.ttl}
		if autottlEnabled() {
			if t := suggestedFakeTTL(ipv4DstIP(pkt)); t > 0 {
				fakeTTLs = []byte{t} // точный TTL известен — бьём прицельно
			} else {
				fakeTTLs = []byte{4, 8, 11, 14, 17, 20} // неизвестно (или silent-drop без RST) — покрываем дальность ТСПУ от близкой до hop~20
			}
		}
		// когда TTL несколько (autottl ещё не знает дальность) — меньше повторов на
		// каждый, чтобы не раздувать число fake-пакетов (суммарно ~как при одном TTL)
		repsPer := reps
		if len(fakeTTLs) > 1 {
			repsPer = 2
		}
		for i := 0; i < n; i++ {
			var fakePayload []byte
			if p.fakeTLS {
				fakePayload = decoyBytes(p) // декой-ClientHello (google или 4pda по технике)
			} else {
				fakePayload = payload[:cut]
			}
			for _, fakeTTL := range fakeTTLs {
				for r := 0; r < repsPer; r++ { // repeats: шлём каждый fake reps раз
					fake := buildSegment(pkt, meta, fakePayload, meta.seq, uint16(0x1300+i*16+r))
					setTTL(fake, fakeTTL)
					if p.ipIDZero {
						setIPID(fake, 0)
					}
					switch p.corrupt {
					case corruptChecksum:
						corruptTCPChecksum(fake, meta.tcpOffset)
					case corruptSeq:
						setSeq(fake, meta.tcpOffset, meta.seq+99999)
					case corruptNoAck:
						clearACK(fake, meta.tcpOffset)
					case corruptMD5:
						if m := addTCPMD5Option(fake, meta.tcpOffset); m != nil {
							fake = m
						}
					case corruptTS:
						corruptTimestamp(fake, meta.tcpOffset)
					case corruptNone:
					}
					wd.sendRaw(fake, addr)
				}
			}
		}
	}

	// seqovl: перед part1 шлём сегмент с перекрытием sequence (zapret-техника
	// против троттлинга потока). Сегмент = паттерн(seqovl байт) + начало данных,
	// с seq, сдвинутым назад на seqovl. DPI обрабатывает паттерн и сбивается,
	// сервер берёт реальные данные по TCP-пересборке.
	sendSeqovl := func() {
		// ФИКС wrap: если seq соединения меньше перекрытия, вычитание ушло бы в
		// огромное число (uint32 wrap) → DPI получит мусорный seq. В таком случае
		// перекрытие пропускаем (шлём part1 как есть), это безопасно.
		if p.seqovl <= 0 || meta.seq < uint32(p.seqovl) {
			wd.send(part1, addr)
			return
		}
		// паттерн перекрытия: тайлим поддельный google-ClientHello до seqovl байт,
		// чтобы DPI видел в перекрытии google-данные (а не нули). Если fakeTLS off —
		// используем начало реального payload как паттерн.
		pat := make([]byte, p.seqovl)
		var src []byte
		if p.fakeTLS {
			src = decoyBytes(p)
		} else {
			src = payload[:cut]
		}
		if len(src) == 0 {
			src = []byte{0x16, 0x03, 0x01}
		}
		for i := 0; i < p.seqovl; i++ {
			pat[i] = src[i%len(src)]
		}
		// перекрывающий сегмент: seqovl байт паттерна + начало реальных данных,
		// с seq, сдвинутым назад на seqovl (zapret-механика).
		ovlData := append(append([]byte{}, pat...), payload[:cut]...)
		ovlSeg := buildSegment(pkt, meta, ovlData, meta.seq-uint32(p.seqovl), 0x2003)
		if p.ipIDZero {
			setIPID(ovlSeg, 0)
		}
		wd.send(ovlSeg, addr)
	}

	// видимость: какой рецепт авто-выбран для этого сервиса (раз на SNI)
	recipeName := string(p.strat)
	if isGoogleSNI(info.sni) {
		recipeName = "google/youtube (zapret-фаза google)"
	} else if isVideoSNI(info.sni) {
		recipeName = "видео-CDN"
	} else if isDiscordMediaSNI(info.sni) {
		recipeName = "discord-голос (media)"
	} else if hl.match(info.sni) {
		recipeName = "общий список zapret (split+seqovl568+4pda)"
	} else {
		recipeName = "вариант C (обнаружена блокировка)"
	}
	logRecipeOnce(info.sni, recipeName, p.seqovl)

	switch p.strat {
	case stratSplit:
		sendSeqovl()
		wd.send(part2, addr)
	case stratDisorder:
		wd.send(part2, addr)
		sendSeqovl()
	case stratFake:
		sendFakes()
		sendSeqovl()
		wd.send(part2, addr)
	case stratFakedDisorder:
		sendFakes()
		wd.send(part2, addr)
		sendSeqovl()
	case stratMultisplit:
		// zapret-general/google: multisplit С seqovl-декоем. Если seqovl задан —
		// шлём декой-оверлап (4pda/google ClientHello на низком seq, ТСПУ видит его
		// первым), иначе старое поведение с отдельным fake.
		if p.seqovl > 0 {
			sendSeqovl()
		} else {
			sendFakes()
		}
		sendMultiSplit(wd, pkt, meta, addr, payload, multiSplitPositions(info, meta.payloadLen), p.ipIDZero, false)
	case stratMultidisorder:
		if p.seqovl > 0 {
			sendSeqovl()
		} else {
			sendFakes()
		}
		sendMultiSplit(wd, pkt, meta, addr, payload, multiSplitPositions(info, meta.payloadLen), p.ipIDZero, true)
	case stratZapretSeqovl:
		// ТОЧНО по докам zapret (split + seqovl): seqovl-байты добавляются в НАЧАЛО
		// ПЕРВОГО сегмента со сдвигом seq в минус на seqovl. Т.е.:
		//   seg1 = pattern(seqovl) ++ payload[:cut]  на seq = S - seqovl
		//   seg2 = payload[cut:]                       на seq = S + cut
		// seg1 — «partially in-window»: ОС берёт только часть ≥ S (реальный payload
		// [:cut]), фейк ниже S отбрасывает, но DPI видит фейк (4pda) целиком. Затем
		// seg2 доставляет остаток. Сервер собирает исходный ClientHello.
		if p.seqovl > 0 && meta.seq >= uint32(p.seqovl) {
			src := decoyBytes(p)
			if len(src) == 0 {
				src = []byte{0x16, 0x03, 0x01}
			}
			pat := make([]byte, p.seqovl)
			for i := 0; i < p.seqovl; i++ {
				pat[i] = src[i%len(src)]
			}
			seg1 := buildSegment(pkt, meta, append(append([]byte{}, pat...), payload[:cut]...), meta.seq-uint32(p.seqovl), 0x2004)
			wd.send(seg1, addr)
			wd.send(part2, addr) // payload[cut:] на seq S+cut (part2 уже на этом seq)
		} else {
			wd.send(part1, addr)
			wd.send(part2, addr)
		}
	case stratOOB:
		ft := p.ttl
		if autottlEnabled() {
			if t := suggestedFakeTTL(ipv4DstIP(pkt)); t > 0 {
				ft = t
			}
		}
		sendOOBFake(wd, pkt, meta, addr, ft)
		sendSeqovl()
		wd.send(part2, addr)
	default:
		wd.send(part1, addr)
		wd.send(part2, addr)
	}
	return true
}

// multiSplitPositions вычисляет несколько позиций разреза: рвём вокруг SNI
// (перед именем и в середине имени), чтобы DPI не собрал SNI ни из одного
// сегмента. Без SNI — делим на ~3 равные части.
func multiSplitPositions(info tlsInfo, payloadLen int) []int {
	var pos []int
	if info.sniOffset > 4 && info.sniLength > 2 {
		p1 := info.sniOffset                    // перед значением SNI
		p2 := info.sniOffset + info.sniLength/2 // внутри SNI
		if p1 > 0 && p1 < payloadLen {
			pos = append(pos, p1)
		}
		if p2 > p1 && p2 < payloadLen {
			pos = append(pos, p2)
		}
	}
	if len(pos) == 0 { // фолбэк: трети
		a, b := payloadLen/3, 2*payloadLen/3
		if a > 0 {
			pos = append(pos, a)
		}
		if b > a && b < payloadLen {
			pos = append(pos, b)
		}
	}
	return pos
}

// sendMultiSplit режет payload по нескольким позициям и шлёт сегменты. disorder=true
// — в обратном порядке (последний сегмент первым), что ломает реассемблер DPI.
func sendMultiSplit(wd *winDivert, pkt []byte, meta ipv4tcp, addr *winDivertAddress, payload []byte, positions []int, ipIDZero, disorder bool) {
	bounds := append([]int{0}, positions...)
	bounds = append(bounds, len(payload))
	type seg struct {
		data []byte
		seq  uint32
	}
	var segs []seg
	for i := 0; i+1 < len(bounds); i++ {
		lo, hi := bounds[i], bounds[i+1]
		if lo >= hi {
			continue
		}
		segs = append(segs, seg{payload[lo:hi], meta.seq + uint32(lo)})
	}
	send := func(s seg, id uint16) {
		b := buildSegment(pkt, meta, s.data, s.seq, id)
		if ipIDZero {
			setIPID(b, 0)
		}
		wd.send(b, addr)
	}
	if disorder {
		for i := len(segs) - 1; i >= 0; i-- {
			send(segs[i], uint16(0x2600+i))
		}
	} else {
		for i := range segs {
			send(segs[i], uint16(0x2600+i))
		}
	}
}

// setURG ставит флаг URG и urgent pointer (для OOB-техники).
func setURG(pkt []byte, tcpOffset int, urgPtr uint16) {
	if tcpOffset+19 < len(pkt) {
		pkt[tcpOffset+13] |= 0x20 // URG
		pkt[tcpOffset+18] = byte(urgPtr >> 8)
		pkt[tcpOffset+19] = byte(urgPtr)
	}
}

// sendOOBFake шлёт out-of-band фейк: сегмент с URG-флагом и мусорным «срочным»
// байтом, низкий TTL. Сервер фейк отбросит (TTL/URG-мусор), а DPI, по-разному
// трактующий urgent-данные, рассинхронизируется. Безопасно (реальные данные целы).
func sendOOBFake(wd *winDivert, pkt []byte, meta ipv4tcp, addr *winDivertAddress, ttl byte) {
	junk := []byte{0x16, 0x03, 0x01, 0x00, 0x00} // похоже на начало TLS-записи
	fake := buildSegment(pkt, meta, junk, meta.seq, 0x2700)
	setTTL(fake, ttl)
	setURG(fake, meta.tcpOffset, uint16(len(junk)))
	wd.sendRaw(fake, addr)
}

// buildSegment собирает новый IP+TCP пакет с заданными данными, seq и IP ID.
func buildSegment(orig []byte, meta ipv4tcp, data []byte, seq uint32, ipid uint16) []byte {
	seg := make([]byte, meta.dataOffset+len(data))
	copy(seg, orig[:meta.dataOffset])
	copy(seg[meta.dataOffset:], data)
	setIPTotalLen(seg, len(seg))
	setIPID(seg, ipid)
	setSeq(seg, meta.tcpOffset, seq)
	return seg
}

// applyQUICDesync — fake-десинк для QUIC Initial (видео идёт по QUIC).
func applyQUICDesync(wd *winDivert, pkt []byte, addr *winDivertAddress, ttl byte) bool {
	meta := parseIPv4UDP(pkt)
	if !meta.ok || meta.payloadLen <= 0 {
		return false
	}
	payload := pkt[meta.dataOffset:]
	if !isQUICInitial(payload) {
		return false
	}
	// СКОРОСТЬ: не фейкуем QUIC к ЗАВЕДОМО РАБОЧИМ адресам — иначе замедляем рабочий
	// трафик лишними пакетами. Заблокированные и НЕИЗВЕСТНЫЕ адреса фейкуем (так
	// ловим новые блокировки вроде googlevideo, который идёт только по QUIC).
	// ИСКЛЮЧЕНИЕ — DISCORD: его TCP-путь «работает» (ОБХОД✓), поэтому оптимизация
	// пропускала фейк его QUIC. Но Discord грузит сообщения/настройки/контент по
	// QUIC (HTTP/3), и этот незафейканный QUIC ТСПУ режет/душит — отсюда «гейтвей
	// дотянулся, а сообщения не грузятся». zapret фейкует Discord-QUIC всегда. Делаем
	// так же: для Discord-IP фейк не пропускаем никогда.
	dstIP := ipv4DstIP(pkt)
	if behaviorEnabled() && !isDiscordIP(dstIP) {
		if host := hostForIP(dstIP); host != "" && behavior.isWorkingHost(host) {
			return false
		}
	}
	if isDiscordIP(dstIP) && quicDiscLogOnce() {
		logStepf("quic", "Discord-QUIC фейкуем (контент Discord идёт по HTTP/3; раньше пропускали как «рабочий хост»)")
	}
	// fake = РЕАЛЬНЫЙ захваченный QUIC Initial (google), валидным UDP-пакетом с
	// низким TTL, repeats раз. ТСПУ принимает как настоящий QUIC-хендшейк и
	// сбивается; до сервера фейк не дойдёт (TTL). Реальный пакет — как есть.
	// Безопасно: реальные данные не трогаем; ошибка сборки фейка → просто пропуск.
	binFake := fakeQUICGoogle
	if len(binFake) > 0 {
		reps := 6
		for r := 0; r < reps; r++ {
			if f := buildVoiceFake(pkt, meta, binFake, ttl); f != nil {
				wd.send(f, addr)
			}
		}
	} else {
		// фолбэк (нет bin): старый способ — копия с низким TTL и порчей начала
		fake := make([]byte, len(pkt))
		copy(fake, pkt)
		setTTL(fake, ttl)
		for i := meta.dataOffset + 1; i < meta.dataOffset+9 && i < len(fake); i++ {
			fake[i] ^= 0xFF
		}
		setIPID(fake, 0x3137)
		wd.sendRaw(fake, addr)
	}
	if err := wd.send(pkt, addr); err != nil {
		return false
	}
	// поймали свежий Initial к этому IP → обход пошёл, форс-дроп к нему больше не нужен
	markQUICAttempted(ipv4DstIP(pkt))
	return true
}

// активный fooling-режим (для мест, где нет desyncParams под рукой — напр. IPv6).
var activeFooling = corruptChecksum

func setFoolingMode(c fakeCorrupt) { activeFooling = c }
func foolingMode() fakeCorrupt     { return activeFooling }

// parseFooling переводит строку флага --fooling в режим порчи fake.
func parseFooling(s string) fakeCorrupt {
	switch s {
	case "badseq":
		return corruptSeq
	case "datanoack":
		return corruptNoAck
	case "md5sig":
		return corruptMD5
	case "ts":
		return corruptTS
	case "hopbyhop":
		return corruptHopByHop
	case "none":
		return corruptNone
	default: // badsum
		return corruptChecksum
	}
}

// логируем применяемый рецепт раз на SNI (видимость авто-классификации, без спама)
var (
	recipeLogged = map[string]bool{}
	recipeLogMu  sync.Mutex
)

func logRecipeOnce(sni, recipe string, seqovl int) {
	if sni == "" {
		return
	}
	recipeLogMu.Lock()
	defer recipeLogMu.Unlock()
	if recipeLogged[sni] {
		return
	}
	recipeLogged[sni] = true
	logStepf("техника", "%s → рецепт %q (seqovl=%d) — авто-выбор по сервису", sni, recipe, seqovl)
}

// logClientHelloSeen — диагностика: показываем КАЖДЫЙ видимый ClientHello (первые
// 20), его SNI и решение. Чтобы по логу было понятно, что движок реально видит.
var (
	chSeenCount int
	chSeenMu    sync.Mutex
)

func logClientHelloSeen(sni string, blocked, fragmented bool) {
	chSeenMu.Lock()
	defer chSeenMu.Unlock()
	chSeenCount++
	if chSeenCount > 20 && chSeenCount%50 != 0 {
		return
	}
	kind := "обычный"
	if fragmented {
		kind = "kyber/фрагмент"
	}
	verdict := "НЕ в списке (наблюдаю)"
	if blocked {
		verdict = "ЗАБЛОКИРОВАН → обхожу"
	}
	if sni == "" {
		sni = "(SNI пуст/ECH)"
	}
	logStepf("CH", "вижу ClientHello #%d: sni=%s [%s] — %s", chSeenCount, sni, kind, verdict)
}
