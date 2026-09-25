# -*- coding: utf-8 -*-
"""Поиск компилятора и переносимый таймер для сгенерированных бенчей.

Раньше путь к clang был вписан в исходник константой, а замер времени шёл через
`QueryPerformanceCounter` — репозиторий не запускался нигде, кроме одной машины.
Целевая аудитория сидит на Linux и macOS, поэтому обе вещи вынесены сюда.
"""
from __future__ import annotations

import os
import shutil
import sys

# Кандидаты по умолчанию: сначала то, что в PATH, потом типовые места установки.
_FALLBACKS = [
    r'C:\Program Files\LLVM\bin\clang.exe',
    '/usr/bin/clang',
    '/usr/local/bin/clang',
    '/opt/homebrew/opt/llvm/bin/clang',
]


def find_clang():
    """Путь к компилятору. Переопределяется переменной окружения PARETO_CLANG."""
    env = os.environ.get('PARETO_CLANG')
    if env:
        if not os.path.exists(env):
            raise SystemExit('PARETO_CLANG указывает в никуда: ' + env)
        return env
    for name in ('clang', 'clang-19', 'clang-18', 'clang-17', 'cc'):
        found = shutil.which(name)
        if found:
            return found
    for path in _FALLBACKS:
        if os.path.exists(path):
            return path
    raise SystemExit(
        'не нашёл clang. Поставь его или укажи путь: PARETO_CLANG=/путь/к/clang')


def find_mlir_tool(name):
    """mlir-opt / mlir-translate. Путь к каталогу задаётся через MLIR_BIN.

    Готовых сборок MLIR под Windows нет, его собирают руками, поэтому каталог
    почти всегда лежит вне PATH и его приходится указывать явно.
    """
    base = os.environ.get('MLIR_BIN')
    exe = name + ('.exe' if sys.platform.startswith('win') else '')
    if base:
        cand = os.path.join(base, exe)
        if os.path.exists(cand):
            return cand
        raise SystemExit('в MLIR_BIN нет {}: {}'.format(exe, base))
    found = shutil.which(name)
    if found:
        return found
    raise SystemExit(
        'не нашёл {0}. Укажи каталог сборки MLIR: MLIR_BIN=/путь/llvm/build/bin'.format(name))


def link_flags():
    """На Unix математика живёт в отдельной библиотеке, на Windows она внутри."""
    return [] if sys.platform.startswith('win') else ['-lm']


# Кусок C, который даёт монотонные наносекунды на всех трёх системах.
TIMER_C = r'''
#if defined(_WIN32)
  #include <windows.h>
  static double now_ns(void) {
    LARGE_INTEGER fr, t;
    QueryPerformanceFrequency(&fr);
    QueryPerformanceCounter(&t);
    return (double)t.QuadPart * 1e9 / (double)fr.QuadPart;
  }
#else
  #include <time.h>
  static double now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec * 1e9 + (double)ts.tv_nsec;
  }
#endif
'''


def fma_flags():
    """Флаги, включающие аппаратную FMA, — они зависят от архитектуры.

    `-mfma` существует только на x86: на arm64 clang отвергает его целиком, и прогон
    падал на macOS-раннере CI («unsupported option '-mfma' for target arm64»). На ARM
    FMA входит в базовый набор инструкций, отдельный флаг не нужен и не существует.
    """
    import platform
    machine = platform.machine().lower()
    if machine in ('arm64', 'aarch64'):
        return []
    return ['-mfma']
