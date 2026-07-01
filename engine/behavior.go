package main

import (
	"fmt"
	"sort"
	"sync"
	"sync/atomic"
	"time"
)

// ── ВАРИАНТ C: поведенческое авто-обнаружение блокировки ───────────────────
//
// Идея (как просил пользователь): движок НАБЛЮДАЕТ за соединениями и сам решает.
//   - соединение работает штатно (данные идут)        → НЕ ТРОГАЕМ
//   - соединение заблокировано (RST/timeout/нет данных) → ВКЛЮЧАЕМ обход
// Это точечно: рабочее (банки, голос Discord, что и так открывается) не ломается
// — в отличие от zapret, который десинхронизирует всё подряд в фильтре и из-за
// этого ломал устройства ввода Discord. Здесь обход применяется только к тому,
// что реально не работает.
//
// Модель сигналов взята из ByeDPI(--auto)/zapret(autohostlist):
//   сигнал блокировки = N ретрансмиссий своего ClientHello БЕЗ ответа сервера,
//   ЛИБО входящий RST после ClientHello, ЛИБО нет данных за окно времени.
// Двухуровневый порог (retrans→fail) + скользящее окно — против ложных срабатываний.

type verdict int

const (
	vUnknown verdict = iota // ещё не знаем
	vWorking                // работает штатно — не трогаем
	vBlocked                // заблокировано — обходим
)

// behaviorCfg — пороги детекта (дефолты как у zapret autohostlist).
type behaviorCfg struct {
	retransThreshold int           // сколько ретрансмиссий ClientHello = 1 неудача (3)
	failThreshold    int           // сколько неудач до вердикта "заблокировано" (3)
	failWindow       time.Duration // скользящее окно для подсчёта неудач (60с)
	verdictTTL       time.Duration // сколько помним вердикт (часы)
	probeTimeout     time.Duration // нет данных за это время = подозрение на блок
}

func defaultBehaviorCfg() behaviorCfg {
	return behaviorCfg{
		retransThreshold: 3,
		failThreshold:    2,
		failWindow:       60 * time.Second,
		verdictTTL:       6 * time.Hour,
		probeTimeout:     5 * time.Second,
	}
}

// connObs — наблюдение за одним соединением (по 5-tuple через connKey).
type connObs struct {
	sni          string
	firstSeen    time.Time
	lastOut      time.Time // последний исходящий сегмент (для детекта ретрансмита)
	lastOutSeq   uint32    // seq последнего исходящего data-сегмента
	retransCount int       // подряд ретрансмиссий одного сегмента
	gotServerAck bool      // сервер прислал данные/ACK с данными (= прогресс)
	gotRST       bool      // пришёл RST
	clientHello  bool      // видели исходящий ClientHello
	desynced     bool      // к этому соединению применён обход
	techUsed     int       // какая техника применена (для измерения её успеха)
	ackedAt      time.Time // когда пришли первые данные (для детекта stateful-RST вскоре после)
	chAt         time.Time // когда увидели ClientHello (для детекта silent-drop: нет ответа)
	failCounted  bool      // провал этого соединения уже засчитан (анти-двойной-счёт)
}

// hostVerdict — агрегированный вердикт по домену/IP (живёт между соединениями).
type hostVerdict struct {
	v             verdict
	failCount     int
	failFirst     time.Time // начало текущего окна неудач
	decidedAt     time.Time
	persistent    bool      // загружен из autohostlist / задан вручную — не протухает
	techIdx       int       // какая техника обхода активна (эскалация при неудаче)
	escFails      int       // неудач уже ПОСЛЕ включения обхода (для эскалации)
	lastChange    time.Time // последняя смена вердикта/техники (гистерезис)
	giveUp        bool      // прошли все техники без успеха → локально не берётся (нужен туннель)
	techConfirmed bool      // текущая техника ПОДТВЕРЖДЕНА (пришли входящие данные на десинк-соединении)
	lastDataAt    time.Time // когда последний раз ЛЮБОЕ соединение к домену получило данные (защита от ложного silent-drop)
}

type behaviorState struct {
	cfg   behaviorCfg
	mu    sync.Mutex
	conns map[uint64]*connObs     // активные соединения (connKey → наблюдение)
	hosts map[string]*hostVerdict // вердикт per-SNI (или per-IP, если SNI нет)
}

var behavior = &behaviorState{
	cfg:   defaultBehaviorCfg(),
	conns: make(map[uint64]*connObs),
	hosts: make(map[string]*hostVerdict),
}

var behaviorOn = false // включается флагом --auto

func setBehaviorEnabled(on bool) { behaviorOn = on }
func behaviorEnabled() bool      { return behaviorOn }

// key для вердикта: SNI если есть, иначе строковый IP.
func verdictKey(sni, ipStr string) string {
	if sni != "" {
		return reduceToSLD(sni)
	}
	return ipStr
}

// reduceToSLD сводит хост к домену 2-го уровня (как nld=2 в zapret), чтобы
// поддомены одного сервиса делили один вердикт (rr1---sn-x.googlevideo.com →
// googlevideo.com).
func reduceToSLD(host string) string {
	n := len(host)
	dots := 0
	cut := 0
	for i := n - 1; i >= 0; i-- {
		if host[i] == '.' {
			dots++
			if dots == 2 {
				cut = i + 1
				break
			}
		}
	}
	if dots >= 2 {
		return host[cut:]
	}
	return host
}

// lookupVerdict — текущий вердикт по домену/IP (с учётом TTL).
func (b *behaviorState) lookupVerdict(key string) verdict {
	b.mu.Lock()
	defer b.mu.Unlock()
	hv, ok := b.hosts[key]
	if !ok {
		return vUnknown
	}
	if !hv.persistent && time.Since(hv.decidedAt) > b.cfg.verdictTTL && hv.v != vUnknown {
		delete(b.hosts, key)
		return vUnknown
	}
	return hv.v
}

// addPersistentBlocked помечает домен как заблокированный НАВСЕГДА (из autohostlist
// или заданный вручную). Используется при загрузке файла автосписка.
func (b *behaviorState) addPersistentBlocked(host string, techIdx int) {
	if host == "" {
		return
	}
	if techIdx < 0 || techIdx >= numTechniques() {
		techIdx = 0
	}
	key := reduceToSLD(host)
	b.mu.Lock()
	defer b.mu.Unlock()
	// техника из файла — СТАРТОВАЯ подсказка (в прошлый раз пробивала). НЕ помечаем
	// подтверждённой: если сеть изменилась и техника больше не пробивает,
	// confirmSurvivors/silent-drop переэскалируют. Если работает — подтвердится по
	// выживанию (быстро, т.к. стартуем сразу с рабочей, без перебора с #0).
	b.hosts[key] = &hostVerdict{v: vBlocked, decidedAt: time.Now(), persistent: true, techIdx: techIdx}
}

// markBlocked / markWorking — выставить вердикт.
func (b *behaviorState) setVerdict(key string, v verdict) {
	b.mu.Lock()
	defer b.mu.Unlock()
	hv := b.hosts[key]
	if hv == nil {
		hv = &hostVerdict{}
		b.hosts[key] = hv
	}
	hv.v = v
	hv.decidedAt = time.Now()
}

// onOutboundData — вызывается на исходящий data-сегмент (ClientHello/GET).
// Фильтр WinDivert исходящий, поэтому работаем по исходящим сигналам:
//   - тот же seq повторно = РЕТРАНСМИССИЯ (клиент пере-шлёт, т.к. ответа нет) → к блоку
//   - новый бОльший seq после ClientHello = ПРОГРЕСС (хендшейк пошёл) → работает
func (b *behaviorState) onOutboundData(key uint64, sni string, seq uint32, isClientHello bool) {
	b.mu.Lock()
	defer b.mu.Unlock()
	co := b.conns[key]
	if co == nil {
		co = &connObs{firstSeen: time.Now(), sni: sni}
		b.conns[key] = co
	}
	if co.sni == "" && sni != "" {
		co.sni = sni
	}
	if isClientHello {
		co.clientHello = true
		if co.chAt.IsZero() {
			co.chAt = time.Now()
		}
	}
	switch {
	case seq != 0 && co.lastOutSeq == seq:
		// ретрансмиссия того же сегмента
		co.retransCount++
		if co.retransCount >= b.cfg.retransThreshold && !co.gotServerAck {
			b.registerFailLocked(co)
		}
	case co.clientHello && co.lastOutSeq != 0 && seq > co.lastOutSeq:
		// прогресс: после ClientHello полетели новые данные. Само по себе это НЕ
		// доказывает «работает» (сайт мог ответить частично/медленно). Поэтому
		// здесь только сбрасываем счётчик ретрансмиссий, а вердикт «работает»
		// ставим лишь по ВХОДЯЩИМ данным (onInboundData) — это надёжнее.
		co.lastOutSeq = seq
		co.retransCount = 0
	default:
		co.lastOutSeq = seq
		co.retransCount = 0
	}
	co.lastOut = time.Now()
}

// onInboundRST — пришёл RST от сервера/ТСПУ после нашего ClientHello.
func (b *behaviorState) onInboundRST(key uint64) {
	b.mu.Lock()
	defer b.mu.Unlock()
	co := b.conns[key]
	if co == nil || !co.clientHello {
		return
	}
	co.gotRST = true
	if !co.gotServerAck {
		// RST ДО установления = классический SNI-блок на рукопожатии.
		statRST(reduceToSLD(co.sni))
		diagReport(co.sni, btSNIBlock)
		b.registerFailLocked(co)
		return
	}
	// RST ПОСЛЕ рукопожатия. Если техника ещё НЕ подтверждена выживанием (соединение
	// не прожило 4с) и RST пришёл вскоре после первых данных = STATEFUL-блок: ТСПУ
	// пропустил рукопожатие, увидел SNI и РВЁТ установленное соединение (Discord
	// «Жду ответа сервера», WebSocket gateway не поднимается). Текущая техника не
	// пробила по-настоящему → эскалируем на следующую (она может не дать ТСПУ
	// закрепить stateful-блок). Если техника УЖЕ подтверждена (соединение жило) —
	// это штатное закрытие/таймаут, не дёргаемся.
	if co.sni == "" || co.ackedAt.IsZero() {
		return
	}
	k := reduceToSLD(co.sni)
	hv := b.hosts[k]
	if hv != nil && hv.techConfirmed {
		return // техника уже доказала работу — RST штатный, рабочую не бросаем
	}
	if time.Since(co.ackedAt) < 10*time.Second {
		statRST(k)
		if statefulLogOnce() {
			logStepf("stateful", "%s: RST после рукопожатия до подтверждения = stateful-блок ТСПУ (как «Жду ответа сервера») → эскалирую технику", k)
		}
		diagReport(co.sni, btSNIBlock)
		b.registerFailLocked(co)
	}
}

// onInboundData — сервер прислал реальные данные = соединение работает.
// markDesynced — движок применил обход к соединению техникой techIdx. Запоминаем,
// чтобы измерить, сработала ли ИМЕННО эта техника (по входящим данным потом).
func (b *behaviorState) markDesynced(key uint64, techIdx int) {
	b.mu.Lock()
	defer b.mu.Unlock()
	co := b.conns[key]
	if co == nil {
		co = &connObs{firstSeen: time.Now()}
		b.conns[key] = co
	}
	co.desynced = true
	co.techUsed = techIdx
}

func (b *behaviorState) onInboundData(key uint64) {
	b.mu.Lock()
	defer b.mu.Unlock()
	co := b.conns[key]
	if co == nil {
		return
	}
	co.gotServerAck = true
	if co.ackedAt.IsZero() {
		co.ackedAt = time.Now()
	}
	if co.sni == "" {
		return
	}
	k := reduceToSLD(co.sni)
	hv := b.hosts[k]
	if co.desynced && hv == nil {
		// Обойдённое соединение ОТВЕТИЛО, но вердикта ещё нет (hostlist-домен, по
		// которому ещё не было провалов). Создаём vBlocked с ТЕКУЩЕЙ техникой —
		// confirmSurvivors подтвердит её по выживанию. БЕЗ этого был баг: silent-drop
		// другого (заброшенного) соединения ложно эскалировал рабочую технику, т.к.
		// hv был nil и защита lastDataAt не срабатывала. Это домен из hostlist —
		// он заблокирован, обход для него работает, фиксируем технику.
		hv = &hostVerdict{v: vBlocked, decidedAt: time.Now(), techIdx: co.techUsed}
		b.hosts[k] = hv
	}
	if hv != nil {
		hv.lastDataAt = time.Now() // домен ОТВЕЧАЕТ — техника для него работает
	}
	statData(k, 0) // диагностика: входящие данные
	// НЕ подтверждаем технику прямо тут по первому/нескольким пакетам: TLS-
	// рукопожатие сервера (ServerHello+Certificate) — это само по себе 3-4 пакета,
	// а stateful-DPI рвёт соединение УЖЕ ПОСЛЕ рукопожатия (Discord «Жду ответа
	// сервера»). Поэтому подтверждаем по ВЫЖИВАНИЮ во времени (см. confirmSurvivors
	// в тике): соединение прожило N секунд БЕЗ RST = техника реально пробила.
	// Здесь только фиксируем рабочее состояние незаблокированных доменов.
	if !co.desynced {
		diagReport(co.sni, btWorking)
		if hv == nil || hv.v == vUnknown {
			b.hosts[k] = &hostVerdict{v: vWorking, decidedAt: time.Now()}
		}
	}
}

// confirmSurvivors — подтверждает технику для соединений, которые ПРОЖИЛИ без RST
// достаточно долго после первых данных. Это надёжнее счётчика пакетов: stateful-
// DPI рвёт соединение вскоре после рукопожатия, поэтому «выжило N секунд» =
// техника действительно пробила, а не ложное срабатывание по ServerHello.
func (b *behaviorState) confirmSurvivors() {
	b.mu.Lock()
	defer b.mu.Unlock()
	now := time.Now()
	for _, co := range b.conns {
		if !co.desynced || co.sni == "" {
			continue
		}
		// SILENT-DROP: обойдённое соединение, на которое НЕ пришло НИ ответа, НИ RST
		// за 6с после ClientHello = ТСПУ молча ДРОПАЕТ (не шлёт RST, просто гасит
		// пакеты). Наш цикл эскалировал только по RST — а тут RST нет, и техника #0
		// застревала навсегда (Discord висит на «А вы знали?»). Засчитываем провал →
		// эскалируем на следующую технику. Считаем один раз на соединение.
		if co.ackedAt.IsZero() && !co.gotRST && !co.failCounted &&
			!co.chAt.IsZero() && now.Sub(co.chAt) > 6*time.Second {
			co.failCounted = true
			k := reduceToSLD(co.sni)
			hv := b.hosts[k]
			// защита: если домен НЕДАВНО отвечал (другое соединение получило данные)
			// — техника для него работает, это соединение просто заброшено приложением.
			// НЕ эскалируем (иначе ложно собьём рабочую технику, напр. на YouTube).
			if hv != nil && (hv.techConfirmed || (!hv.lastDataAt.IsZero() && now.Sub(hv.lastDataAt) < 15*time.Second)) {
				continue
			}
			statSilent(k)
			if silentLogOnce() {
				logStepf("silent", "%s: обойдено, но НЕТ ответа и НЕТ RST за 6с = ТСПУ молча дропает → эскалирую технику", k)
			}
			b.forceEscalate(co)
			continue
		}
		if co.gotRST || co.ackedAt.IsZero() {
			continue
		}
		if now.Sub(co.ackedAt) < 6*time.Second {
			continue // ещё рано — ждём, не рвёт ли ТСПУ stateful-RST
		}
		k := reduceToSLD(co.sni)
		hv := b.hosts[k]
		if hv != nil && hv.v == vBlocked && co.techUsed == hv.techIdx && !hv.techConfirmed {
			hv.techConfirmed = true
			hv.escFails = 0
			logStepf("auto", "✔ техника #%d (%s) ПОДТВЕРЖДЕНА для %s — соединение выжило без RST, фиксирую", hv.techIdx, techniqueName(hv.techIdx), k)
			go rememberTechnique(k, hv.techIdx) // запомнить рабочий рецепт (файл I/O вне лока)
		}
	}
}

// registerFailLocked — учесть одну неудачу в скользящем окне; при достижении
// failThreshold выставить вердикт "заблокировано". Вызывать под mu.
// forceEscalate — СРАЗУ переключить домен на следующую технику. Для silent-drop
// (ТСПУ молча дропает) это явный сигнал, что текущая техника не пробила — не ждём
// накопления неудач. Создаёт вердикт vBlocked, если его нет.
func (b *behaviorState) forceEscalate(co *connObs) {
	if co.sni == "" {
		return
	}
	key := reduceToSLD(co.sni)
	hv := b.hosts[key]
	if hv == nil {
		hv = &hostVerdict{v: vBlocked, decidedAt: time.Now(), lastChange: time.Now().Add(-time.Hour), techIdx: defaultTechFor(co.sni)}
		b.hosts[key] = hv
	}
	if hv.v != vBlocked {
		hv.v = vBlocked
		hv.decidedAt = time.Now()
	}
	if hv.techConfirmed || hv.giveUp {
		return
	}
	if time.Since(hv.lastChange) < 3*time.Second {
		return // не дёргаем технику слишком часто
	}
	next := hv.techIdx + 1
	hv.lastChange = time.Now()
	if next >= numEscalationTechniques() {
		hv.giveUp = true
		diagReport(co.sni, btNoResponse)
		logStepf("auto", "обход для %s не пробил НИ ОДНОЙ техникой (silent-drop) → похоже на IP-блок, нужен туннель", key)
		return
	}
	hv.techIdx = next
	hv.techConfirmed = false
	statEsc(key)
	logStepf("auto", "silent-drop для %s → переключаю на технику #%d (%s)", key, hv.techIdx, techniqueName(hv.techIdx))
}

func (b *behaviorState) registerFailLocked(co *connObs) {
	key := co.sni
	if key == "" {
		return
	}
	key = reduceToSLD(key)
	hv := b.hosts[key]
	if hv == nil {
		hv = &hostVerdict{techIdx: defaultTechFor(co.sni)} // старт с рецепта сервиса, не #0
		b.hosts[key] = hv
	}
	now := time.Now()
	if hv.failFirst.IsZero() || now.Sub(hv.failFirst) > b.cfg.failWindow {
		hv.failFirst = now
		hv.failCount = 0
	}
	hv.failCount++
	if hv.v == vBlocked {
		// Обход УЖЕ включён, но снова неудача.
		// Если техника уже ПОДТВЕРЖДЕНА (работала) — не дёргаемся: это случайный
		// сбой/таймаут, рабочую технику не бросаем.
		if hv.techConfirmed {
			return
		}
		// Техника НЕ подтверждена и снова неудача → она не пробила. Эскалируем:
		// пробуем следующую (как circular у zapret).
		hv.escFails++
		if hv.escFails >= 2 && time.Since(hv.lastChange) > 3*time.Second {
			next := hv.techIdx + 1
			hv.escFails = 0
			hv.lastChange = now
			if next >= numEscalationTechniques() {
				// Прошли ВЕСЬ круг техник, ни одна не пробила. Скорее всего это
				// НЕ SNI-блок, а IP-блок/недоступность — локальный обход бессилен,
				// нужен туннель. Прекращаем долбить, помечаем диагнозом.
				if !hv.giveUp {
					hv.giveUp = true
					diagReport(co.sni, btNoResponse)
					logStepf("auto", "обход для %s не пробил НИ ОДНОЙ техникой (%d/%d) → похоже на IP-блок, локально не берётся, нужен туннель. Перестаю перебирать.", key, numEscalationTechniques(), numEscalationTechniques())
				}
			} else {
				hv.techIdx = next
				hv.techConfirmed = false // новая техника — заново измеряем
				statEsc(key)
				logStepf("auto", "обход для %s техникой не пробил → переключаю на #%d (%s)", key, hv.techIdx, techniqueName(hv.techIdx))
			}
		}
		return
	}
	if hv.failCount >= b.cfg.failThreshold {
		hv.v = vBlocked
		hv.decidedAt = now
		hv.lastChange = now
		if co.gotRST {
			diagReport(co.sni, btSNIBlock)
		} else {
			diagReport(co.sni, btNoResponse) // ретрансмиты без RST = дроп/IP-блок
		}
		logStepf("auto", "вердикт: %s ЗАБЛОКИРОВАН (неудач=%d) → включаю обход (техника #0)", key, hv.failCount)
		go appendBlockedHost(key) // сохранить в автосписок (вне блокировки, файл I/O)
	}
}

// techIdxFor — индекс активной техники обхода для домена (для эскалации).
// defaultTechFor — СТАРТОВАЯ техника для домена по «рецептам zapret». Discord
// (discord.com/gateway/discordapp) обходится рецептом ОБЩИХ доменов (#1, seqovl=568
// +4pda), discord.media — медиа-рецептом (#3, 681+google), остальное (YouTube/google)
// — проверенным #0. Это даёт правильный СТАРТ без перебора; если не сработает —
// эскалация подберёт другую.
func defaultTechFor(sni string) int {
	// ОТКАТ: автоперебор по логу пользователя показал, что #0 (disorder+seqovl681+
	// google) РАБОТАЕТ для discord.com (данные пошли), а multisplit-рецепты #1-#4 —
	// нет (0 данных). Стартуем ВСЕХ с проверенной #0; если не пробьёт — эскалация
	// подберёт. Никаких per-service «угадайка»-рецептов, пока не доказаны.
	return 0
}

func (b *behaviorState) techIdxFor(sni string) int {
	if sni == "" {
		return 0
	}
	if cycleMatch(reduceToSLD(sni)) { // режим перебора: форсим текущую перебираемую технику
		return cycleTech()
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	if hv := b.hosts[reduceToSLD(sni)]; hv != nil {
		return hv.techIdx
	}
	return defaultTechFor(sni)
}

// shouldDesyncBehavior — главное решение варианта C: применять ли обход к этому
// соединению. true = обходить (заблокировано или подозрительно), false = не трогать.
// hostlistHit — попал ли домен в обычный список (тогда обходим в любом случае).
// hostNeedsReset — нужно ли рвать установленные соединения к этому домену.
// ДА только если движок САМ доказал блокировку (vBlocked) И обход ещё НЕ
// подтверждён (techConfirmed=false) И мы не сдались (giveUp). Если обход уже
// работает (techConfirmed) или домен рабочий — НЕ рвём (не ломаем успешное).
// blockedDomains — список доменов (SLD), которые движок САМ признал
// заблокированными. Используется, чтобы проактивно резолвить их IP через DNS и
// рвать УСТАНОВЛЕННЫЕ соединения к ним (даже те, чьё рукопожатие мы не видели).
func (b *behaviorState) blockedDomains() []string {
	b.mu.Lock()
	defer b.mu.Unlock()
	out := make([]string, 0, 8)
	for k, hv := range b.hosts {
		if hv.v == vBlocked && !hv.giveUp {
			out = append(out, k)
		}
	}
	return out
}

// isWorkingHost — домен подтверждён рабочим (vWorking)? Используется, чтобы НЕ
// фейковать QUIC к рабочим адресам (не замедлять рабочий трафик).
func (b *behaviorState) isWorkingHost(sld string) bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	hv := b.hosts[sld]
	return hv != nil && hv.v == vWorking
}

func (b *behaviorState) hostNeedsReset(sld string) bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	hv := b.hosts[sld]
	if hv == nil {
		return false
	}
	if hv.v != vBlocked || hv.techConfirmed || hv.giveUp {
		return false
	}
	// ЗАЩИТА: если домен НЕДАВНО получал данные (за 10с) — он РАБОТАЕТ, даже если
	// техника формально не «подтверждена» (например, gateway Discord: WebSocket
	// получает данные, но соединения короткоживущие и не успевают подтвердиться по
	// выживанию). Рвать такой домен НЕЛЬЗЯ — это убивает рабочий WebSocket и Discord
	// застревает на загрузке. Раньше резет бил по нему вслепую.
	if !hv.lastDataAt.IsZero() && time.Since(hv.lastDataAt) < 10*time.Second {
		return false
	}
	// GRACE: не рвём сразу после вердикта «заблокирован». Даём обходу 12с сработать
	// на НОВЫХ рукопожатиях. Рвём только если за это время ни данных, ни подтверждения.
	return time.Since(hv.decidedAt) > 12*time.Second
}

func (b *behaviorState) shouldDesync(sni, ipStr string, hostlistHit bool) bool {
	key := verdictKey(sni, ipStr)
	// Если по домену уже сдались (прошли все техники без успеха) — это IP-блок,
	// локально не берётся. Не тратим силы (нужен туннель). hostlist не спасёт.
	b.mu.Lock()
	if hv := b.hosts[key]; hv != nil && hv.giveUp {
		b.mu.Unlock()
		return false
	}
	b.mu.Unlock()
	if hostlistHit {
		return true // явный список приоритетнее
	}
	switch b.lookupVerdict(key) {
	case vBlocked:
		return true
	case vWorking:
		return false
	default:
		// неизвестно: на первом ClientHello НЕ трогаем (наблюдаем). Десинк
		// включится, когда накопятся признаки блокировки. Это и есть «не ломать
		// рабочее»: пока не доказана блокировка — пропускаем как есть.
		return false
	}
}

// dumpDiagnosis — выводит в лог текущую картину: какие домены движок счёл
// заблокированными/рабочими. Чтобы и пользователю, и нам было понятно, что
// происходит (вызывается периодически из тикера).
func (b *behaviorState) dumpDiagnosis() {
	// 1) собираем срез вердиктов ПОД b.mu, быстро отпускаем (не держим лок на время лога)
	type vrow struct {
		dom       string
		v         verdict
		techIdx   int
		confirmed bool
		giveUp    bool
	}
	b.mu.Lock()
	rows := make([]vrow, 0, len(b.hosts))
	var blocked, working, gaveup int
	for k, hv := range b.hosts {
		rows = append(rows, vrow{k, hv.v, hv.techIdx, hv.techConfirmed, hv.giveUp})
		switch {
		case hv.v == vBlocked && hv.giveUp:
			gaveup++
		case hv.v == vBlocked:
			blocked++
		case hv.v == vWorking:
			working++
		}
	}
	b.mu.Unlock()
	if len(rows) == 0 {
		return
	}
	// 2) берём снимок статистики (отдельный лок)
	st := snapshotStats()
	// карта домен→вердикт для быстрого доступа при рендере
	vmap := make(map[string]vrow, len(rows))
	for _, r := range rows {
		vmap[r.dom] = r
	}

	statusOf := func(r vrow) string {
		switch {
		case r.v == vBlocked && r.giveUp:
			return "СДАЛСЯ⛔"
		case r.v == vBlocked && r.confirmed:
			return "ОБХОД✓"
		case r.v == vBlocked:
			return "ОБХОД…"
		case r.v == vWorking:
			return "ЧИСТО"
		default:
			return "набл."
		}
	}

	logStep("дашборд", "════════════ СИМБИОНТ: КАРТА ОБХОДА ════════════")
	logStepf("дашборд", "домены: %d  (обходится=%d, работает=%d, сдался=%d)", len(rows), blocked, working, gaveup)
	logStep("дашборд", "ДОМЕН                       СТАТУС    ТЕХНИКА                 CH  DATA  RST SLNT RST-св  ЛАТ")

	// сортируем по «интересности» статистики; домены без статистики — в конец
	keys := sortedStatKeys(st)
	seen := map[string]bool{}
	shown := 0
	emit := func(dom string) {
		if seen[dom] || shown >= 18 {
			return
		}
		seen[dom] = true
		shown++
		r := vmap[dom]
		s := st[dom]
		tech := "—"
		if r.v == vBlocked || r.confirmed {
			tech = techniqueName(r.techIdx)
		}
		if len(tech) > 22 {
			tech = tech[:22]
		}
		lat := "—"
		if !s.firstData.IsZero() && !s.firstSeen.IsZero() {
			ms := s.firstData.Sub(s.firstSeen).Milliseconds()
			if ms >= 0 {
				lat = fmt.Sprintf("%dмс", ms)
			}
		}
		d := dom
		if len(d) > 26 {
			d = d[:26]
		}
		logStepf("дашборд", "%-27s %-9s %-23s %3d %5d %4d %4d %5d  %s",
			d, statusOf(r), tech, s.ch, s.data, s.rst, s.silent, s.resets, lat)
	}
	for _, k := range keys {
		emit(k)
	}
	// домены, у которых есть вердикт, но нет статистики (например, persistent из файла)
	for _, r := range rows {
		emit(r.dom)
	}

	// 3) ASCII-график объёма входящих данных (топ по data)
	maxData := 0
	dataKeys := make([]string, 0, len(st))
	for k, s := range st {
		dataKeys = append(dataKeys, k)
		if s.data > maxData {
			maxData = s.data
		}
	}
	sort.Slice(dataKeys, func(i, j int) bool { return st[dataKeys[i]].data > st[dataKeys[j]].data })
	if maxData > 0 {
		logStep("дашборд", "── ОБЪЁМ ВХОДЯЩИХ ДАННЫХ (пакетов) ──")
		for i, k := range dataKeys {
			if i >= 8 {
				break
			}
			s := st[k]
			d := k
			if len(d) > 22 {
				d = d[:22]
			}
			if s.data == 0 {
				logStepf("дашборд", "%-23s (нет ответа — возможно блок)", d)
			} else {
				logStepf("дашборд", "%-23s %s %d", d, asciiBar(s.data, maxData, 28), s.data)
			}
		}
	}

	// 4) общие метрики пакетов
	logStepf("дашборд", "ПАКЕТЫ: всего=%d TCP443=%d QUIC=%d прочее=%d | ClientHello=%d | вх.RST=%d вх.данные=%d QUIC-fake=%d",
		atomic.LoadInt64(&cntTotal), atomic.LoadInt64(&cntTCP443), atomic.LoadInt64(&cntUDP443),
		atomic.LoadInt64(&cntOther), atomic.LoadInt64(&cntCH), atomic.LoadInt64(&cntInRST),
		atomic.LoadInt64(&cntInData), atomic.LoadInt64(&cntQUICin))
	logStep("дашборд", "════════════════════════════════════════════════")
}

// purgeOld — периодическая чистка старых соединений (вызывать из тикера).
func (b *behaviorState) purgeOld() {
	b.mu.Lock()
	defer b.mu.Unlock()
	cutoff := time.Now().Add(-2 * time.Minute)
	for k, co := range b.conns {
		if co.lastOut.Before(cutoff) && co.firstSeen.Before(cutoff) {
			delete(b.conns, k)
		}
	}
}
