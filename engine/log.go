package main

import (
	"fmt"
	"os"
	"sync"
	"time"
)

// Лог пишется и в консоль, и в файл (Симбионт читает файл).
// Подробно по шагам, БЕЗ дампа сырых байтов каждого пакета — читаемо.
// Есть авто-чистка: при старте старый большой лог обрезается, и во время
// работы лог не растёт бесконечно (ротация по размеру).

const (
	logMaxBytes   = 2 * 1024 * 1024 // 2 МБ — порог авто-чистки
	logKeepOnTrim = 256 * 1024      // сколько «хвоста» оставить при обрезке во время работы
)

var (
	logMu      sync.Mutex
	logFile    *os.File
	logPath    string
	logWritten int64 // сколько байт записали в текущий файл
)

func initLog(path string) {
	if path == "" {
		return
	}
	logPath = path

	// АВТО-ЧИСТКА при старте: если старый лог большой — начинаем заново.
	if fi, err := os.Stat(path); err == nil && fi.Size() > logMaxBytes {
		_ = os.Remove(path)
		fmt.Printf("(старый лог %d КБ удалён — начинаю чистый)\n", fi.Size()/1024)
	}

	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	if err != nil {
		fmt.Printf("не удалось открыть лог-файл %s: %v\n", path, err)
		return
	}
	logFile = f
	if fi, err := f.Stat(); err == nil {
		logWritten = fi.Size()
	}
	logStep("log", "лог-файл открыт: "+path+" (авто-чистка при >2МБ)")
}

func ts() string { return time.Now().Format("2006-01-02T15:04:05.000") }

// logStep — одна строка про конкретный шаг: [категория] сообщение
func logStep(cat, msg string) {
	logMu.Lock()
	defer logMu.Unlock()
	line := fmt.Sprintf("%s  [%s] %s\n", ts(), cat, msg)
	fmt.Print(line)
	if logFile != nil {
		n, _ := logFile.WriteString(line)
		logWritten += int64(n)
		// РОТАЦИЯ во время работы: если файл перерос порог — обрезаем,
		// оставляя только последний «хвост» (старое не нужно).
		if logWritten > logMaxBytes {
			rotateLocked()
		}
	}
}

func logStepf(cat, format string, args ...interface{}) {
	logStep(cat, fmt.Sprintf(format, args...))
}

// rotateLocked обрезает лог, оставляя последние logKeepOnTrim байт.
// Вызывается уже под logMu.
func rotateLocked() {
	if logFile == nil || logPath == "" {
		return
	}
	// читаем хвост
	logFile.Sync()
	data, err := os.ReadFile(logPath)
	if err != nil {
		return
	}
	tail := data
	if len(data) > logKeepOnTrim {
		tail = data[len(data)-logKeepOnTrim:]
	}
	logFile.Close()
	// перезаписываем файл только хвостом
	f, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0644)
	if err != nil {
		return
	}
	header := fmt.Sprintf("%s  [log] (лог обрезан авто-чисткой, оставлен хвост ~%d КБ)\n", ts(), len(tail)/1024)
	f.WriteString(header)
	f.Write(tail)
	logFile = f
	logWritten = int64(len(header) + len(tail))
}

func closeLog() {
	if logFile != nil {
		logFile.Close()
	}
}
