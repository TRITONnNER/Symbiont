package main

// ── ЕДИНАЯ ЛОГИКА РЕШЕНИЙ: обходить или не трогать ─────────────────────────
//
// Здесь в одном месте, явно и читаемо, описаны ВСЕ варианты того, как движок
// решает, применять ли обход к соединению. Принцип: трогаем только то, что
// реально мешает; рабочее и чувствительное не трогаем. Это и безопасно
// (интернет/рабочие сервисы не ломаются), и эффективно (не тратим силы зря).

// desyncDecision — что делать с соединением и почему (причина идёт в лог).
type desyncDecision struct {
	apply  bool   // применять ли обход
	reason string // человекочитаемая причина (для лога/диагностики)
}

// decideForTLS — главная функция решения для TLS-соединения (по SNI).
// Разбирает все случаи по порядку приоритета.
func decideForTLS(sni string, fragmented, hostlistHit, excluded bool) desyncDecision {
	// 1) Явное ИСКЛЮЧЕНИЕ (служебные домены, апдейтеры, то что обход ломает) —
	//    не трогаем НИКОГДА, даже если внешне похоже на заблокированное.
	if excluded {
		return desyncDecision{false, "домен в списке исключений — обход только навредит"}
	}
	// 2) Пустой SNI — не за что зацепиться, не трогаем (часто это служебное/ESNI).
	if sni == "" {
		return desyncDecision{false, "нет SNI — наблюдаю, не трогаю"}
	}
	// 3) Многопакетный (kyber) ClientHello — резать опасно. Обходим БЕЗОПАСНО
	//    (fake-decoy), но это решает отдельный путь; здесь помечаем, что десинк
	//    основного пакета не делаем.
	if fragmented {
		return desyncDecision{false, "kyber-ClientHello — обход только через безопасный fake-decoy, основной пакет не трогаю"}
	}
	// 4) Явный список заблокированных (встроенный + автосписок + ручной) — обходим.
	if hostlistHit {
		return desyncDecision{true, "домен в списке заблокированных — обхожу"}
	}
	// 5) Поведенческий вердикт варианта C.
	switch behavior.lookupVerdict(reduceToSLD(sni)) {
	case vBlocked:
		return desyncDecision{true, "вариант C: домен подтверждённо заблокирован (RST/ретрансмиссии) — обхожу"}
	case vWorking:
		return desyncDecision{false, "вариант C: домен работает штатно — НЕ трогаю"}
	default:
		// 6) Неизвестно — НАБЛЮДАЕМ, не трогаем. Десинк включится, только когда
		//    накопятся признаки блокировки. Так рабочее не ломается на ровном месте.
		return desyncDecision{false, "вариант C: пока не доказана блокировка — наблюдаю, не трогаю"}
	}
}

// логирование решения раз в N, чтобы видеть логику, но не спамить.
var decisionLogN int64

func logDecisionOnce(sni string, d desyncDecision) {
	if !diagnoseEnabled() {
		return
	}
	decisionLogN++
	if decisionLogN <= 20 || decisionLogN%100 == 0 {
		logStepf("решение", "%s → %v (%s)", sni, map[bool]string{true: "ОБХОД", false: "не трогаю"}[d.apply], d.reason)
	}
}
