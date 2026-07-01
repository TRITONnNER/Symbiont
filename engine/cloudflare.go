package main

// ─────────────────────────────────────────────────────────────────────────────
// Cloudflare и другие ОБЩИЕ CDN-диапазоны. На этих IP сидят ОДНОВРЕМЕННО и
// заблокированные домены (напр. cloudflare-ech.com), и нужные сервисы (gateway и
// CDN Discord — gateway.discord.gg, cdn.discordapp.com тоже за Cloudflare).
//
// ПРОБЛЕМА, которую это чинит: refreshBlockedIPs резолвил cloudflare-ech.com в
// Cloudflare-IP (162.159.x) и добавлял их в список «рвать по IP». Резет затем рвал
// ВСЕ соединения к этим общим адресам — включая gateway/CDN Discord → Discord
// застревал на загрузке. Обход по ClientHello (SNI-десинк) от этого не страдает,
// поэтому исключаем общие CDN-диапазоны ТОЛЬКО из IP-резета.
//
// Диапазоны — официальные списки Cloudflare (могут меняться, но эти стабильны
// годами). Покрывают адреса, на которых живёт Discord.

type ipRange struct{ lo, hi uint32 }

var sharedCDNRanges = []ipRange{
	{0x68100000, 0x681FFFFF}, // 104.16.0.0/12  Cloudflare
	{0xA29E0000, 0xA29FFFFF}, // 162.158.0.0/15 Cloudflare (вкл. 162.159.x — gateway/CDN Discord)
	{0xAC400000, 0xAC47FFFF}, // 172.64.0.0/13  Cloudflare
	{0xADF53000, 0xADF53FFF}, // 173.245.48.0/20 Cloudflare
	{0xBC726000, 0xBC726FFF}, // 188.114.96.0/20 Cloudflare
	{0xBE5DF000, 0xBE5DFFFF}, // 190.93.240.0/20 Cloudflare
	{0xC5EAF000, 0xC5EAF3FF}, // 197.234.240.0/22 Cloudflare
	{0xC6298000, 0xC629FFFF}, // 198.41.128.0/17 Cloudflare
	{0x8D654000, 0x8D657FFF}, // 141.101.64.0/18 Cloudflare
	{0x6CA2C000, 0x6CA2FFFF}, // 108.162.192.0/18 Cloudflare
	{0x83004800, 0x83004BFF}, // 131.0.72.0/22 Cloudflare
}

// isSharedCDNIP — IP принадлежит общему CDN (Cloudflare). Такие НЕЛЬЗЯ рвать по IP,
// т.к. на них сидят и нужные сервисы.
func isSharedCDNIP(ip uint32) bool {
	for _, r := range sharedCDNRanges {
		if ip >= r.lo && ip <= r.hi {
			return true
		}
	}
	return false
}
