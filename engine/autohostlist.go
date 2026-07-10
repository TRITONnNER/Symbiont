package main

import (
	"bufio"
	"os"
	"strconv"
	"strings"
	"sync"
)

// ── Автосписок заблокированных доменов (autohostlist) ──────────────────────
//
// Вариант C сам обнаруживает заблокированные домены. Чтобы это знание НЕ
// терялось при перезапуске, пишем их в текстовый файл рядом с .exe и подхватываем
// при старте (обходим сразу, без повторного обучения). Файл человекочитаемый —
// можно добавлять/убирать домены ВРУЧНУЮ (по домену на строку, # = комментарий).
// Это и память движка, и ручное управление списком одновременно.

const blockedPath = "symbiont-blocked.txt"

var (
	blkMu   sync.Mutex
	blkSeen = map[string]bool{} // что уже в файле (чтобы не дублировать)
)

// loadBlockedHosts читает автосписок при старте и помечает домены как
// заблокированные навсегда (persistent). ok=false, если файла нет.
func loadBlockedHosts() int {
	data, err := os.ReadFile(blockedPath)
	if err != nil {
		return 0
	}
	n := 0
	blkMu.Lock()
	sc := bufio.NewScanner(strings.NewReader(string(data)))
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		line = strings.ToLower(line)
		// формат: "домен" (старый) или "домен техника" (новый — запомненный рецепт)
		fields := strings.Fields(line)
		dom := fields[0]
		techIdx := 0
		if len(fields) >= 2 {
			if t, e := strconv.Atoi(fields[1]); e == nil {
				techIdx = t
			}
		}
		blkSeen[dom] = true
		rememberedTech[dom] = techIdx // чтобы при подтверждении той же техники не дублировать
		behavior.addPersistentBlocked(dom, techIdx)
		n++
	}
	blkMu.Unlock()
	if n > 0 {
		logStepf("hostlist", "автосписок: загружено %d заблокированных доменов из %s (обхожу сразу)", n, blockedPath)
	}
	return n
}

// appendBlockedHost дописывает домен в автосписок, если его там ещё нет.
func appendBlockedHost(host string) {
	host = strings.ToLower(strings.TrimSpace(host))
	if host == "" {
		return
	}
	blkMu.Lock()
	defer blkMu.Unlock()
	if blkSeen[host] {
		return
	}
	blkSeen[host] = true
	f, err := os.OpenFile(blockedPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
	if err != nil {
		return
	}
	defer f.Close()
	_, _ = f.WriteString(host + "\n")
	logStepf("hostlist", "автосписок: + %s (сохранён, обойдётся и после перезапуска)", host)
}

var rememberedTech = map[string]int{}

// rememberTechnique запоминает РАБОЧУЮ технику для домена (дописывает «домен N» в
// автосписок). При следующем запуске движок стартует сразу с неё — без перебора.
// Сохраняет только при СМЕНЕ запомненной техники (не спамит файл).
func rememberTechnique(host string, techIdx int) {
	host = strings.ToLower(strings.TrimSpace(host))
	if host == "" {
		return
	}
	blkMu.Lock()
	defer blkMu.Unlock()
	if prev, ok := rememberedTech[host]; ok && prev == techIdx {
		return // уже записана эта техника — не дублируем
	}
	rememberedTech[host] = techIdx
	blkSeen[host] = true
	f, err := os.OpenFile(blockedPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
	if err != nil {
		return
	}
	defer f.Close()
	_, _ = f.WriteString(host + " " + strconv.Itoa(techIdx) + "\n")
	logStepf("hostlist", "автосписок: %s → техника #%d запомнена (после перезапуска стартую сразу с неё)", host, techIdx)
}
