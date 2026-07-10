package main

import (
	"os"
	"strings"
)

// Hostlist решает, к КАКИМ доменам применять обход.
// КРИТИЧНО: трогать только заблокированные сайты. Остальное (банки, DNS, гос,
// игровые логины, аккаунты) пропускать как есть — иначе мы их ломаем.

// дефолтный список типично заблокированных в РФ доменов (суффиксы).
var defaultBlocked = []string{
	"youtube.com", "googlevideo.com", "ytimg.com", "ggpht.com", "youtu.be",
	"instagram.com", "cdninstagram.com",
	"whatsapp.com", "whatsapp.net", "wa.me",
	"facebook.com", "fbcdn.net", "fb.com",
	"twitter.com", "x.com", "twimg.com",
	"discord.com", "discord.gg", "discordapp.net", "discordapp.com", "discord.media",
	"telegram.org", "t.me", "telegram.me", "tdesktop.com", "telesco.pe",
	"tiktok.com", "tiktokcdn.com",
	"signal.org",
	"soundcloud.com",
	"rutracker.org",
	"linkedin.com",
}

type hostlist struct {
	suffixes    []string
	excludes    []string // домены-ИСКЛЮЧЕНИЯ: не трогаем, даже если под общим правилом
	discordOnly bool     // режим теста: только Discord
	matchAll    bool     // если true — режем всё (для отладки/совместимости)
}

// defaultExcludes — служебные домены, которым обход НЕ нужен и только ВРЕДИТ
// (апдейтер зависал из-за обхода). CDN-домены НЕ исключаем — им обход может быть
// нужен. Если что-то ещё зависает — пользователь добавит в hosts-exclude.
// discordDomains — ВСЕ домены Discord (для режима --only-discord: тестируем
// только Discord, не трогаем и не логируем YouTube/прочее).
var discordDomains = []string{
	"discord.com", "discord.gg", "discordapp.net", "discordapp.com",
	"discord.media", "discordapp.io", "discord.dev", "discordstatus.com",
	"updates.discord.com", "stable.dl2.discordapp.net",
}

// defaultExcludes — домены, которым обход НЕ нужен. Сейчас пуст: апдейтер
// Discord УБРАН отсюда, потому что его НАДО обходить (он душится по SNI на
// TCP/443, как сайты). Раньше он был тут — и это мешало его обходить!
var defaultExcludes = []string{}

// newDiscordHostlist — режим теста ТОЛЬКО Discord: обрабатываем и логируем
// исключительно домены Discord (включая апдейтер), остальное не трогаем.
func newDiscordHostlist() *hostlist {
	h := &hostlist{}
	h.suffixes = append(h.suffixes, discordDomains...)
	h.discordOnly = true
	logStepf("hostlist", "режим ТОЛЬКО DISCORD: %d доменов Discord (YouTube/прочее не трогаем)", len(h.suffixes))
	return h
}

// newHostlist строит список из файла (по строке на домен) или из дефолта.
// Спец-значение "*" в файле или пустой файл с флагом → режем всё.
func newHostlist(path string) *hostlist {
	h := &hostlist{}
	h.excludes = append(h.excludes, defaultExcludes...) // апдейтер/CDN — не трогаем
	if path == "" {
		h.suffixes = append(h.suffixes, defaultBlocked...)
		logStepf("hostlist", "встроенный список (%d доменов) + %d исключений (апдейтер Discord и пр.)", len(h.suffixes), len(h.excludes))
		return h
	}
	data, err := os.ReadFile(path)
	if err != nil {
		logStepf("hostlist", "не прочитал %s (%v) → встроенный список", path, err)
		h.suffixes = append(h.suffixes, defaultBlocked...)
		return h
	}
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(strings.ToLower(line))
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if line == "*" {
			h.matchAll = true
			logStep("hostlist", "режим '*': обрабатываю ВЕСЬ TLS-трафик")
			return h
		}
		h.suffixes = append(h.suffixes, line)
	}
	if len(h.suffixes) == 0 {
		h.suffixes = append(h.suffixes, defaultBlocked...)
	}
	logStepf("hostlist", "загружено доменов из файла: %d", len(h.suffixes))
	return h
}

// match возвращает true, если SNI входит в список (по суффиксу домена).
// isExcluded — домен в списке исключений (обход ему только навредит)?
func (h *hostlist) isExcluded(sni string) bool {
	s := strings.ToLower(strings.TrimSpace(sni))
	if s == "" {
		return false
	}
	for _, ex := range h.excludes {
		if s == ex || strings.HasSuffix(s, "."+ex) {
			return true
		}
	}
	return false
}

func (h *hostlist) match(sni string) bool {
	s := strings.ToLower(strings.TrimSpace(sni))
	if s == "" {
		return false // пустой SNI — не трогаем (часто служебный/DNS трафик)
	}
	// ИСКЛЮЧЕНИЯ имеют приоритет: апдейтер/CDN/телеметрия — НЕ трогаем,
	// даже если домен подходит под общее правило (иначе ломаем им соединение).
	for _, ex := range h.excludes {
		if s == ex || strings.HasSuffix(s, "."+ex) {
			return false
		}
	}
	if h.matchAll {
		return true
	}
	for _, suf := range h.suffixes {
		if s == suf || strings.HasSuffix(s, "."+suf) {
			return true
		}
	}
	return false
}

// isUpdaterSNI — домен апдейтера Discord (Squirrel, виснет на проверке обновлений)?
func isUpdaterSNI(sni string) bool {
	s := strings.ToLower(strings.TrimSpace(sni))
	return s == "updates.discord.com" || s == "update.discord.com"
}

// isVideoSNI — это домен видео-CDN (поток видео, который душат троттлингом)?
// isGoogleSNI — домен из фазы google zapret (list-google.txt): YouTube и его CDN.
// Для них применяется точный рецепт zapret (multisplit+seqovl681+ip-id=zero).
func isGoogleSNI(sni string) bool {
	s := strings.ToLower(strings.TrimSpace(sni))
	for _, suf := range []string{
		"googlevideo.com", "youtube.com", "youtu.be", "ytimg.com", "ggpht.com",
		"googleusercontent.com", "youtube-nocookie.com", "youtubekids.com",
		"googleapis.com", "play.google.com", "l.google.com",
	} {
		if s == suf || strings.HasSuffix(s, "."+suf) {
			return true
		}
	}
	return false
}

func isVideoSNI(sni string) bool {
	s := strings.ToLower(strings.TrimSpace(sni))
	for _, suf := range []string{"googlevideo.com", "ytimg.com", "ggpht.com"} {
		if s == suf || strings.HasSuffix(s, "."+suf) {
			return true
		}
	}
	return false
}
