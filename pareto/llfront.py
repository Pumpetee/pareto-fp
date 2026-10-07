# -*- coding: utf-8 -*-
"""Чтение промежуточного представления LLVM вместо текста на C.

Зачем это нужно. Свой разборщик C мы довели до половины функций в чистом C, но на
C++ он беспомощен принципиально: шаблоны, классы, перегрузка операторов,
наследование. Полётная математика PX4 написана ровно так — `matrix::Vector3f` это
шаблон с наследованием и `operator()`. Писать свой разбор C++ значит писать
полкомпилятора и проиграть.

Правильный ход — не разбирать C++ самим, а читать то, что выдал компилятор.
Шаблоны разворачивает clang, типы выводит clang, препроцессор выполняет clang.
Нам остаётся арифметика, а она в этом представлении видна прямее, чем в исходнике:

    %4 = fmul float %0, %0
    %5 = fmul float %1, %1
    %6 = fadd float %4, %5
    %9 = call float @llvm.sqrt.f32(float %8)

Это и есть дерево `sqrt(x*x + y*y + ...)` в одинарной точности. Так читается
любой язык, который компилирует LLVM: C, C++, Rust, Swift.

Про верность. Без `-ffast-math` компилятор ОБЯЗАН сохранять смысл вещественных
операций, а `-ffp-contract=off` запрещает сливать умножение со сложением в одну
операцию. Поэтому представление после оптимизации — честная запись той же
программы. Более того, для утверждений о безопасности это ЛУЧШЕ исходника: мы
считаем границу для того, что реально исполнится, а не для того, что написано.

Чего здесь нет намеренно: памяти и циклов. Если в теле остались load, store,
getelementptr или phi, функция отвергается — значение за обращением к памяти нам
неизвестно, и притворяться, что известно, значило бы считать границу для другой
программы. На -O2 у расчётных функций память обычно уходит сама.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from pareto.precision import FLOAT32, FLOAT64

TYPES = {'float': FLOAT32, 'double': FLOAT64}

# Одноместные вызовы LLVM и libm, которые мы знаем как операции.
CALL1 = {
    'llvm.sqrt.f32': 'sqrt', 'llvm.sqrt.f64': 'sqrt',
    'sqrtf': 'sqrt', 'sqrt': 'sqrt',
    'llvm.fabs.f32': 'fabs', 'llvm.fabs.f64': 'fabs',
    'fabsf': 'fabs', 'fabs': 'fabs',
    'llvm.exp.f32': 'exp', 'llvm.exp.f64': 'exp', 'expf': 'exp', 'exp': 'exp',
    'llvm.log.f32': 'log', 'llvm.log.f64': 'log', 'logf': 'log', 'log': 'log',
    'llvm.sin.f32': 'sin', 'llvm.sin.f64': 'sin', 'sinf': 'sin', 'sin': 'sin',
    'llvm.cos.f32': 'cos', 'llvm.cos.f64': 'cos', 'cosf': 'cos', 'cos': 'cos',
    'atanf': 'atan', 'atan': 'atan',
}

CALL2 = {
    'llvm.minnum.f32': 'fmin', 'llvm.minnum.f64': 'fmin',
    'llvm.maxnum.f32': 'fmax', 'llvm.maxnum.f64': 'fmax',
    'fminf': 'fmin', 'fmin': 'fmin', 'fmaxf': 'fmax', 'fmax': 'fmax',
    'atan2f': 'atan2', 'atan2': 'atan2',
    'hypotf': 'hypot', 'hypot': 'hypot',
    'llvm.pow.f32': 'pow', 'llvm.pow.f64': 'pow', 'powf': 'pow', 'pow': 'pow',
}

CALL3 = {
    'llvm.fmuladd.f32': 'fma', 'llvm.fmuladd.f64': 'fma',
    'llvm.fma.f32': 'fma', 'llvm.fma.f64': 'fma',
    'fmaf': 'fma', 'fma': 'fma',
}

BINOP = {'fadd': '+', 'fsub': '-', 'fmul': '*', 'fdiv': '/'}

_DEFINE = re.compile(r'^define[^@]*@(?P<name>"[^"]+"|[\w.$]+)\s*\((?P<args>[^)]*)\)')
_ARG = re.compile(r'(float|double)\b[^,]*?%(?P<reg>[\w.]+)')
_HEX = re.compile(r'^0x([0-9A-Fa-f]+)$')


class LLError(ValueError):
    """Функция не читается этим способом. Причина — в сообщении."""


def _value(token, env, fmt_hint):
    """Операнд: регистр из окружения либо числовая постоянная.

    Отдаётся пара (дерево, формат). Формат нужен, чтобы не навешивать округление
    на значение, которое уже лежит в этом формате: `llvm.sqrt.f32` возвращает
    float32, и повторное округление добавляло бы в границу половину младшей
    единицы из ниоткуда. Граница от этого не становится неверной, но становится
    хуже, а завышать её без причины значит хуже продавать.
    """
    token = token.strip()
    if token.startswith('%'):
        key = token[1:]
        if key not in env:
            raise LLError('значение %{} приходит из места, которое мы не '
                          'моделируем'.format(key))
        return env[key]
    if token in ('zeroinitializer', 'undef', 'poison'):
        raise LLError('неопределённое значение в выражении')
    m = _HEX.match(token)
    if m:
        # LLVM печатает вещественные постоянные шестнадцатеричным образцом
        # двойной точности — даже для float, значение при этом точно
        # представимо в float.
        import struct
        bits = int(m.group(1), 16)
        return (('num', struct.unpack('<d', struct.pack('<Q', bits))[0]),
                fmt_hint)
    try:
        return (('num', float(token)), fmt_hint)
    except ValueError:
        raise LLError('операнд {!r} не разобран'.format(token))


def parse_module(text):
    """Все функции модуля: имя -> (аргументы, тело строками)."""
    out = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = _DEFINE.match(lines[i].strip())
        if not m:
            i += 1
            continue
        name = m.group('name').strip('"')
        args = [(mm.group('reg'), TYPES[mm.group(1)])
                for mm in _ARG.finditer(m.group('args'))]
        body, i = [], i + 1
        while i < len(lines) and lines[i].strip() != '}':
            body.append(lines[i])
            i += 1
        out[name] = (args, body)
        i += 1
    return out


def build(args, body):
    """Дерево выражения возврата по телу функции. Иначе LLError.

    Поддерживаются прямолинейные функции: ровно один блок и один `ret`. Память,
    ветвления и phi отвергаются — там значение зависит от того, чего мы не
    моделируем.
    """
    env = {}
    for reg, fmt in args:
        env[reg] = (('var', 'a' + reg if reg.isdigit() else reg), fmt)
    result = None

    for raw in body:
        line = raw.strip()
        if not line or line.startswith(';'):
            continue
        if line.endswith(':') or line.startswith('br ') or line.startswith('phi'):
            raise LLError('в теле есть ветвление или цикл')
        if re.match(r'^\w+:\s*;', line):
            raise LLError('в теле есть ветвление или цикл')
        if line.startswith('ret '):
            parts = line.split()
            if len(parts) < 3:
                raise LLError('возврат без значения')
            result = _value(parts[2], env, None)[0]
            continue
        for bad, why in (('load ', 'чтение памяти'), ('store ', 'запись памяти'),
                         ('getelementptr', 'адресная арифметика'),
                         ('alloca', 'локальная память'),
                         (' phi ', 'слияние путей'), ('switch ', 'switch'),
                         ('invoke ', 'вызов с раскруткой стека')):
            if bad in line:
                raise LLError(why + ' в теле')
        m = re.match(r'^%(?P<dst>[\w.]+)\s*=\s*(?P<rest>.+)$', line)
        if m is None:
            # Вызовы без результата (lifetime, memset) безвредны только если
            # ничего не читают. Проверено выше; остальное — отказ.
            if line.startswith('call ') or line.startswith('tail call '):
                continue
            raise LLError('строка не разобрана: ' + line[:60])
        dst, rest = m.group('dst'), m.group('rest')

        op = rest.split()[0]
        if op in ('tail', 'musttail', 'notail'):
            rest = rest.split(None, 1)[1]
            op = rest.split()[0]

        if op in BINOP:
            mm = re.match(r'\w+\s+(?:[\w()]+\s+)*?(float|double)\s+(.+)$', rest)
            if mm is None:
                raise LLError('арифметика не разобрана: ' + rest[:60])
            fmt = TYPES[mm.group(1)]
            a_s, b_s = [t.strip() for t in mm.group(2).split(',')[:2]]
            a, b = _value(a_s, env, fmt), _value(b_s, env, fmt)
            node = (BINOP[op], a[0], b[0])
            env[dst] = (_round(node, fmt), fmt)
            continue
        if op == 'fneg':
            mm = re.search(r'(float|double)\s+(\S+)', rest)
            if mm is None:
                raise LLError('fneg не разобран')
            fmt = TYPES[mm.group(1)]
            env[dst] = (('neg', _value(mm.group(2), env, fmt)[0]), fmt)
            continue
        if op in ('fpext', 'fptrunc'):
            mm = re.search(r'(float|double)\s+(\S+)\s+to\s+(float|double)', rest)
            if mm is None:
                raise LLError('смена точности не разобрана')
            src = _value(mm.group(2), env, TYPES[mm.group(1)])[0]
            dst_fmt = TYPES[mm.group(3)]
            env[dst] = ((_round(src, dst_fmt) if op == 'fptrunc' else src),
                        dst_fmt)
            continue
        if op == 'call':
            env[dst] = _call(rest, env)
            continue
        if op == 'select':
            raise LLError('select: выбор по условию пока не читается')
        raise LLError('операция {!r} не поддержана'.format(op))

    if result is None:
        raise LLError('в теле нет возврата значения')
    return result


def _round(node, fmt):
    """Надеть округление к узкому формату, если нужно."""
    from pareto.precision import round_op_for
    op = round_op_for(fmt)
    return node if op is None else (op, node)


def _call(rest, env):
    m = re.search(r'@([\w.$]+)\s*\((?P<args>.*)\)', rest)
    if m is None:
        raise LLError('вызов не разобран: ' + rest[:60])
    fname = m.group(1)
    ret_m = re.search(r'(float|double)\s+@', rest) or \
        re.search(r'call[^@]*?(float|double)', rest)
    fmt = TYPES[ret_m.group(1)] if ret_m else FLOAT64
    raw = m.group('args')
    operands = []
    depth = 0
    cur = ''
    for ch in raw:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        if ch == ',' and depth == 0:
            operands.append(cur)
            cur = ''
        else:
            cur += ch
    if cur.strip():
        operands.append(cur)
    vals = []
    for part in operands:
        mm = re.search(r'(float|double)\b[^%\-0-9]*(%[\w.]+|[-\w.+]+)', part)
        if mm is None:
            continue
        vals.append(_value(mm.group(2), env, TYPES[mm.group(1)])[0])
    # Округление навешивается ОБЯЗАТЕЛЬНО, и я это чуть не потерял. `llvm.sqrt.f32`
    # возвращает одинарную точность: сама операция и есть округление к float32.
    # Сняв его как «лишнее», я заставил модель считать корень в двойной точности
    # — то есть ЗАНИЗИЛ границу, а это уже не граница. Поймано на неправдоподобном
    # числе: перепись выходила «туже в 869 миллиардов раз».
    #
    # Двойного счёта тут нет: в нашей модели f32(sqrt(x)) это одно округление
    # точного корня к float32, ровно то, что делает IEEE-754.
    if fname in CALL1 and len(vals) == 1:
        return (_round((CALL1[fname], vals[0]), fmt), fmt)
    if fname in CALL2 and len(vals) == 2:
        return (_round((CALL2[fname], vals[0], vals[1]), fmt), fmt)
    if fname in CALL3 and len(vals) == 3:
        return (_round((CALL3[fname], vals[0], vals[1], vals[2]), fmt), fmt)
    raise LLError('вызов {} не из известного набора'.format(fname))


def compile_to_ll(path, clang, extra=(), cpp=None, opt='-O2'):
    """Скомпилировать исходник в представление LLVM.

    Уровень оптимизации нужен не для скорости, а чтобы ушли память и шаблонные
    обёртки: на -O0 тело состоит из alloca и load, и читать там нечего.
    Слияние умножения со сложением ЗАПРЕЩЕНО явно — иначе компилятор сольёт их в
    одну операцию, и мы посчитаем границу не для той программы.
    """
    src = Path(path)
    is_cpp = cpp if cpp is not None else src.suffix in ('.cpp', '.cc', '.cxx',
                                                        '.hpp', '.hh')
    out = src.with_suffix('.ll')
    cmd = [clang, str(src), opt, '-S', '-emit-llvm',
           '-ffp-contract=off', '-fno-vectorize', '-fno-slp-vectorize',
           '-fno-math-errno', '-o', str(out)]
    if is_cpp:
        cmd = [clang, '-x', 'c++', '-std=c++14'] + cmd[1:]
    cmd += list(extra)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise LLError('компилятор не собрал файл: ' +
                      (r.stderr or '').strip().splitlines()[-1][:200])
    return out


def demangle(name, clang_dir=None):
    """Человеческое имя функции из искажённого. Если не вышло — как есть."""
    exe = 'llvm-cxxfilt'
    if clang_dir:
        cand = Path(clang_dir) / ('llvm-cxxfilt.exe')
        if cand.exists():
            exe = str(cand)
    try:
        r = subprocess.run([exe, name], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except OSError:
        pass
    return name


def functions_of(path):
    """Читаемые функции представления: имя -> (аргументы, дерево) либо причина."""
    text = Path(path).read_text(encoding='utf-8', errors='replace')
    out = {}
    for name, (args, body) in parse_module(text).items():
        try:
            tree = build(args, body)
        except LLError as e:
            out[name] = ('отказ', str(e), args)
            continue
        out[name] = ('дерево', tree, args)
    return out
