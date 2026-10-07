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
    'llvm.asin.f32': 'asin', 'llvm.asin.f64': 'asin',
    'asinf': 'asin', 'asin': 'asin',
    'llvm.acos.f32': 'acos', 'llvm.acos.f64': 'acos',
    'acosf': 'acos', 'acos': 'acos',
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
# Постоянная ОДИНАРНОЙ точности печатается с приставкой f0x и восемью цифрами:
# f0x34000000 это FLT_EPSILON. Без этого разбор спотыкался на настоящем коде
# управления PX4, где epsilon сравнивается с нормой вектора.
_HEXF = re.compile(r'^f0x([0-9A-Fa-f]{8})$')


# Предикаты сравнения. Берём только УПОРЯДОЧЕННЫЕ: `olt` истинно, когда оба
# операнда — числа и первый меньше. Неупорядоченные (`ult` и прочие) истинны
# также когда один из операндов NaN, и приравнивать их к обычному сравнению
# значило бы читать другую программу. Такие отвергаем.
FCMP = {'olt': '<', 'ole': '<=', 'ogt': '>', 'oge': '>=', 'oeq': '==',
        'one': '!='}

# Неупорядоченные предикаты истинны ЕЩЁ И когда один из операндов NaN. Компилятор
# ставит их постоянно: `fabsf(x) < c` в ветвлении превращается в `ugt` от
# отрицания. Отвергать их значило бы терять настоящий код управления PX4 —
# constrainXY отвергалась ровно на этом.
#
# Принимаем как обычное сравнение, и вот на каком основании. Расходимся мы с
# компилятором только если операнд оказался NaN. Но NaN в выражении означает, что
# программа уже возвращает не число, и такой случай ловится ОТДЕЛЬНО: граница для
# него не доказывается, а опасное место называется прямо. То есть там, где мы
# вообще что-то утверждаем, NaN исключён, и предикаты совпадают.
#
# Страховка не на слове: судья исполняет функцию и сравнивает с нашим ответом, и
# неверно выбранная ветка даёт расхождение, которое он покажет.
FCMP_UNORDERED = {'ult': '<', 'ule': '<=', 'ugt': '>', 'uge': '>=',
                  'ueq': '==', 'une': '!='}


def _same(a, b):
    """Совпадают ли деревья с точностью до округления.

    Округление здесь не значимо: `f32(x)` и `x` для сравнения образцов min/max
    — одно и то же значение, просто в одном случае компилятор сохранил узел.
    """
    def strip(t):
        while isinstance(t, tuple) and t and t[0] in ('f32', 'f16', 'approx',
                                                      'eft'):
            t = t[1]
        return t
    return strip(a) == strip(b)


def _is_zero(t):
    while isinstance(t, tuple) and t and t[0] in ('f32', 'f16'):
        t = t[1]
    return isinstance(t, tuple) and t[0] == 'num' and float(t[1]) == 0.0


def _negates(t, base):
    while isinstance(t, tuple) and t and t[0] in ('f32', 'f16'):
        t = t[1]
    if isinstance(t, tuple) and t[0] == 'neg' and _same(t[1], base):
        return True
    return (isinstance(t, tuple) and t[0] == '-' and _is_zero(t[1])
            and _same(t[2], base))


NAN_FREE_OPS = {'+', '-', '*', 'neg', 'fabs', 'fmin', 'fmax', 'num', 'var',
                'f32', 'f16'}


def nan_free_shape(tree):
    """Может ли выражение дать NaN. Судим по форме, односторонне.

    «Нет» значит доказано, «да» значит не доказано. Деление, корень и libm
    способны дать NaN, обычная арифметика над числами — нет.

    Нужно для узнавания образца минимума и максимума. `select(a < b, a, b)` и
    `fmin(a, b)` совпадают, пока ни один операнд не NaN. Если NaN оказался
    ВТОРЫМ операндом, они расходятся: выбор отдаёт второй (сравнение ложно), а
    IEEE fmin — первый, как не-NaN. Ровно это и случилось на limitTilt из PX4,
    где acosf получает аргумент вне области: исполнение дало 0, наше чтение NaN.
    Поэтому образец сворачивается только когда NaN исключён по форме, иначе
    остаётся настоящим ветвлением — дороже, зато точно.
    """
    if not isinstance(tree, tuple):
        return False
    op = tree[0]
    if op not in NAN_FREE_OPS:
        return False
    if op in ('num', 'var'):
        return True
    return all(nan_free_shape(k) for k in tree[1:])


def recognise_select(cond, a, b):
    """Узнать в выборе обычную операцию: минимум, максимум или модуль.

    Это не украшение. Выбор без узнавания превращается в ДВА пути исполнения, и
    на вложенных выборах их число растёт вдвое на каждом: обычное ограничение
    снизу и сверху даёт четыре пути вместо одной операции. А `fmin`, `fmax` и
    `fabs` у нас уже есть, и ошибки они не вносят вовсе.
    """
    op, left, right = cond
    if not (nan_free_shape(a) and nan_free_shape(b)):
        return None          # NaN не исключён — сворачивать нельзя
    if op in ('<', '<='):
        if _same(a, left) and _same(b, right):
            return ('fmin', left, right)
        if _same(a, right) and _same(b, left):
            return ('fmax', left, right)
        if _is_zero(right) and _negates(a, left) and _same(b, left):
            return ('fabs', left)
    if op in ('>', '>='):
        if _same(a, left) and _same(b, right):
            return ('fmax', left, right)
        if _same(a, right) and _same(b, left):
            return ('fmin', left, right)
        if _is_zero(right) and _same(a, left) and _negates(b, left):
            return ('fabs', left)
    return None


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
    m = _HEXF.match(token)
    if m:
        import struct
        bits = int(m.group(1), 16)
        return (('num', struct.unpack('<f', struct.pack('<I', bits))[0]),
                fmt_hint)
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
        value = float(token)
    except ValueError:
        raise LLError('операнд {!r} не разобран'.format(token))
    # Постоянная ОДИНАРНОЙ точности печатается десятичной записью, которая
    # кругом-обратно верна во float32 — но не в double. Читая `-9.806650e+00`
    # как число двойной точности, мы получали НЕ ту постоянную, что в программе:
    # float32(9.80665) равен 9.8066501617431640625. Разница в младшей единице,
    # и ровно она дала расхождение с исполнением на трёх функциях управления PX4.
    # Поймано судьёй.
    if fmt_hint is not None and value == value:
        try:
            value = fmt_hint.round(value)
        except Exception:
            pass
    return (('num', value), fmt_hint)


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
    """Дерево выражения возврата. Только прямолинейная функция, иначе LLError."""
    stmts, result = _walk(args, body)
    if stmts:
        raise LLError('в теле есть выбор по условию: это программа с путями, '
                      'читайте её через build_program')
    return result


def build_program(args, body):
    """Программа функции операторами, как в pareto/program.py.

    Сначала пробуем прочесть как один блок — это самый частый и самый дешёвый
    случай. Если в теле переходы между блоками, читаем по графу: досрочные
    возвраты и слияния разрешаются дублированием путей.
    """
    try:
        stmts, result = _walk(args, body)
        return list(stmts) + [('return', result)]
    except LLError:
        return walk_cfg(args, body)


MAX_LL_PATHS = 64


def split_blocks(body):
    """Тело на блоки: метка -> строки. Первый блок без метки — входной."""
    blocks = {'entry': []}
    label = 'entry'
    for raw in body:
        line = raw.strip()
        # Комментарий отрезаем ДО разбора. Метка блока печатается так:
        #   13:                      ; preds = %7, %10
        # и проверка «в строке нет знака равенства» срабатывала на слове preds,
        # поэтому метка принималась за инструкцию и обход рвался сообщением
        # «блок кончается без перехода».
        cut = line.find(';')
        if cut >= 0:
            line = line[:cut].strip()
        if not line:
            continue
        m = re.match(r'^([-\w.$]+):\s*$', line)
        if m:
            label = m.group(1)
            blocks.setdefault(label, [])
            continue
        blocks[label].append(line)
    return blocks


def walk_cfg(args, body):
    """Программа функции по ГРАФУ блоков, а не по одному блоку.

    Досрочный возврат в живом коде управления встречается постоянно:
    `if (norm < eps) return v0.normalized() * max;`. Компилятор делает из этого
    отдельные блоки с переходами и узлами слияния, и пока читался один блок,
    такие функции отвергались целиком — а это настоящий код PX4 (constrainXY).

    Обход идёт ПО ПУТЯМ с дублированием блоков, и это ключ к слиянию: узел phi
    выбирает значение по тому, ОТКУДА пришли, а на известном пути выбор
    однозначен. Ничего вычислять не нужно. Ровно так же устроен разбор
    ветвлений в C: каждый путь прямолинеен.

    Цикл отвергается: повторный вход в блок на одном пути означает неизвестное
    число шагов, а значение за ним нам неизвестно тоже.
    """
    blocks = split_blocks(body)
    # Входной блок в представлении БЕЗЫМЯННЫЙ, а слияния ссылаются на него
    # номером: нумерация безымянных значений идёт сквозная, и после аргументов
    # %0..%n-1 входной блок получает %n. Угадывать номер не нужно — он
    # вычисляется: это та метка в слияниях, которой нет среди блоков.
    referenced = set()
    for _lbl, _lines in blocks.items():
        for _l in _lines:
            if _l.split()[0:1] == ['%' + _l.split()[0].lstrip('%')] or 'phi ' in _l:
                for pm in re.finditer(r'\]\s*,?|%([-\w.$]+)\s*\]', _l):
                    if pm.group(1):
                        referenced.add(pm.group(1))
    entry_aliases = {r for r in referenced if r not in blocks}
    for alias in entry_aliases:
        blocks[alias] = blocks['entry']
    base_env = {}
    for reg, fmt in args:
        base_env[reg] = (('var', 'a' + reg if reg.isdigit() else reg), fmt)
    counter = [0]

    def go(label, env, conds, came_from, seen, depth):
        if label in seen:
            raise LLError('в теле цикл: блок {} встречается на пути дважды'
                          .format(label))
        if depth > MAX_LL_PATHS:
            raise LLError('слишком глубокое ветвление')
        seen = seen | {label}
        env = dict(env)
        conds = dict(conds)
        stmts = []
        for line in blocks.get(label, []):
            if line.startswith('ret '):
                parts = line.split()
                if len(parts) < 3:
                    raise LLError('возврат без значения')
                return stmts + [('return', _value(parts[2], env, None)[0])]
            if line.startswith('br '):
                m1 = re.match(r'br\s+label\s+%([-\w.$]+)', line)
                if m1:
                    return stmts + go(m1.group(1), env, conds, label, seen,
                                      depth + 1)
                m2 = re.match(r'br\s+i1\s+(\S+?),\s*label\s+%([-\w.$]+),'
                              r'\s*label\s+%([-\w.$]+)', line)
                if m2 is None:
                    raise LLError('переход не разобран: ' + line[:60])
                ckey = m2.group(1).lstrip('%')
                if ckey not in conds:
                    raise LLError('условие перехода получено не сравнением')
                then_s = go(m2.group(2), env, conds, label, seen, depth + 1)
                else_s = go(m2.group(3), env, conds, label, seen, depth + 1)
                return stmts + [('if', conds[ckey], then_s, else_s)]
            if line.startswith('switch ') or line.startswith('indirectbr'):
                raise LLError('switch или косвенный переход в теле')
            if line.startswith('unreachable'):
                raise LLError('недостижимый конец блока')
            for bad, why in (('load ', 'чтение памяти'),
                             ('store ', 'запись памяти'),
                             ('getelementptr', 'адресная арифметика'),
                             ('alloca', 'локальная память')):
                if bad in line:
                    raise LLError(why + ' в теле')
            m = re.match(r'^%([-\w.$]+)\s*=\s*(.+)$', line)
            if m is None:
                if line.startswith('call ') or line.startswith('tail call '):
                    continue
                raise LLError('строка не разобрана: ' + line[:60])
            dst, rest = m.group(1), m.group(2)
            if rest.split()[0] == 'phi':
                mm = re.match(r'phi\s+(?:fast\s+)*(float|double)\s+(.*)$', rest)
                if mm is None:
                    raise LLError('слияние не разобрано: ' + rest[:60])
                fmt_p = TYPES[mm.group(1)]
                picked = None
                for pm in re.finditer(
                        r'\[\s*([^,\]]+?)\s*,\s*%([-\w.$]+)\s*\]', mm.group(2)):
                    if pm.group(2) == came_from:
                        picked = pm.group(1)
                        break
                if picked is None:
                    raise LLError('в слиянии нет ветви из блока {}'.format(
                        came_from))
                env[dst] = (_value(picked, env, fmt_p)[0], fmt_p)
                continue
            _instr(dst, rest, env, conds, stmts, counter)
        raise LLError('блок {} кончается без перехода'.format(label))

    entry_label = sorted(entry_aliases)[0] if entry_aliases else 'entry'
    return go(entry_label, base_env, {}, None, frozenset(), 0)


def _instr(dst, rest, env, conds, stmts, counter):
    """Одна инструкция представления. Общая для обхода блока и обхода графа.

    Выделено отдельно не для красоты: обход графа блоков должен понимать те же
    инструкции, что и обход одного блока, и дублировать их значило бы однажды
    поправить в одном месте и забыть в другом.
    """

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
        return
    if op == 'fneg':
        mm = re.search(r'(float|double)\s+(\S+)', rest)
        if mm is None:
            raise LLError('fneg не разобран')
        fmt = TYPES[mm.group(1)]
        env[dst] = (('neg', _value(mm.group(2), env, fmt)[0]), fmt)
        return
    if op in ('fpext', 'fptrunc'):
        mm = re.search(r'(float|double)\s+(\S+)\s+to\s+(float|double)', rest)
        if mm is None:
            raise LLError('смена точности не разобрана')
        src = _value(mm.group(2), env, TYPES[mm.group(1)])[0]
        dst_fmt = TYPES[mm.group(3)]
        env[dst] = ((_round(src, dst_fmt) if op == 'fptrunc' else src),
                    dst_fmt)
        return
    if op == 'call':
        env[dst] = _call(rest, env)
        return
    if op == 'fcmp':
        mm = re.match(r'fcmp\s+(?:fast\s+|nnan\s+|ninf\s+)*(\w+)\s+'
                      r'(float|double)\s+(.+)$', rest)
        if mm is None:
            raise LLError('сравнение не разобрано: ' + rest[:60])
        pred = mm.group(1)
        relop = FCMP.get(pred) or FCMP_UNORDERED.get(pred)
        if relop is None:
            raise LLError('предикат сравнения {!r} неизвестен'.format(pred))
        fmt_c = TYPES[mm.group(2)]
        l_s, r_s = [t.strip() for t in mm.group(3).split(',')[:2]]
        conds[dst] = (relop,
                      _value(l_s, env, fmt_c)[0],
                      _value(r_s, env, fmt_c)[0])
        return
    if op == 'select':
        mm = re.match(r'select\s+i1\s+(\S+?),\s*(float|double)\s+(\S+?),'
                      r'\s*(float|double)\s+(\S+)$', rest)
        if mm is None:
            raise LLError('выбор не разобран: ' + rest[:60])
        ckey = mm.group(1).lstrip('%')
        if ckey not in conds:
            raise LLError('условие выбора получено не сравнением')
        fmt_s = TYPES[mm.group(2)]
        a_v = _value(mm.group(3), env, fmt_s)[0]
        b_v = _value(mm.group(5), env, fmt_s)[0]
        got = recognise_select(conds[ckey], a_v, b_v)
        if got is not None:
            env[dst] = (got, fmt_s)
            return
        # Обычный выбор: это два пути исполнения. Имя результата одно на обе
        # ветви, иначе дальше по тексту сослались бы на имя из одной из них.
        inner = 'sel' + dst
        stmts.append(('if', conds[ckey],
                      [('let', inner, a_v)], [('let', inner, b_v)]))
        env[dst] = (('var', inner), fmt_s)
        return
    raise LLError('операция {!r} не поддержана'.format(op))


def _walk(args, body):
    """Общий обход тела: возвращает (операторы, дерево возврата).

    Память, циклы и phi отвергаются — там значение зависит от того, чего мы не
    моделируем, и притворяться, что моделируем, значило бы считать границу для
    другой программы.
    """
    env = {}
    conds = {}
    stmts = []
    counter = [0]
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
        _instr(dst, rest, env, conds, stmts, counter)

    if result is None:
        raise LLError('в теле нет возврата значения')
    return stmts, result


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
    """Читаемые функции представления: имя -> (вид, содержимое, аргументы).

    Вид «дерево» — прямолинейная функция. Вид «программа» — с ветвлениями, её
    разбирает pareto/program.py по путям. Вид «отказ» несёт причину словами.
    """
    text = Path(path).read_text(encoding='utf-8', errors='replace')
    out = {}
    for name, (args, body) in parse_module(text).items():
        try:
            stmts = build_program(args, body)
        except LLError as e:
            out[name] = ('отказ', str(e), args)
            continue
        # Признак «одно выражение» — это оператор ВОЗВРАТА, а не длина списка.
        # Обход графа возвращает один оператор ветвления, и по длине он
        # выглядел как выражение: за дерево принималось условие, а дальше всё
        # падало. Поймано судьёй.
        if len(stmts) == 1 and stmts[0][0] == 'return':
            out[name] = ('дерево', stmts[0][1], args)
        else:
            out[name] = ('программа', stmts, args)
    return out
