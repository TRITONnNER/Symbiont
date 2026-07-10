package main

import (
	"strings"
	"sync"
	"time"
)

// ─────────────────────────────────────────────────────────────────────────────
// Режим АВТОПЕРЕБОРА техник (--cycle=домен). За один запуск прогоняет ВСЕ техники
// по очереди (по cycleWindow секунд каждую) на указанном домене и логирует, на
// какой пошли входящие данные. Чинит медленный цикл отладки: вместо 10 сессий —
// один запуск с готовым ответом «для X работает техника #N».
// ─────────────────────────────────────────────────────────────────────────────

const cycleWindow = 15 * time.Second

var (
	cycleTarget     string // домен-подстрока для перебора (пусто = выкл)
	cycleIdx        int
	cycleLastSwitch time.Time
	cyclePrev       dstat
	cycleDone       bool
	cycleMu         sync.Mutex
)

func cycleEnabled() bool {
	cycleMu.Lock()
	defer cycleMu.Unlock()
	return cycleTarget != "" && !cycleDone
}

// cycleMatch — относится ли домен к перебираемому (по подстроке).
func cycleMatch(sld string) bool {
	cycleMu.Lock()
	defer cycleMu.Unlock()
	if cycleTarget == "" || cycleDone {
		return false
	}
	return strings.Contains(sld, cycleTarget)
}

// cycleTech — текущая перебираемая техника (для techIdxFor override).
func cycleTech() int {
	cycleMu.Lock()
	defer cycleMu.Unlock()
	return cycleIdx
}

// cycleTick — вызывать из тикера. Раз в cycleWindow переключает технику и логирует
// дельту статистики (сколько данных/RST/silent пришло на предыдущей технике).
func cycleTick() {
	cycleMu.Lock()
	if cycleTarget == "" || cycleDone {
		cycleMu.Unlock()
		return
	}
	target := cycleTarget
	if cycleLastSwitch.IsZero() {
		cycleLastSwitch = time.Now()
		idx := cycleIdx
		cycleMu.Unlock()
		logStepf("cycle", "═══ АВТОПЕРЕБОР техник для «%s» ═══ начинаю с #%d (%s), по %ds на технику", target, idx, techniqueName(idx), int(cycleWindow.Seconds()))
		return
	}
	if time.Since(cycleLastSwitch) < cycleWindow {
		cycleMu.Unlock()
		return
	}
	prevIdx := cycleIdx
	prev := cyclePrev
	cycleMu.Unlock()

	// снимок статистики по целевому домену (target может быть SLD или подстрокой —
	// суммируем все подходящие)
	st := snapshotStats()
	var cur dstat
	for k, s := range st {
		if strings.Contains(k, target) {
			cur.data += s.data
			cur.rst += s.rst
			cur.silent += s.silent
		}
	}
	dData := cur.data - prev.data
	dRST := cur.rst - prev.rst
	dSilent := cur.silent - prev.silent
	verdictStr := "✗ нет данных"
	if dData > 0 && dRST == 0 && dSilent == 0 {
		verdictStr = "✔✔ РАБОТАЕТ (данные пошли, без RST/silent)"
	} else if dData > 0 {
		verdictStr = "± частично (данные есть, но и помехи)"
	} else if dRST > 0 {
		verdictStr = "✗ RST (ТСПУ режет)"
	} else if dSilent > 0 {
		verdictStr = "✗ silent-drop (молча гасит)"
	}
	logStepf("cycle", "техника #%d (%s): +данные=%d +RST=%d +silent=%d → %s", prevIdx, techniqueName(prevIdx), dData, dRST, dSilent, verdictStr)

	cycleMu.Lock()
	cyclePrev = cur
	cycleIdx++
	cycleLastSwitch = time.Now()
	if cycleIdx >= numTechniques() {
		cycleDone = true
		cycleMu.Unlock()
		logStepf("cycle", "═══ ПЕРЕБОР ЗАВЕРШЁН для «%s» ═══ смотри выше, какая техника дала «РАБОТАЕТ». Запусти с ней постоянно (она запомнится в автосписок).", target)
		return
	}
	nextIdx := cycleIdx
	cycleMu.Unlock()
	logStepf("cycle", "→ переключаю на технику #%d (%s)", nextIdx, techniqueName(nextIdx))
}
