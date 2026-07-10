//go:build windows

package main

// Авто-перебор МАТРИЦЫ параметров (Слой 5). Движок сам прогоняет десятки
// комбинаций (техника × позиция реза × TTL × тип порчи fake) через РЕАЛЬНУЮ
// пробу передачи данных к странице И видео-CDN, и сам выбирает рабочую.

import (
	"context"
	"crypto/tls"
	"net"
	"sync"
	"sync/atomic"
	"time"
)

// активные параметры обхода, которые читает цикл перехвата
var activeParams atomic.Value // хранит desyncParams (ЖИВОЙ трафик)
var activeQUIC atomic.Value   // хранит string (drop/desync/pass)

// --- изоляция пробы от живого трафика ---
// Во время авто-перебора движок НЕ должен ломать твой браузер тестовыми
// параметрами. Поэтому: живой трафик идёт на liveParams (стабильные/безопасные),
// а тестовые параметры применяются ТОЛЬКО к соединениям пробера.
var (
	probeParams atomic.Value // desyncParams, которые сейчас ТЕСТируются
	probeActive int32        // 1 = идёт проба (atomic)
	probeDstIP  uint32       // IP цели пробы (только к нему применяем probeParams)
	probeDstMu  sync.RWMutex
)

func setProbing(on bool) {
	if on {
		atomic.StoreInt32(&probeActive, 1)
	} else {
		atomic.StoreInt32(&probeActive, 0)
	}
}
func isProbing() bool { return atomic.LoadInt32(&probeActive) == 1 }

func setProbeTarget(ip uint32) {
	probeDstMu.Lock()
	probeDstIP = ip
	probeDstMu.Unlock()
}
func getProbeTarget() uint32 {
	probeDstMu.RLock()
	v := probeDstIP
	probeDstMu.RUnlock()
	return v
}
func setProbeParams(p desyncParams) { probeParams.Store(p) }
func getProbeParams() desyncParams {
	v := probeParams.Load()
	if v == nil {
		return getActiveParams()
	}
	return v.(desyncParams)
}

func getActiveParams() desyncParams {
	v := activeParams.Load()
	if v == nil {
		return techniqueByIndex(0) // эталон zapret: fake+seqovl681+ip-id=zero
	}
	return v.(desyncParams)
}
func setActiveParams(p desyncParams) { activeParams.Store(p) }

func getActiveQUIC() string {
	v := activeQUIC.Load()
	if v == nil {
		return "desync"
	}
	return v.(string)
}
func setActiveQUIC(m string) { activeQUIC.Store(m) }

// updaterParams — отдельная техника специально для апдейтера Discord
// (updates.discord.com), если основная его не пробивает. nil = использовать обычную.
var updaterParams atomic.Value // *desyncParams

func setUpdaterParams(p *desyncParams) {
	if p == nil {
		updaterParams.Store((*desyncParams)(nil))
	} else {
		updaterParams.Store(p)
	}
}
func getUpdaterParams() *desyncParams {
	v := updaterParams.Load()
	if v == nil {
		return nil
	}
	return v.(*desyncParams)
}

const probeHost = "www.youtube.com:443"                    // САЙТ (заблокированный)
const probeApp = "gateway.discord.gg:443"                  // ПРИЛОЖЕНИЕ (Discord идёт сюда)
const probeVideo = "rr1---sn-q4fl6n66.googlevideo.com:443" // ВИДЕО-CDN
const probeUpdater = "updates.discord.com:443"             // АПДЕЙТЕР Discord (виснет)

// pageProbeTarget — что использовать как «сайт» для подбора техники.
// В режиме только-Discord проверяем по discord.com, иначе по youtube.
func pageProbeTarget() string {
	if discordOnlyMode {
		return "discord.com:443"
	}
	return probeHost
}

// probeSiteAndApp: техника рабочая, если пробивает И сайт (YouTube), И
// приложение (Discord). Они идут по-разному, поэтому проверяем оба.
func probeSiteAndApp(timeout time.Duration) (site, app bool) {
	site = probeData(pageProbeTarget(), timeout)
	app = probeData(probeApp, timeout)
	return
}

// buildMatrix строит набор комбинаций для перебора — от вероятных к экзотике.
// estimateBestTTL (метод 2) быстро ищет TTL, при котором fake-десинк начинает
// пробивать страницу. Это косвенно = расстояние до ТСПУ. Пробуем TTL по
// порядку вероятности (DPI обычно в 3-6 хопах), возвращаем первый рабочий.
func estimateBestTTL(timeout time.Duration) byte {
	logStep("probe", "метод 2: проверяю мягкий disorder и подбираю TTL...")
	// Сперва пробуем МЯГКИЙ disorder (не использует TTL, не ломает).
	setProbeParams(desyncParams{strat: stratDisorder, cut: cutSNImid, ttl: 4, corrupt: corruptNone, fakes: 0})
	if probeData(pageProbeTarget(), timeout) {
		logStep("probe", "метод 2: disorder уже пробивает — TTL не критичен, беру 4")
		return 4
	}
	// disorder не хватило — ищем TTL для fake-техник (TTL=1,2 пропускаем, ломают).
	for _, ttl := range []byte{4, 3, 5, 6, 7} {
		setProbeParams(desyncParams{strat: stratFake, cut: cutSNImid, ttl: ttl, corrupt: corruptChecksum, fakes: 1})
		if probeData(pageProbeTarget(), timeout) {
			logStepf("probe", "метод 2: рабочий TTL=%d для fake — ставлю первым", ttl)
			return ttl
		}
	}
	logStep("probe", "метод 2: явный TTL не выделился, беру 4")
	return 4
}

// buildUpdaterMatrix — техники специально для апдейтера Discord, от мягких к
// сильным. Апдейтер капризный, ему может подойти не то, что основному трафику.
func buildUpdaterMatrix(bestTTL byte) []desyncParams {
	return []desyncParams{
		{strat: stratDisorder, cut: cutSNIstart, ttl: bestTTL, corrupt: corruptNone},
		{strat: stratSplit, cut: cutSNImid, ttl: bestTTL, corrupt: corruptNone},
		{strat: stratFake, cut: cutSNIstart, ttl: bestTTL, corrupt: corruptChecksum, fakes: 1, repeats: 6, fakeTLS: true, ipIDZero: true},
		{strat: stratFakedDisorder, cut: cutSNImid, ttl: bestTTL, corrupt: corruptChecksum, fakes: 1, repeats: 8, fakeTLS: true, ipIDZero: true},
		{strat: stratFake, cut: cutSNImid, ttl: bestTTL, corrupt: corruptSeq, fakes: 1, repeats: 8, seqovl: 568, fakeTLS: true, ipIDZero: true},
	}
}

// buildVideoMatrix — МОЩНЫЕ техники против троттлинга видео, скопированные из
// рабочих конфигов zapret (seqovl + repeats + поддельный google-ClientHello +
// ip-id=zero). Это то, чего раньше не было и что реально пробивает googlevideo.
func buildVideoMatrix(bestTTL byte) []desyncParams {
	var m []desyncParams
	// сочетания, проверенные сообществом для googlevideo:
	seqovls := []int{681, 726, 568} // популярные значения перекрытия
	for _, ovl := range seqovls {
		// fake,multisplit + seqovl + fakeTLS + repeats (главный рабочий рецепт)
		m = append(m, desyncParams{strat: stratFake, cut: cutSNIstart, ttl: bestTTL, corrupt: corruptChecksum, fakes: 1, repeats: 8, seqovl: ovl, fakeTLS: true, ipIDZero: true})
		m = append(m, desyncParams{strat: stratFake, cut: cutSNImid, ttl: bestTTL, corrupt: corruptSeq, fakes: 1, repeats: 11, seqovl: ovl, fakeTLS: true, ipIDZero: true})
		// multisplit-стиль через disorder + seqovl (без fake, мягче)
		m = append(m, desyncParams{strat: stratDisorder, cut: cutSNIstart, ttl: bestTTL, corrupt: corruptNone, seqovl: ovl, fakeTLS: true, ipIDZero: true})
	}
	// fakeddisorder + repeats + fakeTLS (сильный вариант)
	m = append(m, desyncParams{strat: stratFakedDisorder, cut: cutSNImid, ttl: bestTTL, corrupt: corruptChecksum, fakes: 1, repeats: 8, fakeTLS: true, ipIDZero: true})
	m = append(m, desyncParams{strat: stratFake, cut: cutSNIstart, ttl: bestTTL, corrupt: corruptChecksum, fakes: 1, repeats: 11, fakeTLS: true, ipIDZero: true})
	return m
}

func buildMatrix(bestTTL byte) []desyncParams {
	var m []desyncParams
	// компактный набор: сильные техники, 2 позиции, лучший TTL + пара запасных.
	// Этого достаточно для страниц; не раздуваем до сотен (иначе перебор долгий).
	// порядок: сначала МЯГКИЕ (disorder/split — не ломают), потом агрессивные fake.
	// disorder у этого ТСПУ пробивал страницы — пробуем его первым.
	techniques := []strategyName{stratDisorder, stratSplit, stratFake, stratFakedDisorder}
	cuts := []cutMode{cutSNImid, cutSNIstart}
	ttls := []byte{bestTTL, 3, 5}
	for _, t := range techniques {
		for _, c := range cuts {
			if t == stratFake || t == stratFakedDisorder {
				for _, ttl := range ttls {
					m = append(m, desyncParams{strat: t, cut: c, ttl: ttl, corrupt: corruptChecksum, fakes: 1})
				}
			} else {
				m = append(m, desyncParams{strat: t, cut: c, ttl: 3, corrupt: corruptNone, fakes: 0})
			}
		}
	}
	return m
}

// probeData — реальная проба: TLS + HTTP GET + чтение ответа. true если данные пошли.
func probeData(target string, timeout time.Duration) bool {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	// Резолвим через СИСТЕМНЫЙ DNS (у пользователя настроен DoH) — НЕ через
	// 8.8.8.8:53 по UDP, который у многих провайдеров блокируется/спуфится
	// (из-за этого проба апдейтера Discord ложно падала на резолве).
	host, _, _ := net.SplitHostPort(target)
	if ips, e := net.DefaultResolver.LookupIP(ctx, "ip4", host); e == nil && len(ips) > 0 {
		ip4 := ips[0].To4()
		if ip4 != nil {
			setProbeTarget(uint32(ip4[0])<<24 | uint32(ip4[1])<<16 | uint32(ip4[2])<<8 | uint32(ip4[3]))
		}
	}
	d := &net.Dialer{Timeout: timeout}
	raw, err := d.DialContext(ctx, "tcp", target)
	if err != nil {
		return false
	}
	defer raw.Close()
	tconn := tls.Client(raw, &tls.Config{ServerName: host, InsecureSkipVerify: true})
	raw.SetDeadline(time.Now().Add(timeout))
	if err := tconn.HandshakeContext(ctx); err != nil {
		return false
	}
	req := "GET / HTTP/1.1\r\nHost: " + host + "\r\nConnection: close\r\n\r\n"
	if _, err := tconn.Write([]byte(req)); err != nil {
		tconn.Close()
		return false
	}
	buf := make([]byte, 1024)
	n, err := tconn.Read(buf)
	tconn.Close()
	return err == nil && n >= 16
}

// autoProbe перебирает матрицу, ищет комбинацию, тянущую страницу И видео.
// autoProbe идёт по ГРАДАЦИИ методов (простое→сложное) и фиксирует первый,
// при котором заработало видео. Если видео не покорилось — оставляет лучшее
// для страниц. Все методы перебираются АВТОМАТИЧЕСКИ, ничего руками.
func autoProbe(timeout time.Duration) {
	setProbing(true)
	defer setProbing(false) // по завершении — живой трафик идёт на выбранные params
	// на ступенях 1-2 дробление потока выключено (включим на ступени 3-4),
	// чтобы во время этих проб не трогать видео-трафик лишний раз
	setStreamEnabled(false)
	setIPFragEnabled(false)
	bestTTL := estimateBestTTL(timeout)
	matrix := buildMatrix(bestTTL)

	// --- СТУПЕНЬ 1+2: TCP-десинк (матрица техник/TTL/позиций) ---
	// Ищем комбинацию, тянущую И САЙТ (YouTube), И ПРИЛОЖЕНИЕ (Discord).
	logStepf("probe", "=== Ступень 1-2: TCP-десинк, %d комбинаций (TTL=%d первым). Проверяю сайт+приложение ===", len(matrix), bestTTL)
	var pageParams *desyncParams // первая, что пробила сайт (запасной вариант)
	var bothParams *desyncParams // пробила И сайт, И приложение (приоритет)
	tested := 0
	for i := range matrix {
		p := matrix[i]
		setProbeParams(p)
		tested++
		site, app := probeSiteAndApp(timeout)
		if !site && !app {
			continue
		}
		logStepf("probe", "[%d/%d] %s → сайт(YouTube)=%v приложение(Discord)=%v", tested, len(matrix), p.String(), site, app)
		if site && pageParams == nil {
			pp := p
			pageParams = &pp
		}
		if site && app && bothParams == nil {
			bp := p
			bothParams = &bp
			logStepf("probe", "=== %s пробивает И сайт И приложение — это наш кандидат ===", p.String())
		}
		// сразу проверим видео (вдруг повезёт без тяжёлых методов)
		if site && probeData(probeVideo, timeout) {
			logStepf("probe", "=== ВИДЕО+сайт пошли на Ступени 1-2: %s — фиксирую ===", p.String())
			setActiveParams(p)
			setActiveQUIC("desync")
			saveProfile(p, "desync", false, false, 0, "видео+сайт на ступени 1-2")
			return
		}
		// если нашли технику для сайта+приложения — дальше можно проверять видео на ней
		if bothParams != nil {
			break
		}
	}
	// выбираем лучшее: приоритет — пробивающее И сайт И приложение
	chosen := bothParams
	if chosen == nil {
		chosen = pageParams
	}
	if chosen == nil {
		logStep("probe", "=== проба не подтвердила технику; ставлю disorder (мягкая, не ломает) ===")
		dp := desyncParams{strat: stratDisorder, cut: cutSNImid, ttl: bestTTL, corrupt: corruptNone, fakes: 0}
		setActiveParams(dp)
		setActiveQUIC("pass")
		saveProfile(dp, "pass", false, false, 0, "disorder по умолчанию (проба не подтвердила)")
		return
	}
	if bothParams != nil {
		logStepf("probe", "выбрана техника для сайта+приложения: %s", chosen.String())
	} else {
		logStepf("probe", "выбрана техника для сайта (приложение не подтвердилось): %s", chosen.String())
	}
	pageParams = chosen
	setActiveParams(*pageParams)

	// --- СТУПЕНЬ APD: апдейтер Discord (updates.discord.com) ---
	// Проверяем, отвечает ли апдейтер на основной технике. Если нет — подбираем
	// ему ОТДЕЛЬНУЮ технику (вплоть до мощных fakeTLS/repeats), чтобы Discord
	// перестал виснуть на "Checking for updates".
	setUpdaterParams(nil)
	setProbeParams(*pageParams)
	if probeData(probeUpdater, timeout) {
		logStep("probe", "=== апдейтер Discord отвечает на основной технике — ок ===")
	} else {
		logStep("probe", "=== апдейтер Discord НЕ отвечает — подбираю ему отдельную технику ===")
		updMatrix := buildUpdaterMatrix(bestTTL)
		found := false
		for i := range updMatrix {
			up := updMatrix[i]
			setUpdaterParams(&up) // применяем кандидата ТОЛЬКО к апдейтеру
			setProbeParams(up)    // и к пробе
			if probeData(probeUpdater, timeout) {
				logStepf("probe", "=== апдейтер Discord ПОШЁЛ на технике: %s — фиксирую для него ===", up.String())
				found = true
				break
			}
			logStepf("probe", "апдейтер: %s — пока нет", up.String())
		}
		if !found {
			setUpdaterParams(nil)
			logStep("probe", "=== апдейтер Discord не пробился ни одной техникой (запуск Discord может виснуть) ===")
		}
	}
	setProbeParams(*pageParams) // вернуть пробе основную технику

	// В режиме только-Discord видео не тестируем — фиксируем технику и выходим.
	if discordOnlyMode {
		logStep("probe", "=== режим только-Discord: видео-ступени пропущены, техника зафиксирована ===")
		saveProfile(*pageParams, "pass", false, false, 0, "только Discord (тест)")
		return
	}

	// Это главный рабочий рецепт zapret против троттлинга видео, которого
	// раньше не было. Применяем к ClientHello googlevideo через probeParams.
	logStep("probe", "=== Ступень 2.5: мощные техники (seqovl+fakeTLS+repeats) на видео ===")
	vmatrix := buildVideoMatrix(bestTTL)
	for i := range vmatrix {
		vp := vmatrix[i]
		setProbeParams(vp)
		if probeData(probeVideo, timeout) {
			logStepf("probe", "=== ВИДЕО пошло на Ступени 2.5: %s — фиксирую! ===", vp.String())
			setActiveParams(vp)
			setActiveQUIC("pass")
			saveProfile(vp, "pass", false, false, 0, "видео через seqovl/fakeTLS (мощный рецепт)")
			return
		}
		logStepf("probe", "ступень 2.5: %s — видео пока нет", vp.String())
	}

	// --- СТУПЕНЬ 3: + дробление ПОТОКА видео (метод 1+4) ---
	logStep("probe", "=== Ступень 3: включаю дробление потока видео (метод 1+4) ===")
	setStreamEnabled(true)
	setIPFragEnabled(false)
	for _, parts := range []int{3, 4, 6} {
		setStreamParts(parts)
		if probeData(probeVideo, timeout) {
			logStepf("probe", "=== ВИДЕО пошло на Ступени 3: дробление x%d — фиксирую ===", parts)
			setActiveQUIC("desync")
			saveProfile(*pageParams, "desync", true, false, parts, "видео через дробление потока")
			return
		}
		logStepf("probe", "ступень 3: дробление x%d — видео пока нет", parts)
	}

	// --- СТУПЕНЬ 4: + IP-фрагментация потока (метод 3, тяжёлая артиллерия) ---
	logStep("probe", "=== Ступень 4: включаю IP-фрагментацию видео-потока (метод 3) ===")
	setIPFragEnabled(true)
	for _, parts := range []int{2, 3, 4} {
		setStreamParts(parts)
		if probeData(probeVideo, timeout) {
			logStepf("probe", "=== ВИДЕО пошло на Ступени 4: IP-фраг + дробление x%d — фиксирую ===", parts)
			setActiveQUIC("desync")
			saveProfile(*pageParams, "desync", true, true, parts, "видео через IP-фрагментацию потока")
			return
		}
		logStepf("probe", "ступень 4: IP-фраг x%d — видео пока нет", parts)
	}

	// --- Ничего не пробило видео: оставляем лучшее для страниц, видео → туннель ---
	logStep("probe", "=== видео не покорилось НИ ОДНИМ методом (1-4) ===")
	logStepf("probe", "=== фиксирую лучшее для страниц/Discord: %s; видео честно через туннель ===", pageParams.String())
	setStreamEnabled(false) // дробление видео не помогло — выключаем, чтобы не вредить
	setIPFragEnabled(false)
	setActiveParams(*pageParams)
	setActiveQUIC("pass") // НЕ трогаем QUIC — иначе ломаем не-видео сайты на QUIC
	saveProfile(*pageParams, "pass", false, false, 0, "страницы/Discord работают; видео через туннель")
}
