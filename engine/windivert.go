//go:build windows

package main

// Прямой доступ к WinDivert.dll через syscall (без cgo).
// WinDivert — это драйвер перехвата пакетов на уровне Windows Filtering Platform.
// Мы загружаем WinDivert.dll в рантайме (она должна лежать рядом с .exe),
// открываем хэндл с фильтром, читаем пакеты, модифицируем, отправляем обратно.

import (
	"fmt"
	"syscall"
	"unsafe"
)

// Слой WinDivert (priority повыше — перехватываем до отправки в сеть)
const (
	winDivertLayerNetwork = 0
	winDivertFlagSniffOff = 0 // 0 = можем модифицировать (не sniff-only)
)

// WINDIVERT_ADDRESS — структура адреса пакета (упрощённая, нужные поля).
// Реальная структура WinDivert 2.x: Timestamp(8) + флаги/слои(битовые поля 8) + union 64 байта.
// Нам важно: направление (outbound/inbound) и интерфейсы для корректной переотправки.
// winDivertAddress — ТОЧНАЯ раскладка WINDIVERT_ADDRESS (80 байт). РАНЬШЕ структура
// была 72 байта и Flags стоял на смещении 8 (там лежит Layer=0), из-за чего
// outbound() ВСЕГДА возвращал false → весь исходящий трафик уходил в обработку
// входящих и обход НЕ ПРИМЕНЯЛСЯ ВООБЩЕ. Это и был корень "ничего не работает".
// Битовое поле WinDivert (UINT32 на смещении 8): Layer:8, Event:8, затем байт
// флагов на смещении 10 (Sniffed:1, Outbound:1, Loopback:1, Impostor:1, IPv6:1,
// IPChecksum:1, TCPChecksum:1, UDPChecksum:1), затем Reserved1:8.
type winDivertAddress struct {
	Timestamp int64    // смещение 0
	Layer     uint8    // смещение 8
	Event     uint8    // смещение 9
	Flags     uint8    // смещение 10 — здесь бит Outbound (0x02) и прочие
	Reserved1 uint8    // смещение 11
	Reserved2 uint32   // смещение 12
	Union     [64]byte // смещение 16..79 (данные сетевого слоя: IfIdx, SubIfIdx, …)
}

// биты байта флагов (смещение 10): Sniffed=0x01, Outbound=0x02, Loopback=0x04,
// Impostor=0x08, IPv6=0x10, IPChecksum=0x20, TCPChecksum=0x40, UDPChecksum=0x80.
func (a *winDivertAddress) outbound() bool { return a.Flags&0x02 != 0 }
func (a *winDivertAddress) setOutbound(v bool) {
	if v {
		a.Flags |= 0x02
	} else {
		a.Flags &^= 0x02
	}
}

type winDivert struct {
	dll          *syscall.LazyDLL
	procOpen     *syscall.LazyProc
	procRecv     *syscall.LazyProc
	procSend     *syscall.LazyProc
	procClose    *syscall.LazyProc
	procHelperCS *syscall.LazyProc // WinDivertHelperCalcChecksums
	handle       uintptr
}

const invalidHandle = ^uintptr(0)

// newWinDivert загружает WinDivert.dll и резолвит функции.
func newWinDivert() (*winDivert, error) {
	logStep("windivert", "загружаю WinDivert.dll (должна лежать рядом с .exe)")
	dll := syscall.NewLazyDLL("WinDivert.dll")
	if err := dll.Load(); err != nil {
		return nil, fmt.Errorf("не удалось загрузить WinDivert.dll: %w (положи WinDivert.dll и WinDivert64.sys рядом с symbiont-engine.exe)", err)
	}
	wd := &winDivert{
		dll:          dll,
		procOpen:     dll.NewProc("WinDivertOpen"),
		procRecv:     dll.NewProc("WinDivertRecv"),
		procSend:     dll.NewProc("WinDivertSend"),
		procClose:    dll.NewProc("WinDivertClose"),
		procHelperCS: dll.NewProc("WinDivertHelperCalcChecksums"),
		handle:       invalidHandle,
	}
	logStep("windivert", "WinDivert.dll загружена, функции зарезолвлены")
	return wd, nil
}

// open открывает хэндл перехвата по фильтру (например, "outbound and tcp.DstPort == 443").
func (wd *winDivert) open(filter string) error {
	logStep("windivert", "открываю хэндл, фильтр: "+filter)
	fb, err := syscall.BytePtrFromString(filter)
	if err != nil {
		return fmt.Errorf("плохой фильтр: %w", err)
	}
	// WinDivertOpen(filter, layer, priority, flags)
	h, _, callErr := wd.procOpen.Call(
		uintptr(unsafe.Pointer(fb)),
		uintptr(winDivertLayerNetwork),
		uintptr(0), // priority 0
		uintptr(winDivertFlagSniffOff),
	)
	if h == invalidHandle {
		return fmt.Errorf("WinDivertOpen не удался (нужны права администратора при первом запуске драйвера, и WinDivert не должен быть заблокирован антивирусом): %v", callErr)
	}
	wd.handle = h
	logStep("windivert", "хэндл открыт успешно")
	return nil
}

// recv читает один пакет. Возвращает срез данных пакета и его адрес.
func (wd *winDivert) recv(buf []byte) (int, *winDivertAddress, error) {
	var addr winDivertAddress
	var readLen uint32
	r, _, callErr := wd.procRecv.Call(
		wd.handle,
		uintptr(unsafe.Pointer(&buf[0])),
		uintptr(len(buf)),
		uintptr(unsafe.Pointer(&readLen)),
		uintptr(unsafe.Pointer(&addr)),
	)
	if r == 0 {
		return 0, nil, fmt.Errorf("WinDivertRecv ошибка: %v", callErr)
	}
	return int(readLen), &addr, nil
}

// send отправляет пакет обратно в сеть (после возможной модификации).
func (wd *winDivert) send(data []byte, addr *winDivertAddress) error {
	// пересчитать контрольные суммы после модификации
	wd.procHelperCS.Call(
		uintptr(unsafe.Pointer(&data[0])),
		uintptr(len(data)),
		uintptr(unsafe.Pointer(addr)),
		uintptr(0),
	)
	return wd.sendRaw(data, addr)
}

// sendRaw отправляет пакет БЕЗ пересчёта контрольных сумм.
// Нужно для fake-пакетов с намеренно «битой» суммой (сервер их отбросит).
func (wd *winDivert) sendRaw(data []byte, addr *winDivertAddress) error {
	var sendLen uint32
	r, _, callErr := wd.procSend.Call(
		wd.handle,
		uintptr(unsafe.Pointer(&data[0])),
		uintptr(len(data)),
		uintptr(unsafe.Pointer(&sendLen)),
		uintptr(unsafe.Pointer(addr)),
	)
	if r == 0 {
		return fmt.Errorf("WinDivertSend ошибка: %v", callErr)
	}
	return nil
}

func (wd *winDivert) close() {
	if wd.handle != invalidHandle {
		wd.procClose.Call(wd.handle)
		wd.handle = invalidHandle
		logStep("windivert", "хэндл закрыт")
	}
}
