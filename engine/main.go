//go:build windows

package main

// symbiont-engine — локальный DPI-обход через WinDivert.
// Перехватывает исходящий TLS-трафик (порт 443), находит ClientHello,
// рвёт SNI техникой split, чтобы DPI не распознал имя сайта.
// Ловит ВЕСЬ трафик системы (браузеры, приложения, игры) — не только браузер.

import (
	"flag"
	"os"
	"os/signal"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

// глобальные счётчики для живой сводки (atomic — читаются из фоновой горутины)
var (
	cntTotal      int64
	cntTCP443     int64
	cntUDP443     int64
	cntOther      int64
	cntCH         int64
	cntQUIC       int64
	cntQUICin     int64 // перехваченных QUIC Initial (рукопожатий) — для сводки
	cntQUICForced int64 // дропнуто established QUIC для форса переподключения
	ipv6Desync    bool  // трогать ли IPv6 (по умолч нет — у многих он без интернета)
	seenSNI       = map[string]bool{}
	seenSNIMu     sync.Mutex
)

// joinUnique склеивает части фильтра, убирая дубликаты (например, UDP/443
// могут добавить и QUIC, и голос — оставляем одну).
func joinUnique(parts []string, sep string) string {
	seen := map[string]bool{}
	var uniq []string
	for _, p := range parts {
		if !seen[p] {
			seen[p] = true
			uniq = append(uniq, p)
		}
	}
	out := ""
	for i, p := range uniq {
		if i > 0 {
			out += sep
		}
		out += p
	}
	return out
}

// discordOnlyMode — режим теста только Discord (фокус логов/обработки)
var discordOnlyMode = false

func noteSNI(sni string) {
	if sni == "" {
		return
	}
	// в режиме только-Discord не засоряем лог посторонними доменами
	if discordOnlyMode {
		l := strings.ToLower(sni)
		if !strings.Contains(l, "discord") {
			return
		}
	}
	seenSNIMu.Lock()
	defer seenSNIMu.Unlock()
	if len(seenSNI) > 4000 { // кэш для дедупликации лога — сбрасываем при разрастании
		seenSNI = map[string]bool{}
	}
	if !seenSNI[sni] {
		seenSNI[sni] = true
		logStepf("sni", "НОВЫЙ домен в трафике: %s", sni)
	}
}

func main() {
	var (
		logPath    = flag.String("log", "", "путь к лог-файлу (Симбионт его читает)")
		strat      = flag.String("strategy", "disorder", "стратегия: split | disorder | fake | fakeddisorder (disorder мягкая, по умолч)")
		auto       = flag.Bool("auto", false, "СТАРЫЙ авто-перебор техник пробером. По умолч ВЫКЛ: используется эталон zapret сразу + вариант C (умнее)")
		reprobe    = flag.Bool("reprobe", false, "игнорировать сохранённый профиль и подобрать технику заново")
		dropQUIC   = flag.Bool("drop-quic", false, "глушить QUIC/UDP443 (старый режим). Лучше --quic=desync")
		quicMode   = flag.String("quic", "desync", "QUIC: desync (фейк google-бином, как zapret — видео по QUIC; реальный пакет проходит) | pass | drop. По умолч desync")
		dropDiscQ  = flag.Bool("drop-discord-quic", false, "ронять QUIC к Discord-IP (форс TCP). По умолч ВЫКЛ: тестами доказано, что Discord виснет НЕ из-за QUIC, а глобальный дроп ломает видео YouTube (googlevideo работает только по QUIC)")
		discZapret = flag.Bool("discord-zapret", true, "Discord (discord.com/discord.gg и общий список) на рецепте zapret general = рабочий #0 + декой 4pda + seqovl 568. По умолчанию ВКЛ. Отключить: --discord-zapret=false (вернёт #0+google).")
		streamF    = flag.Bool("stream", false, "метод 1+4: дробить ПОТОК видео-CDN. ВЫКЛ по умолч (включит авто-перебор, если поможет видео)")
		streamN    = flag.Int("stream-parts", 3, "на сколько частей дробить сегмент потока (метод 4)")
		ipfragF    = flag.Bool("ipfrag", false, "метод 3: фрагментация на уровне IP (эксперимент)")
		tlsrecF    = flag.Bool("tlsrec", false, "TLS-record фрагментация: рвать ClientHello на 2 TLS-записи внутри SNI (дыра DPI, CCS2023)")
		voiceF     = flag.Bool("voice", true, "обход ГОЛОСА Discord (UDP 50000-65535 + discord.media)")
		onlyDisc   = flag.Bool("only-discord", false, "режим теста: обрабатывать и логировать ТОЛЬКО Discord")
		ports      = flag.String("ports", "443,2053,2083,2087,2096,8443", "TCP-порты через запятую. По умолч — набор zapret (443 + Discord media/приложения 2053,2083,2087,2096,8443)")
		splitPos   = flag.Int("split-pos", 2, "запасная позиция разреза, если SNI не найден")
		hostlistF  = flag.String("hostlist", "", "файл со списком доменов для обхода (пусто = встроенный список заблокированных)")
		autoDetect = flag.Bool("auto-detect", true, "вариант C: сам обнаруживать заблокированные домены по поведению (ретрансмиссии) и обходить только их, не трогая рабочее")
		watchInb   = flag.Bool("watch-inbound", true, "ловить ВХОДЯЩИЕ ответы (RST/данные сервера) — даёт детект блокировки по RST и autottl")
		autottlF   = flag.Bool("autottl", true, "вычислять TTL фейка по TTL ответного RST (где стоит ТСПУ)")
		diagnoseF  = flag.Bool("diagnose", true, "диагностика: активные ИЗОЛИРОВАННЫЕ пробы (TCP vs TLS) + классификация типа блокировки. Безопасно (твой трафик не трогает), по умолч ВКЛ")
		randomizeF = flag.Bool("randomize", false, "рандомизировать позицию разреза (против статистического детекта). ОСТОРОЖНО: может ослабить обход, по умолч ВЫКЛ")
		quicSplitF = flag.Bool("quic-split", false, "QUIC: резать CRYPTO-фреймы заблокированных (эксперимент для видео). По умолч ВЫКЛ — чтобы QUIC-сайты как YouTube не ломались")
		resetConns = flag.Bool("reset-conns", true, "точечно разрывать УЖЕ УСТАНОВЛЕННЫЕ соединения к заблокированным сервисам (Discord/Telegram переподключатся через обход). Рабочее не трогает")
		ipv6F      = flag.Bool("ipv6", false, "обходить IPv6 (по умолч ВЫКЛ — у многих IPv6 'подключён без интернета', и вмешательство мешает fallback на IPv4)")
		quicForceF = flag.Bool("quic-force", false, "форсировать переподключение established QUIC к заблокированным (по умолч ВЫКЛ — может передёргивать рабочие соединения и замедлять; включай если нужен обход без перезапуска приложений)")
		foolingF   = flag.String("fooling", "badsum", "чем портить fake: badsum | badseq | datanoack | md5sig | ts | none")
		mtprotoF   = flag.Bool("mtproto", true, "обход MTProto (Telegram-десктоп): рвать сигнатуру на соединениях к дата-центрам Telegram. ВКЛ")
		cycleF     = flag.String("cycle", "", "ДИАГНОСТИКА: автоперебор всех техник на домене (напр. --cycle=discord.com). Покажет в логе, какая техника пробивает.")
	)
	flag.Parse()

	setBehaviorEnabled(*autoDetect)
	setAutoTTLEnabled(*autottlF)
	setDiagnoseEnabled(*diagnoseF)
	setRandomizeEnabled(*randomizeF)
	setQUICSplitEnabled(*quicSplitF)
	setMTProtoEnabled(*mtprotoF)
	if *cycleF != "" {
		cycleTarget = strings.ToLower(strings.TrimSpace(*cycleF))
		logStepf("cycle", "РЕЖИМ АВТОПЕРЕБОРА включён для «%s» — техники будут перебираться по очереди (обычная логика для этого домена отключена)", cycleTarget)
	}
	setResetEnabled(*resetConns)
	ipv6Desync = *ipv6F
	setQUICForceEnabled(*quicForceF)
	watchInbound := *watchInb

	initLog(*logPath)
	defer closeLog()
	logStep("start", "symbiont-engine запущен")
	logStepf("start", "стратегия=%s порты=%s split-pos=%d", *strat, *ports, *splitPos)
	if *autoDetect {
		logStep("auto", "вариант C включён: обхожу только то, что реально заблокировано (по ретрансмиссиям), рабочее не трогаю")
		loadBlockedHosts() // подхватить ранее обнаруженные/заданные вручную заблокированные домены
	}

	var hl *hostlist
	if *onlyDisc {
		hl = newDiscordHostlist()
		discordOnlyMode = true
	} else {
		hl = newHostlist(*hostlistF)
	}
	setStreamEnabled(*streamF)
	setStreamParts(*streamN)
	setIPFragEnabled(*ipfragF)
	setTLSRecEnabled(*tlsrecF)
	setVoiceEnabled(*voiceF)
	logStepf("start", "stream-десинк видео=%v (частей=%d), ipfrag=%v, голос Discord=%v", streamEnabled(), getStreamParts(), ipfragEnabled(), voiceEnabled())

	// строим фильтр WinDivert: исходящий TCP на заданные порты (для split)
	// + исходящий UDP на 443 (QUIC/HTTP3) — его будем ГЛУШИТЬ, чтобы браузер
	// откатился на TCP, где работает наша фрагментация (так делает и zapret).
	var conds []string
	var condsIn []string // для входящих: порт ИСТОЧНИКА = наш целевой
	for _, p := range strings.Split(*ports, ",") {
		p = strings.TrimSpace(p)
		if p != "" {
			conds = append(conds, "tcp.DstPort == "+p)
			condsIn = append(condsIn, "tcp.SrcPort == "+p)
		}
	}
	// нормализуем режим QUIC (старый --drop-quic = drop)
	qmode := *quicMode
	if *dropQUIC {
		qmode = "drop"
	}
	setActiveQUIC(qmode) // атомарно — авто-перебор сможет поменять
	setDiscordQUICDrop(*dropDiscQ)
	if *dropDiscQ {
		logStep("quic", "включён точечный дроп Discord-QUIC по IP (--drop-discord-quic)")
	}
	setGeneralZapret(*discZapret)
	if *discZapret {
		logStep("start", "Discord/общий список на рецепте zapret general (рабочий #0 + декой 4pda + seqovl568). Отключить: --discord-zapret=false")
	}
	tcpPart := "(tcp and (" + strings.Join(conds, " or ") + "))"
	udpParts := []string{}
	if qmode != "pass" {
		udpParts = append(udpParts, "(udp and udp.DstPort == 443)")
	}
	if voiceEnabled() {
		// голос Discord: UDP 443 (discord.media) + диапазон 50000-65535
		udpParts = append(udpParts, "(udp and udp.DstPort >= 50000 and udp.DstPort <= 65535)")
		udpParts = append(udpParts, "(udp and udp.DstPort == 443)")
	}
	var filter string
	if len(udpParts) == 0 {
		filter = "outbound and " + tcpPart
	} else {
		filter = "outbound and (" + tcpPart + " or " + joinUnique(udpParts, " or ") + ")"
	}
	// ВХОДЯЩИЕ: захватываем ответы от целевых портов (RST/данные сервера) —
	// это даёт варианту C детект блокировки по RST и autottl по TTL ответа.
	// Входящие только наблюдаем и переотправляем без изменений.
	if watchInbound && len(condsIn) > 0 {
		tcpPartIn := "(tcp and (" + strings.Join(condsIn, " or ") + "))"
		filter = "(" + filter + ") or (inbound and " + tcpPartIn + ")"
	}
	filterBase := filter // базовый фильтр (для фолбэка, если Telegram-диапазоны не примутся)
	// Telegram DC на ЛЮБОМ порту (десктоп ходит не только по 443): добавляем
	// перехват по IP-диапазонам Telegram, иначе MTProto-обход не применяется.
	if *mtprotoF {
		if tg := telegramFilterClause(); tg != "" {
			filter = "(" + filter + ") or (outbound and tcp and (" + tg + "))"
		}
	}
	logStepf("start", "режим QUIC (старт): %s", qmode)

	wd, err := newWinDivert()
	if err != nil {
		logStepf("fatal", "%v", err)
		os.Exit(1)
	}
	if err := wd.open(filter); err != nil {
		// фолбэк: возможно WinDivert не принял Telegram-диапазоны в фильтре —
		// пробуем БЕЗ них (Telegram-обход на не-443 портах не сработает, но движок
		// запустится и всё остальное будет работать). Лучше частично, чем никак.
		logStepf("warn", "фильтр не открылся (%v) — пробую без Telegram-диапазонов", err)
		filter2 := filterBase
		if err2 := wd.open(filter2); err2 != nil {
			logStepf("fatal", "%v", err2)
			os.Exit(2)
		}
		logStep("start", "запущен на базовом фильтре (без Telegram all-port)")
	}
	defer wd.close()

	// аккуратное завершение по Ctrl+C / сигналу от Симбионта
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	go func() {
		<-stop
		logStep("stop", "получен сигнал остановки")
		wd.close()
		closeLog()
		os.Exit(0)
	}()

	// начальная стратегия: по умолчанию ЭТАЛОН zapret (techniqueByIndex(0):
	// fake+seqovl681+ip-id=zero — то, что реально пробивает). Если пользователь
	// ЯВНО задал --strategy/--fooling (не дефолт) — уважаем его выбор.
	base := techniqueByIndex(0)
	if *strat != "disorder" {
		base.strat = strategyName(*strat)
	}
	if *foolingF != "badsum" {
		base.corrupt = parseFooling(*foolingF)
	}
	setActiveParams(base)
	setFoolingMode(base.corrupt)
	buf := make([]byte, 65535)
	var pktCount, chCount, desyncCount, quicDropped int
	var voiceCount int
	var quicInitials, quicData, quicSeen int // диагностика QUIC

	// Модель работы:
	// • По умолчанию (--auto выкл): СРАЗУ применяем эталон zapret (techniqueByIndex(0):
	//   fake+seqovl681+ip-id=zero) к известному заблокированному, а вариант C
	//   эскалирует технику, если не пробило. Старый профиль НЕ грузим (он мог
	//   быть от прежней версии и слабее).
	// • Если ЯВНО задан --auto: работает старый пробер-перебор (legacy).
	if *auto {
		if savedP, savedQ, savedStream, savedFrag, savedParts, ok := loadProfile(); ok && !*reprobe {
			setActiveParams(savedP)
			setActiveQUIC(savedQ)
			setStreamEnabled(savedStream)
			setIPFragEnabled(savedFrag)
			if savedParts > 0 {
				setStreamParts(savedParts)
			}
			logStepf("start", "использую сохранённую технику: %s, QUIC=%s (новый подбор: --reprobe)", savedP.String(), savedQ)
		} else {
			go func() {
				time.Sleep(800 * time.Millisecond)
				autoProbe(2500 * time.Millisecond)
				logStepf("probe", "активные параметры теперь: %s, QUIC=%s", getActiveParams().String(), getActiveQUIC())
			}()
		}
	} else {
		logStepf("start", "режим: эталон zapret сразу (%s) + вариант C для неизвестного. QUIC=%s", getActiveParams().String(), getActiveQUIC())
	}

	// фоновая сводка трафика каждые 5 секунд — видно, ЧТО реально идёт
	go func() {
		// СРАЗУ при старте (через 1.5с, чтобы WinDivert уже ловил пакеты): находим и
		// рвём стартовые соединения процесса Discord по PID. Discord обычно уже
		// запущен до движка и держит соединения на «мёртвом» пути (гейтвей не
		// достреливает READY → не грузятся сообщения/настройки). После разрыва он
		// переподключается, и новые рукопожатия идут через обход — как если бы движок
		// стартовал раньше Discord (как служба zapret). Захват стартового набора тут
		// же фиксируется, поэтому свежие соединения потом не дёргаем.
		if behaviorEnabled() {
			time.Sleep(1500 * time.Millisecond)
			resetBlockedConnections()
		}
		t := time.NewTicker(5 * time.Second)
		defer t.Stop()
		tick := 0
		for range t.C {
			tick++
			healTick() // A+B: адаптивно регулируем интенсивность по нагрузке канала
			if behaviorEnabled() {
				behavior.purgeOld()
				behavior.confirmSurvivors() // подтверждаем технику по выживанию соединения (без RST)
				cycleTick()                 // автоперебор техник (если включён --cycle)
				if tick%6 == 0 {            // раз в ~30с — диагноз (что движок понял про ТСПУ)
					behavior.dumpDiagnosis()
				}
			}
			// точечный разрыв установленных соединений к заблокированным сервисам
			// (раз в ~10с): Discord/Telegram переподключатся через обход. Рабочее
			// не трогаем (только IP, достоверно связанные с блоком).
			if tick%2 == 0 { // резет каждые ~10с — быстрее передёргиваем застрявшие соединения
				resetBlockedConnections()
				purgeQUICForce()
			}
			if tick%4 == 0 { // реже (~20с) — тяжёлые операции
				refreshBlockedIPs() // проактивно резолвим IP заблокированных доменов
				purgeKilledConns()
			}
			logStepf("сводка", "всего=%d | TCP/443=%d | UDP/443(QUIC)=%d | прочее=%d | ClientHello=%d | QUIC-заглушено=%d | вх.RST=%d вх.данные=%d",
				atomic.LoadInt64(&cntTotal), atomic.LoadInt64(&cntTCP443),
				atomic.LoadInt64(&cntUDP443), atomic.LoadInt64(&cntOther),
				atomic.LoadInt64(&cntCH), atomic.LoadInt64(&cntQUIC),
				atomic.LoadInt64(&cntInRST), atomic.LoadInt64(&cntInData))
			// ЧЕЛОВЕКОЧИТАЕМЫЙ вывод — простым языком, что происходит
			ch := atomic.LoadInt64(&cntCH)
			qi := atomic.LoadInt64(&cntQUICin)
			udp := atomic.LoadInt64(&cntUDP443)
			switch {
			case ch == 0 && qi == 0 && udp > 100:
				logStep("вывод", "трафик идёт по QUIC, рукопожатий не видно. ВЕРОЯТНО ПРИЧИНА: IPv6 'подключён без интернета' — браузер шлёт по IPv6 в никуда. РЕШЕНИЕ: отключи IPv6 в Windows (свойства адаптера → снять 'IP версии 6'), тогда всё пойдёт по IPv4 через обход.")
			case ch == 0 && qi > 0:
				logStepf("вывод", "QUIC-рукопожатия ловлю (%d) и фейкую как zapret. TCP-рукопожатий нет — это норма, сайты идут по QUIC.", qi)
			case ch > 0:
				logStepf("вывод", "вижу TCP-рукопожатия (%d) — обход применяется. Ищи строки [CH] и [auto ✔ подтверждена].", ch)
			default:
				logStep("вывод", "пока тихо — открой YouTube/Discord, чтобы пошли новые соединения.")
			}
		}
	}()

	processPacket := func(pkt []byte, addr *winDivertAddress) {
		// БЕЗОПАСНОСТЬ: при любой панике обработки — отправляем пакет КАК ЕСТЬ.
		// Это гарантирует, что интернет не прервётся из-за ошибки в десинке.
		defer func() {
			if r := recover(); r != nil {
				logStepf("recover", "паника при обработке пакета: %v — отправляю как есть (инет не рвём)", r)
				_ = wd.send(pkt, addr)
			}
		}()

		// классификация протокола для сводки
		if len(pkt) >= 10 {
			switch pkt[9] {
			case 6:
				atomic.AddInt64(&cntTCP443, 1)
			case 17:
				atomic.AddInt64(&cntUDP443, 1)
			default:
				atomic.AddInt64(&cntOther, 1)
			}
		}

		// первые 5 пакетов — подробно логируем (диагностика: доходит ли трафик)
		if pktCount <= 5 {
			proto := "?"
			if len(pkt) >= 10 {
				switch pkt[9] {
				case 6:
					proto = "TCP"
				case 17:
					proto = "UDP"
				}
			}
			logStepf("diag", "пакет #%d: %d байт, протокол=%s (исходящий по фильтру)", pktCount, len(pkt), proto)
		}

		// Фильтр ловит исходящие (на целевые порты) И, если включён
		// --watch-inbound, входящие ответы от этих портов. Бит направления
		// addr.outbound() надёжно их различает.

		// ВХОДЯЩИЕ ответы (если включён --watch-inbound): только наблюдаем
		// (RST = блок, данные = работает, TTL = autottl) и переотправляем БЕЗ
		// изменений. Петель нет: WinDivert не перехватывает свои инъекции.
		if watchInbound && !addr.outbound() {
			if len(pkt) >= 1 && pkt[0]>>4 == 6 {
				handleInbound6(pkt)
			} else {
				handleInbound(pkt)
			}
			err := wd.send(pkt, addr)
			noteSendResult(err)
			if err != nil {
				logStepf("loop", "переотправка входящего не удалась: %v", err)
			}
			return
		}

		// Остальное — ИСХОДЯЩИЕ пакеты по фильтру.

		// IPv6 (исходящий): безопасный десинк через fake-decoy. Реальный IPv6-пакет
		// ВСЕГДА отправляется как есть — связность не сломается. Для UDP/QUIC по
		// IPv6 — просто проброс (десинк IPv6-UDP пока не делаем).
		// IPv6: у многих провайдеров (и у тебя) IPv6 "подключён, но без интернета".
		// Тогда браузерные IPv6-попытки и так дохнут, а наше вмешательство лишь
		// мешает браузеру свалиться на рабочий IPv4. Поэтому по умолчанию IPv6 НЕ
		// трогаем — пропускаем как есть. Обход IPv6 включается флагом --ipv6.
		if len(pkt) >= 40 && pkt[0]>>4 == 6 {
			if ipv6Desync {
				if pkt[6] == 6 {
					if applyDesync6(wd, pkt, addr, hl) {
						atomic.AddInt64(&cntCH, 1)
						return
					}
				} else if pkt[6] == 17 {
					if um6 := parseIPv6UDP(pkt); um6.ok && um6.dstPort == 443 {
						if quicSplitEnabled() && applyQUICSplit(wd, pkt, addr, um6, hl) {
							return
						}
						if getActiveQUIC() == "desync" && applyQUICDesync6(wd, pkt, addr, 4) {
							atomic.AddInt64(&cntQUICin, 1)
							return
						}
					}
				}
			}
			err := wd.send(pkt, addr)
			noteSendResult(err)
			return
		}

		// UDP-пакет (IPv4)
		if len(pkt) >= 10 && pkt[9] == 17 {
			meta := parseIPv4UDP(pkt)
			// ГОЛОС Discord (UDP 50000-65535 или discord.media) — десинк голоса.
			if voiceEnabled() && applyVoiceDesync(wd, pkt, addr) {
				voiceCount++
				if voiceCount <= 5 || voiceCount%200 == 0 {
					logStepf("voice", "десинк голосового UDP Discord (всего %d)", voiceCount)
				}
				return
			}
			// QUIC frame splitting (если включён): для ЗАБЛОКИРОВАННЫХ доменов
			// режем ClientHello на 2 CRYPTO-фрейма. Работает даже в режиме pass —
			// обычные QUIC-сайты не трогаем (split только заблокированных), а при
			// любой ошибке исходный пакет уходит как есть. Крипто сверено с RFC 9001.
			if quicSplitEnabled() && meta.dstPort == 443 {
				if applyQUICSplit(wd, pkt, addr, meta, hl) {
					return
				}
			}
			// ДИАГНОСТИКА QUIC: для первых пакетов UDP/443 показываем, ЧТО это —
			// Initial (рукопожатие, можно обойти) или данные established-соединения
			// (рукопожатие уже было, обойти нельзя). Это снимает догадки.
			if meta.ok && meta.dstPort == 443 && meta.payloadLen > 0 {
				quicSeen++
				if quicSeen <= 15 {
					pl := pkt[meta.dataOffset:]
					b0 := pl[0]
					form := "short-header (established, рукопожатие УЖЕ было)"
					verStr := ""
					if b0&0x80 != 0 && len(pl) >= 5 {
						ver := uint32(pl[1])<<24 | uint32(pl[2])<<16 | uint32(pl[3])<<8 | uint32(pl[4])
						tp := (b0 & 0x30) >> 4
						form = "long-header тип=" + itoaB(tp)
						verStr = " ver=0x" + hex32(ver)
					}
					logStepf("quic-diag", "UDP/443 #%d: байт0=0x%02x%s [%s] Initial=%v", quicSeen, b0, verStr, form, isQUICInitial(pl))
				}
			}
			// Discord по QUIC → принудительно роняем (точечно по SNI/IP), чтобы
			// клиент ушёл на TCP, где обход работает. Это in-engine версия теста
			// «блок UDP/443 в фаерволе → Discord ожил». YouTube/прочий QUIC не трогаем.
			if dropDiscordQUIC(pkt, meta) {
				quicDropped++
				atomic.AddInt64(&cntQUIC, 1)
				return
			}
			// QUIC (UDP 443): поведение по текущему режиму.
			switch getActiveQUIC() {
			case "drop":
				quicDropped++
				atomic.AddInt64(&cntQUIC, 1)
				if quicDropped <= 3 || quicDropped%50 == 0 {
					logStepf("quic", "заглушен QUIC UDP/443 (всего %d)", quicDropped)
				}
				return
			case "desync":
				if applyQUICDesync(wd, pkt, addr, 3) {
					quicInitials++
					atomic.AddInt64(&cntQUICin, 1)
					if quicInitials <= 8 || quicInitials%100 == 0 {
						logStepf("quic", "✔ QUIC Initial #%d перехвачен → впрыснул фейк google-бином (как zapret), реальный пакет пропущен", quicInitials)
					}
					return
				}
				quicData++ // UDP/443 НЕ Initial (данные established QUIC — рукопожатие уже было)
				// ФОРС переподключения: если это established QUIC к достоверно
				// заблокированному IP, к которому мы ещё НЕ видели свежего Initial
				// (значит соединение жило до старта движка) — дропаем, чтобы браузер
				// открыл новое соединение. Новый Initial поймаем и обойдём. Само-
				// ограничено: после перехвата Initial к этому IP дроп прекращается.
				if shouldForceQUICReconnect(ipv4DstIP(pkt)) {
					qf := atomic.AddInt64(&cntQUICForced, 1)
					if qf <= 3 || qf%200 == 0 {
						logStepf("quic-force", "дроп established QUIC к %s (жило до движка) → браузер переоткроет соединение через обход", ipToStr(ipv4DstIP(pkt)))
					}
					return // дропаем (не отправляем) — форсируем переподключение
				}
				wd.send(pkt, addr)
				return
			default: // pass
				wd.send(pkt, addr)
				return
			}
		}

		// Метод 1+4: десинк ПОТОКА для видео-IP (дробим крупные сегменты данных).
		// Только если включён stream-режим.
		if streamEnabled() && applyStreamDesync(wd, pkt, addr) {
			return
		}

		// MTProto (Telegram-десктоп): если пакет к IP дата-центра Telegram и это
		// не TLS — рвём сигнатуру MTProto. Делаем ДО TLS-десинка (у MTProto нет SNI).
		{
			m := parseIPv4TCP(pkt)
			if m.ok && m.payloadLen > 0 {
				info := parseTLSClientHello(pkt[m.dataOffset:])
				if !info.isClientHello && applyMTProtoDesync(wd, pkt, m, addr) {
					return
				}
			} else if m.ok && mtprotoEnabled() && isTelegramDC(ipv4DstIP(pkt)) {
				// пакет к Telegram DC БЕЗ payload (обычно SYN). Логируем — если видим
				// только такие и НЕТ data-пакетов, значит соединение виснет на TCP-
				// рукопожатии (SYN уходит, но ответа нет) = ТСПУ блокирует Telegram на
				// уровне IP/SYN, локальный десинк (он по data) тут бессилен — нужен туннель.
				if tgDiagLogOnce() {
					logStepf("mtproto-diag", "пакет к Telegram DC %s порт=%d БЕЗ данных (SYN/handshake, payloadLen=0)", ipToStr(ipv4DstIP(pkt)), m.dstPort)
				}
				if noteTelegramSYNOnly() {
					logStep("telegram", "ВЕРДИКТ: к дата-центрам Telegram идут ТОЛЬКО SYN — ни SYN-ACK, ни data-пакетов. Это блок Telegram ПО IP дата-центров, а не по DPI-сигнатуре. Локальный десинк бессилен (рвать нечего: handshake не завершается, данные не отправляются). Решение: MTProxy в самом Telegram (Настройки → Продвинутые → Тип соединения → Использовать прокси → MTProto) либо туннель только под Telegram. Без удалённой точки это не лечится — физика.")
				}
			}
		}

		// Выбор параметров: во время авто-перебора тестовые параметры применяем
		// ТОЛЬКО к цели пробы (youtube/googlevideo пробера). ТВОЙ остальной трафик
		// идёт на стабильные live-параметры — браузер не ломается во время перебора.
		params := getActiveParams()
		if isProbing() {
			dst := ipv4DstIP(pkt)
			if dst == getProbeTarget() {
				params = getProbeParams() // только пробное соединение тестируем
			}
			// иначе — твой трафик на стабильных live-параметрах (не трогаем перебором)
		}

		handled := applyDesyncP(wd, pkt, addr, params, *splitPos, hl)
		if handled {
			chCount++
			atomic.AddInt64(&cntCH, 1)
			desyncCount++
			if chCount%10 == 0 {
				logStepf("stat", "перехвачено=%d, ClientHello=%d, QUIC-заглушено=%d", pktCount, chCount, quicDropped)
			}
			return
		}
		// не наш случай — отправляем как есть
		err := wd.send(pkt, addr)
		noteSendResult(err)
		if err != nil {
			logStepf("loop", "переотправка пакета не удалась: %v", err)
		}
	}

	logStep("loop", "вхожу в цикл перехвата пакетов")
	// активная диагностика (если --diagnose): изолированные пробы ключевых доменов
	if diagnoseEnabled() {
		runActiveDiagnosis([]string{"www.youtube.com", "discord.com", "discord.media", "updates.discord.com"})
	}
	for {
		n, addr, err := wd.recv(buf)
		if err != nil {
			logStepf("loop", "ошибка чтения пакета: %v (продолжаю)", err)
			continue
		}
		pktCount++
		atomic.AddInt64(&cntTotal, 1)
		pkt := make([]byte, n)
		copy(pkt, buf[:n])
		processPacket(pkt, addr)
	}
}

func itoaB(b byte) string { return string(rune('0' + b)) }

func hex32(v uint32) string {
	const h = "0123456789abcdef"
	out := make([]byte, 8)
	for i := 0; i < 8; i++ {
		out[7-i] = h[v&0xf]
		v >>= 4
	}
	return string(out)
}
