package main

import (
	"context"
	"crypto/tls"
	"math/rand"
	"net"
	"sync"
	"sync/atomic"
	"time"
)

// ── Переключатели (безопасные, рискованное по умолчанию ВЫКЛ) ──────────────
var (
	diagnoseOn  int32 = 0
	randomizeOn int32 = 0
)

func setDiagnoseEnabled(v bool) {
	if v {
		atomic.StoreInt32(&diagnoseOn, 1)
	} else {
		atomic.StoreInt32(&diagnoseOn, 0)
	}
}
func diagnoseEnabled() bool { return atomic.LoadInt32(&diagnoseOn) == 1 }

func setRandomizeEnabled(v bool) {
	if v {
		atomic.StoreInt32(&randomizeOn, 1)
	} else {
		atomic.StoreInt32(&randomizeOn, 0)
	}
}
func randomizeEnabled() bool { return atomic.LoadInt32(&randomizeOn) == 1 }

// jitterCut слегка сдвигает позицию разреза (±2) для рандомизации против
// статистического детекта. Безопасно: остаётся в пределах payload. Только если
// включён --randomize.
func jitterCut(cut, payloadLen int) int {
	if !randomizeEnabled() || payloadLen < 8 {
		return cut
	}
	d := rand.Intn(5) - 2 // -2..+2
	c := cut + d
	if c < 1 {
		c = 1
	}
	if c >= payloadLen {
		c = payloadLen - 1
	}
	return c
}

// ── Диагностика типа блокировки (пассивная, безопасная) ────────────────────
// Классифицируем по наблюдаемому поведению per-домен:
//   - пришёл RST после ClientHello, данных не было → SNI-блокировка ТСПУ
//   - ClientHello ушёл, ответа нет совсем (только ретрансмиты) → дроп/IP-блок
//   - данные пошли → работает
type blockType int

const (
	btUnknown blockType = iota
	btSNIBlock
	btNoResponse
	btWorking
)

func (bt blockType) String() string {
	switch bt {
	case btSNIBlock:
		return "SNI-блокировка (ТСПУ режет по имени сайта)"
	case btNoResponse:
		return "проба без обхода не прошла (узел мог быть недоступен ИЛИ это DPI-блок) — обход всё равно применяю"
	case btWorking:
		return "работает"
	default:
		return "неизвестно"
	}
}

type diagInfo struct {
	bt   blockType
	at   time.Time
	seen bool
}

var (
	diagMu  sync.Mutex
	diagByH = map[string]*diagInfo{}
)

// diagReport фиксирует тип блокировки для домена и логирует при изменении.
func diagReport(host string, bt blockType) {
	if !diagnoseEnabled() || host == "" {
		return
	}
	key := reduceToSLD(host)
	diagMu.Lock()
	defer diagMu.Unlock()
	di := diagByH[key]
	if di == nil {
		di = &diagInfo{}
		diagByH[key] = di
	}
	if di.bt != bt {
		di.bt = bt
		di.at = time.Now()
		logStepf("диагностика", "%s: %s", key, bt.String())
	}
}

// ── Самодиагностика канала (пассивная, безопасная) ─────────────────────────
// Считаем ошибки отправки (признак переполнения очереди WinDivert / перегрузки).
// Только наблюдаем и предупреждаем — НИКАКИХ авто-действий, которые могли бы
// уронить сеть. Авто-лечение (смена MTU и т.п.) — отдельный осознанный шаг.
var (
	sendErrCnt  int64
	sendOKCnt   int64
	lastErrWarn int64
)

func noteSendResult(err error) {
	if err != nil {
		n := atomic.AddInt64(&sendErrCnt, 1)
		if n == 1 || n%100 == 0 {
			logStepf("канал", "ошибок отправки=%d (возможна перегрузка/переполнение очереди — обход продолжает работать)", n)
		}
	} else {
		atomic.AddInt64(&sendOKCnt, 1)
	}
}

func channelStats() (okN, errN int64) {
	return atomic.LoadInt64(&sendOKCnt), atomic.LoadInt64(&sendErrCnt)
}

// ── АКТИВНАЯ диагностика (безопасная: ИЗОЛИРОВАННЫЕ соединения) ─────────────
// Шлёт пробы на ОТДЕЛЬНЫХ сокетах (не трогая твой живой трафик) и классифицирует,
// что делает ТСПУ. TCP-проба отделяет IP-блок от SNI-блока:
//   - TCP не коннектится         → IP-блок/недоступен (локально не берётся, туннель)
//   - TCP ок, но TLS рвётся      → SNI-блокировка (ТСПУ режет по имени) — наш профиль
//   - TCP ок и TLS ок            → работает (обход не нужен)
// Безопасно по построению: соединения изолированы, с таймаутами; провал пробы
// твой браузер не заметит.

// probeTCPOnly — только TCP-коннект (без TLS). true если соединение установилось.
func probeTCPOnly(hostport string, timeout time.Duration) bool {
	d := &net.Dialer{Timeout: timeout}
	c, err := d.Dial("tcp", hostport)
	if err != nil {
		return false
	}
	_ = c.Close()
	return true
}

// probeTLSOnly — TCP + TLS-хендшейк с указанным SNI. true если хендшейк прошёл.
func probeTLSOnly(hostport, sni string, timeout time.Duration) bool {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	d := &net.Dialer{Timeout: timeout}
	raw, err := d.DialContext(ctx, "tcp", hostport)
	if err != nil {
		return false
	}
	defer raw.Close()
	raw.SetDeadline(time.Now().Add(timeout))
	tc := tls.Client(raw, &tls.Config{ServerName: sni, InsecureSkipVerify: true})
	return tc.HandshakeContext(ctx) == nil
}

// activeDiagnoseOne диагностирует один домен на изолированных соединениях.
func activeDiagnoseOne(host string, timeout time.Duration) blockType {
	hp := host + ":443"
	tcpOK := probeTCPOnly(hp, timeout)
	if !tcpOK {
		return btNoResponse // IP-уровень: не коннектится
	}
	if probeTLSOnly(hp, host, timeout) {
		return btWorking // и TCP, и TLS прошли
	}
	return btSNIBlock // TCP ок, TLS рвётся = блок по SNI
}

// runActiveDiagnosis — одноразовый прогон активной диагностики по списку доменов.
// Запускается при --diagnose в фоне, не блокирует старт. Безопасно.
func runActiveDiagnosis(hosts []string) {
	if !diagnoseEnabled() {
		return
	}
	go func() {
		timeout := 4 * time.Second
		logStep("диагностика", "активная диагностика (изолированные пробы, твой трафик не трогаю)…")
		for _, h := range hosts {
			bt := activeDiagnoseOne(h, timeout)
			logStepf("диагностика", "%s → %s", h, bt.String())
			diagReport(h, bt)
			time.Sleep(300 * time.Millisecond) // не частим
		}
		logStep("диагностика", "активная диагностика завершена")
	}()
}

// ── A+B авто-лечение (безопасное самолечение перегрузки) ───────────────────
// Единственное «лечащее» действие, которое НЕ может уронить сеть: при росте
// ошибок отправки (перегрузка/переполнение очереди WinDivert) автоматически
// СНИЖАЕМ интенсивность собственного обхода (меньше repeats фейков), а при
// норме — восстанавливаем. В худшем случае обход просто мягче — сеть цела.
var desyncIntensityPct int64 = 100 // 100% = полная интенсивность

func desyncIntensity() int { return int(atomic.LoadInt64(&desyncIntensityPct)) }

// scaleReps масштабирует число повторов под текущую интенсивность (минимум 1).
func scaleReps(reps int) int {
	r := reps * desyncIntensity() / 100
	if r < 1 {
		r = 1
	}
	return r
}

var (
	healLastErr   int64
	healZeroTicks int
)

// healTick вызывается из тикера (раз в 5с). Сравнивает прирост ошибок отправки
// и регулирует интенсивность. Полностью безопасно: только меняет НАШУ активность.
func healTick() {
	_, errN := channelStats()
	delta := errN - atomic.LoadInt64(&healLastErr)
	atomic.StoreInt64(&healLastErr, errN)
	cur := atomic.LoadInt64(&desyncIntensityPct)
	switch {
	case delta > 50: // много ошибок за интервал → перегрузка, снижаем
		healZeroTicks = 0
		n := cur - 25
		if n < 50 {
			n = 50
		}
		if n != cur {
			atomic.StoreInt64(&desyncIntensityPct, n)
			logStepf("канал", "перегрузка (ошибок +%d) → снижаю интенсивность обхода до %d%% (сеть в приоритете)", delta, n)
		}
	case delta == 0: // тихо — постепенно восстанавливаем
		healZeroTicks++
		if healZeroTicks >= 3 && cur < 100 {
			n := cur + 25
			if n > 100 {
				n = 100
			}
			atomic.StoreInt64(&desyncIntensityPct, n)
			logStepf("канал", "канал стабилен → восстанавливаю интенсивность обхода до %d%%", n)
			healZeroTicks = 0
		}
	default:
		healZeroTicks = 0
	}
}
