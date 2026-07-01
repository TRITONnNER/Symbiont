package main

import _ "embed"

// РЕАЛЬНЫЕ захваченные пакеты (а не синтетика). Источник — открытый проект
// zapret (GPL): это сырые L7-payload'ы настоящих хендшейков. DPL/ТСПУ принимает
// их как легитимные (структурно валидные), а реальный сервер отбрасывает (fake
// едет с низким TTL/битым seq). Синтетика из нулей stateful-DPI НЕ пробивает —
// это и была главная причина, почему ранний движок не брал то, что берёт zapret.

//go:embed fakes/tls_clienthello_www_google_com.bin
var fakeTLSGoogle []byte // 681 байт — реальный google ClientHello (для seqovl google-класса)

//go:embed fakes/tls_clienthello_4pda_to.bin
var fakeTLS4pda []byte // 284 байта — ClientHello 4pda (для общих сайтов, seqovl 568)

//go:embed fakes/quic_initial_www_google_com.bin
var fakeQUICGoogle []byte // 1200 байт — реальный QUIC Initial google

//go:embed fakes/quic_initial_dbankcloud_ru.bin
var fakeVoiceDiscord []byte // 1357 байт — для fake-discord и fake-stun (голос)

//go:embed fakes/stun.bin
var fakeSTUN []byte // 100 байт — STUN
