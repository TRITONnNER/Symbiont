//go:build windows

package main

// Запоминание рабочей техники между запусками. Движок сам определяет, какая
// техника пробивает ИМЕННО твою сеть/ТСПУ, и сохраняет её в файл рядом с .exe.
// При следующем старте — сразу берёт её, не перебирая заново (твоя сеть стабильна).

import (
	"encoding/json"
	"os"
)

const profilePath = "symbiont-profile.json"

// savedProfile — что храним на диске (человекочитаемый JSON).
type savedProfile struct {
	Strat   string `json:"strat"`
	Cut     int    `json:"cut"`
	TTL     int    `json:"ttl"`
	Corrupt int    `json:"corrupt"`
	Fakes   int    `json:"fakes"`
	QUIC    string `json:"quic"`
	Stream  bool   `json:"stream"`
	IPFrag  bool   `json:"ipfrag"`
	Parts   int    `json:"parts"`
	Note    string `json:"note"` // для человека: что это
}

// saveProfile сохраняет найденную рабочую конфигурацию.
func saveProfile(p desyncParams, quic string, stream, ipfrag bool, parts int, note string) {
	sp := savedProfile{
		Strat: string(p.strat), Cut: int(p.cut), TTL: int(p.ttl),
		Corrupt: int(p.corrupt), Fakes: p.fakes,
		QUIC: quic, Stream: stream, IPFrag: ipfrag, Parts: parts, Note: note,
	}
	data, err := json.MarshalIndent(sp, "", "  ")
	if err != nil {
		return
	}
	if err := os.WriteFile(profilePath, data, 0644); err != nil {
		logStepf("profile", "не смог сохранить профиль: %v", err)
		return
	}
	logStepf("profile", "рабочая техника сохранена в %s (%s)", profilePath, note)
}

// loadProfile читает сохранённую конфигурацию. ok=false, если файла нет.
func loadProfile() (desyncParams, string, bool, bool, int, bool) {
	data, err := os.ReadFile(profilePath)
	if err != nil {
		return desyncParams{}, "", false, false, 0, false
	}
	var sp savedProfile
	if err := json.Unmarshal(data, &sp); err != nil {
		return desyncParams{}, "", false, false, 0, false
	}
	p := desyncParams{
		strat:   strategyName(sp.Strat),
		cut:     cutMode(sp.Cut),
		ttl:     byte(sp.TTL),
		corrupt: fakeCorrupt(sp.Corrupt),
		fakes:   sp.Fakes,
	}
	logStepf("profile", "загружен сохранённый профиль: %s, QUIC=%s, stream=%v (%s)", p.String(), sp.QUIC, sp.Stream, sp.Note)
	return p, sp.QUIC, sp.Stream, sp.IPFrag, sp.Parts, true
}
