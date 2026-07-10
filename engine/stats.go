package main

import (
	"sort"
	"sync"
	"time"
)

// ─────────────────────────────────────────────────────────────────────────────
// Модуль статистики (НАБЛЮДАЕМОСТЬ). Полностью развязан с логикой обхода — только
// считает события per-домен, чтобы по логу было видно ЧТО/КАК/ПОЧЕМУ работает.
// ─────────────────────────────────────────────────────────────────────────────

type dstat struct {
	ch        int       // ClientHello к домену
	data      int       // входящих data-пакетов (ответы сервера)
	rst       int       // входящих RST (сброс соединения ТСПУ)
	silent    int       // silent-drop событий (молчаливый дроп)
	esc       int       // переключений техники (эскалаций)
	resets    int       // разрывов соединений к домену
	bytes     int64     // примерный объём входящих данных (байт)
	firstSeen time.Time // когда домен впервые замечен
	firstData time.Time // когда впервые пришли данные (латентность обхода)
}

var (
	dstats   = map[string]*dstat{}
	dstatsMu sync.Mutex
)

func getStatLocked(sld string) *dstat {
	s := dstats[sld]
	if s == nil {
		s = &dstat{firstSeen: time.Now()}
		dstats[sld] = s
		if len(dstats) > 3000 { // защита от роста памяти
			// удаляем без активности (грубо): оставляем как есть, просто не плодим
		}
	}
	return s
}

func statCH(sld string) {
	if sld == "" {
		return
	}
	dstatsMu.Lock()
	getStatLocked(sld).ch++
	dstatsMu.Unlock()
}

func statData(sld string, n int) {
	if sld == "" {
		return
	}
	dstatsMu.Lock()
	s := getStatLocked(sld)
	s.data++
	s.bytes += int64(n)
	if s.firstData.IsZero() {
		s.firstData = time.Now()
	}
	dstatsMu.Unlock()
}

func statRST(sld string) {
	if sld == "" {
		return
	}
	dstatsMu.Lock()
	getStatLocked(sld).rst++
	dstatsMu.Unlock()
}

func statSilent(sld string) {
	if sld == "" {
		return
	}
	dstatsMu.Lock()
	getStatLocked(sld).silent++
	dstatsMu.Unlock()
}

func statEsc(sld string) {
	if sld == "" {
		return
	}
	dstatsMu.Lock()
	getStatLocked(sld).esc++
	dstatsMu.Unlock()
}

func statReset(sld string) {
	if sld == "" {
		return
	}
	dstatsMu.Lock()
	getStatLocked(sld).resets++
	dstatsMu.Unlock()
}

// snapshotStats — копия статистики для отрисовки (под локом, потом отпускаем).
func snapshotStats() map[string]dstat {
	dstatsMu.Lock()
	defer dstatsMu.Unlock()
	out := make(map[string]dstat, len(dstats))
	for k, v := range dstats {
		out[k] = *v
	}
	return out
}

// asciiBar рисует горизонтальный бар из value относительно max (ширина width).
func asciiBar(value, max, width int) string {
	if max <= 0 || value <= 0 {
		return ""
	}
	n := value * width / max
	if n < 1 && value > 0 {
		n = 1
	}
	if n > width {
		n = width
	}
	b := make([]rune, n)
	for i := range b {
		b[i] = '█'
	}
	return string(b)
}

// sortedStatKeys — ключи статистики, отсортированные по «интересности»
// (сначала с RST/silent — проблемные, потом по объёму данных).
func sortedStatKeys(m map[string]dstat) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool {
		a, b := m[keys[i]], m[keys[j]]
		ap := a.rst + a.silent
		bp := b.rst + b.silent
		if ap != bp {
			return ap > bp // проблемные сверху
		}
		return a.data > b.data
	})
	return keys
}
