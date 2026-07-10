package main

// ── Таблица техник обхода для ЭСКАЛАЦИИ ────────────────────────────────────
//
// Когда обход домена не пробил, движок переключается на следующую технику по
// этому списку (как circular-оркестратор zapret). Порядок: от мягкого/дешёвого
// к агрессивному. Каждая запись — известная безопасная комбинация (реальный
// google-паттерн как fake, fail-open защитит при любой ошибке).

var techniqueTable = []struct {
	name string
	p    desyncParams
}{
	// #0 — disorder+seqovl=681+google. ПОДТВЕРЖДЁН по логу: discord.com И YouTube
	// получают данные. ГЛАВНАЯ рабочая техника. НЕ менять.
	{"disorder+seqovl681+google(подтв.)", desyncParams{strat: stratDisorder, cut: cutSNImid, ttl: 4, corrupt: corruptChecksum, fakes: 1, repeats: 6, fakeTLS: true, ipIDZero: true, seqovl: 681, decoy: decoyGoogle}},
	// #1 — fake+seqovl=681+google (проверенный fake-вариант, следующая ступень).
	{"fake+seqovl681+google", desyncParams{strat: stratFake, cut: cutSNIstart, ttl: 4, corrupt: corruptChecksum, fakes: 1, repeats: 6, fakeTLS: true, ipIDZero: true, seqovl: 681, decoy: decoyGoogle}},
	// #2 — fake+seqovl=568+google.
	{"fake+seqovl568+google", desyncParams{strat: stratFake, cut: cutSNIstart, ttl: 4, corrupt: corruptChecksum, fakes: 1, repeats: 6, fakeTLS: true, ipIDZero: true, seqovl: 568, decoy: decoyGoogle}},
	// #3 — fakeddisorder + md5sig (другой fooling).
	{"fakeddisorder+md5sig", desyncParams{strat: stratFakedDisorder, cut: cutSNImid, ttl: 4, corrupt: corruptMD5, fakes: 1, repeats: 6, fakeTLS: true, decoy: decoyGoogle}},
	// #4 — multidisorder (другой механизм реассемблера).
	{"multidisorder", desyncParams{strat: stratMultidisorder, cut: cutSNImid, ttl: 4, corrupt: corruptSeq, fakes: 1, repeats: 4, fakeTLS: true, decoy: decoyGoogle}},
	// #5 — disorder+badseq (мягкий).
	{"disorder+badseq", desyncParams{strat: stratDisorder, cut: cutSNImid, ttl: 4, corrupt: corruptSeq, fakes: 0}},
	// #6 — split+badsum (запасной).
	{"split+badsum", desyncParams{strat: stratSplit, cut: cutSNImid, ttl: 4, corrupt: corruptChecksum, fakes: 0}},
	// #7 — oob(URG), экзотика.
	{"oob(URG)", desyncParams{strat: stratOOB, cut: cutSNImid, ttl: 4, corrupt: corruptNone, fakes: 0}},
	// #8 — ЭКСПЕРИМЕНТ: zapret-general multisplit+seqovl568+4pda. По логу дал 0 данных
	// (возможно multisplit-реализация неидеальна). В КОНЦЕ — пробуем последним.
	{"эксп:multisplit+seqovl568+4pda", desyncParams{strat: stratMultisplit, cut: cutSNIstart, ttl: 4, corrupt: corruptChecksum, fakes: 1, repeats: 6, fakeTLS: true, ipIDZero: true, seqovl: 568, decoy: decoy4pda}},
	// #9 — ЭКСПЕРИМЕНТ: multisplit+seqovl681+google. Тоже в конце.
	{"эксп:multisplit+seqovl681+google", desyncParams{strat: stratMultisplit, cut: cutSNIstart, ttl: 4, corrupt: corruptChecksum, fakes: 1, repeats: 6, fakeTLS: true, ipIDZero: true, seqovl: 681, decoy: decoyGoogle}},
}

func numTechniques() int { return len(techniqueTable) }

// numEscalationTechniques — сколько техник перебирает АВТО-эскалация при неудаче.
// Последние 2 (#8-#9) — экспериментальные multisplit (по логу дают 0 данных у
// пользователя), поэтому авто-эскалация в них НЕ заходит (иначе ломает домены,
// которые до них доэскалировали, напр. googlevideo). Они доступны только через
// ручной --cycle для проверки. Если эксперименты докажут работу — поднять предел.
func numEscalationTechniques() int {
	n := len(techniqueTable) - 2
	if n < 1 {
		n = len(techniqueTable)
	}
	return n
}

func techniqueName(i int) string {
	if i < 0 || i >= len(techniqueTable) {
		return "?"
	}
	return techniqueTable[i].name
}

// techniqueByIndex возвращает параметры техники по индексу (с обёрткой границ).
func techniqueByIndex(i int) desyncParams {
	if i < 0 || i >= len(techniqueTable) {
		i = 0
	}
	return techniqueTable[i].p
}
