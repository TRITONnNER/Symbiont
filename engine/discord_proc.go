//go:build windows

package main

import (
	"strings"
	"syscall"
	"unsafe"
)

// Определение процессов Discord по имени → PID. Нужно, чтобы при старте движка
// (когда Discord УЖЕ запущен) точно найти и разорвать ИМЕННО его старые соединения
// — по владельцу сокета (PID), а не угадывать по IP. Это и есть «как у zapret»:
// после разрыва Discord переподключается, и новое рукопожатие идёт через обход.

var (
	kernel32                     = syscall.NewLazyDLL("kernel32.dll")
	procCreateToolhelp32Snapshot = kernel32.NewProc("CreateToolhelp32Snapshot")
	procProcess32FirstW          = kernel32.NewProc("Process32FirstW")
	procProcess32NextW           = kernel32.NewProc("Process32NextW")
	procCloseHandleK             = kernel32.NewProc("CloseHandle")
)

const th32csSnapProcess = 0x00000002

// PROCESSENTRY32W (Unicode). Раскладка обязана совпадать с WinAPI.
type processEntry32W struct {
	Size            uint32
	Usage           uint32
	ProcessID       uint32
	DefaultHeapID   uintptr
	ModuleID        uint32
	Threads         uint32
	ParentProcessID uint32
	PriClassBase    int32
	Flags           uint32
	ExeFile         [260]uint16
}

// discordProcessPIDs — набор PID всех процессов Discord.exe (Electron поднимает
// несколько; сетевые сокеты держит главный/сетевой). Пусто, если Discord не
// запущен или снапшот не удался — тогда PID-резет просто ничего не делает.
func discordProcessPIDs() map[uint32]bool {
	pids := map[uint32]bool{}
	snap, _, _ := procCreateToolhelp32Snapshot.Call(th32csSnapProcess, 0)
	if snap == 0 || snap == ^uintptr(0) { // INVALID_HANDLE_VALUE
		return pids
	}
	defer procCloseHandleK.Call(snap)

	var pe processEntry32W
	pe.Size = uint32(unsafe.Sizeof(pe))
	ret, _, _ := procProcess32FirstW.Call(snap, uintptr(unsafe.Pointer(&pe)))
	for ret != 0 {
		name := syscall.UTF16ToString(pe.ExeFile[:])
		if strings.EqualFold(name, "Discord.exe") {
			pids[pe.ProcessID] = true
		}
		ret, _, _ = procProcess32NextW.Call(snap, uintptr(unsafe.Pointer(&pe)))
	}
	return pids
}
