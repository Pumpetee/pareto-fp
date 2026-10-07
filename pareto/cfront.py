# -*- coding: utf-8 -*-
"""Чтение настоящего файла на C: функция целиком, а не выписанная из неё формула.

Это тот самый шаг, без которого инструмент нельзя было навести на чужой код. До
него человек обязан был сам найти в файле нужное выражение, выписать его строкой и
руками указать диапазоны. Теперь он передаёт файл.

Границы честности. Подмножество C здесь узкое и намеренно узкое:

    double f(double x, float y) {
        double t = x * x;               объявление с инициализацией
        t = t + 1.0;                    переприсваивание
        if (t > 0.0) { ... } else ...   ветвление, включая && и ||
        for (int i = 0; i < 4; i++)     цикл с известным числом шагов
        return sqrt(t) - x;             возврат
    }

Всё, что за этими рамками — указатели, массивы с переменным индексом, вызовы
чужих функций, `while`, `goto`, целочисленная арифметика как часть расчёта —
**отклоняется с указанием строки**. Это главное требование к такому разборщику:
он обязан молчать только про то, что действительно понял. Разборщик, который на
непонятной конструкции делает вид, что понял, выдаёт границу ошибки не для той
программы, которая лежит в файле, — а это хуже отсутствия инструмента.

Типы считаются по правилам C, а не «на глаз». В `float a, b; a*b` умножение идёт
в binary32, и в дереве появляется узел округления. В `float a; a*2.0` — уже в
binary64, потому что `2.0` это double, и узла нет. Присваивание в переменную типа
`float` округляет, возврат из функции типа `float` округляет. Ошибиться здесь
значит посчитать границу для программы, которой в файле нет.
"""
from __future__ import annotations

import re
from pathlib import Path

from pareto.precision import FLOAT32, FLOAT64, Format, round_op_for

FLOAT_TYPES = {'double': FLOAT64, 'float': FLOAT32, 'long double': FLOAT64}

# Отдельная метка для ЦЕЛОГО литерала. В C `2` это int, и по обычным
# арифметическим преобразованиям он приводится к типу второго операнда: в
# `2 * amountPow3`, где amountPow3 это float, всё считается в float. Пока целый
# литерал носил формат double, такое выражение считалось у нас в двойной
# точности, то есть мы разбирали не ту программу, что написана. Расхождение
# нашла побитовая сверка с clang на Vector3CubicHermite из raylib 07.10.2026.
#
# Важно не перепутать с `2.0`: это уже double по стандарту, и оно ДЕЙСТВИТЕЛЬНО
# утягивает выражение в двойную точность. Разница только в записи, и потому
# решается она на разборе литерала, а не догадкой по значению.
INT_LITERAL = 'int-literal'


# Целые параметры. В вещественной арифметике целое представляется точно, пока
# |n| < 2^53, и отвергать функцию только за `int count` в сигнатуре — терять её
# на ровном месте. Опасный случай — целое как счётчик цикла с неизвестным числом
# шагов — отсекается отдельно, там отказ остаётся.
INT_TYPES = ('int', 'long', 'short', 'char', 'unsigned', 'signed', 'size_t',
             'ptrdiff_t', 'int8_t', 'int16_t', 'int32_t', 'int64_t',
             'uint8_t', 'uint16_t', 'uint32_t', 'uint64_t')

# Функции, которые мы умеем анализировать. Суффикс f — вариант для float.
MATH1 = {'sqrt': 'sqrt', 'exp': 'exp', 'log': 'log', 'expm1': 'expm1', 'log1p': 'log1p',
         'sin': 'sin', 'cos': 'cos', 'atan': 'atan', 'fabs': 'fabs'}
MATH2 = {'hypot': 'hypot', 'atan2': 'atan2', 'fmin': 'fmin', 'fmax': 'fmax'}
MATH3 = {'fma': 'fma'}

REL = ('<=', '>=', '==', '!=', '<', '>')


class CParseError(ValueError):
    """Файл содержит то, про что этот метод ничего доказать не может."""

    def __init__(self, msg, line=None):
        self.line = line
        ValueError.__init__(self, msg if line is None else
                            'line {}: {}'.format(line, msg))


# ---------- лексер ----------
_TOKEN = re.compile(r"""
      (?P<ws>\s+)
    # В режиме VERBOSE решётка открывает комментарий, поэтому её обязательно
    # экранировать: без этого шаблон обрывается и разбор ломается целиком.
    | (?P<pp>^[ \t]*\#[^\n]*)
    | (?P<lcomment>//[^\n]*)
    | (?P<bcomment>/\*.*?\*/)
    | (?P<str>"(?:\\.|[^"\\])*")
    | (?P<chr>'(?:\\.|[^'\\])*')
    | (?P<num>(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?[fFlL]?)
    | (?P<name>[A-Za-z_][A-Za-z_0-9]*)
    | (?P<op><<=|>>=|<=|>=|==|!=|&&|\|\||\+\+|--|->|\+=|-=|\*=|/=|[-+*/%<>=(){};,!&|?:\[\].])
""", re.VERBOSE | re.DOTALL | re.MULTILINE)


class Tok:
    __slots__ = ('kind', 'text', 'line')

    def __init__(self, kind, text, line):
        self.kind, self.text, self.line = kind, text, line

    def __repr__(self):
        return '{}({!r})'.format(self.kind, self.text)


def tokenize(src):
    out, pos, line = [], 0, 1
    n = len(src)
    while pos < n:
        m = _TOKEN.match(src, pos)
        if m is None:
            raise CParseError('character {!r} is not part of the supported subset of C'
                              .format(src[pos]), line)
        kind = m.lastgroup
        text = m.group()
        line += text.count('\n')
        pos = m.end()
        if kind in ('ws', 'lcomment', 'bcomment', 'pp'):
            continue
        out.append(Tok(kind, text, line))
    out.append(Tok('eof', '', line))
    return out


# ---------- типизированное выражение ----------
class Typed:
    """Дерево плюс тип результата по правилам C."""

    __slots__ = ('tree', 'fmt')

    def __init__(self, tree, fmt):
        self.tree, self.fmt = tree, fmt


def _wrap(tree, fmt):
    """Надеть округление, если результат обязан лежать в узком формате."""
    if fmt is INT_LITERAL:
        return tree            # целое значение точно, округлять нечего
    op = round_op_for(fmt)
    return tree if op is None else (op, tree)


def _is_zero(value):
    """Является ли значение буквальным нулём: `{ 0 }` в C обнуляет всю структуру."""
    tree = value.tree if hasattr(value, 'tree') else value
    return tree[0] == 'num' and float(tree[1]) == 0.0


def _binary(op, a, b):
    """Обычное арифметическое преобразование C: шире из двух типов, и округление в него.

    Целый литерал типа не навязывает: он приводится к типу второго операнда. Если
    целые с обеих сторон — результат тоже целый, и для нашей арифметики это точное
    число без округления.
    """
    if a.fmt is INT_LITERAL and b.fmt is INT_LITERAL:
        return Typed((op, a.tree, b.tree), INT_LITERAL)
    if a.fmt is INT_LITERAL:
        fmt = b.fmt
    elif b.fmt is INT_LITERAL:
        fmt = a.fmt
    else:
        fmt = FLOAT64 if (a.fmt is FLOAT64 or b.fmt is FLOAT64) else a.fmt
    return Typed(_wrap((op, a.tree, b.tree), fmt), fmt)


# К диагностике отнесены и явные пустышки: B2_UNUSED(x) разворачивается в
# `(void)x` и на вычисление не влияет вовсе. Проверки входа вроде
# B2_CHECK_INPUT_RETURN — другое дело: при нарушении условия функция выходит
# раньше. Пропуская их, мы разбираем ПУТЬ, НА КОТОРОМ ПРЕДУСЛОВИЕ ВЫПОЛНЕНО, и
# это осознанное допущение, а не недосмотр: именно для такого пути человек и
# спрашивает границу. Список узкий и признан узким.
_DIAGNOSTIC = re.compile(
    # `check` с границей слова не ловил B2_CHECK_INPUT_RETURN: подчёркивание
    # тоже буква. Допускаем и подчёркивание следом, но не произвольное
    # продолжение — чтобы checksum не был принят за диагностику.
    r"assert|abort|log|trace|print|warn|error|fatal|debug|check(?:\b|_)"
    r"|unused|unref|ignore", re.I)


def _is_diagnostic(name):
    """Похоже ли имя на проверку или запись в журнал.

    Нарочно по имени, а не по сигнатуре: отличить «эта функция ничего не меняет»
    в общем случае нельзя, а список подозрительных слов проверяем и признаём его
    узким. Любой другой незнакомый вызов остаётся отказом.
    """
    return bool(_DIAGNOSTIC.search(name))


class _Parser:
    def __init__(self, toks, resolve=None, macros=None, consts=None,
                 globs=None, float_types=None):
        self.t, self.i = toks, 0
        # Вещественные типы ПРОЕКТА, а не только три имени из стандарта. Chipmunk
        # весь написан на cpFloat, и девять его функций отвергались на строке
        # `cpFloat width = box.r - box.l;` — то есть на обычном объявлении
        # локальной переменной. Псевдонимы типов собирались, но до разборщика не
        # доходили: он знал только float, double и long double.
        self.float_types = dict(FLOAT_TYPES)
        if float_types:
            self.float_types.update(float_types)
        self.vars = {}          # имя в C -> (имя в дереве, формат)
        self.counter = 0
        # Чем разрешать вызовы соседних функций. Без этого настоящий код читается
        # плохо: он собран композицией, и отвергать функцию только за то, что она
        # состоит из других, которые мы умеем читать, — потеря на ровном месте.
        self.resolve = resolve
        self.macros = macros or {}
        self.consts = consts or {}
        self.globs = globs or {}
        self.extra_inputs = {}
        self.structs = {}
        # Локальные переменные структурного типа: имя -> (тип, {поле: внутреннее
        # имя}). Нужны, чтобы читать `Vector2 result = { a, b }; return result;`
        # — а это форма, в которой написана вся векторная математика. В одном
        # raymath.h таких функций 109 из 146, и до сих пор перечислитель их даже
        # не видел.
        self.struct_locals = {}
        # Какое поле результата разбираем. Функция, отдающая Vector2, — это два
        # скалярных выхода: у каждого своё выражение и своя граница. Так вся
        # остальная машина остаётся нетронутой: ей по-прежнему приходит одна
        # скалярная программа.
        self.want_field = None
        self.opaque = set()

    # --- служебное ---
    def peek(self, k=0):
        j = self.i + k
        return self.t[j] if j < len(self.t) else self.t[-1]

    def at(self, text):
        return self.peek().text == text

    def take(self, text=None):
        tok = self.peek()
        if text is not None and tok.text != text:
            raise CParseError('expected {!r}, found {!r}'.format(text, tok.text), tok.line)
        self.i += 1
        return tok

    def fresh(self, base):
        self.counter += 1
        return '{}#{}'.format(base, self.counter)

    # --- выражения ---
    def expr(self):
        return self.additive()

    def additive(self):
        node = self.multiplicative()
        while self.peek().text in ('+', '-'):
            op = self.take().text
            node = _binary(op, node, self.multiplicative())
        return node

    def multiplicative(self):
        node = self.unary()
        while self.peek().text in ('*', '/'):
            op = self.take().text
            node = _binary(op, node, self.unary())
        while self.peek().text == '%':
            raise CParseError('the remainder operator % is integer arithmetic and is not '
                              'part of what this method analyses', self.peek().line)
        return node

    def unary(self):
        tok = self.peek()
        if tok.text == '-':
            self.take()
            inner = self.unary()
            return Typed(('neg', inner.tree), inner.fmt)
        if tok.text == '+':
            self.take()
            return self.unary()
        if tok.text == '!':
            raise CParseError('logical negation is not supported inside an expression',
                              tok.line)
        return self.postfix()

    def postfix(self):
        node = self.primary()
        if self.peek().text in ('++', '--', '[', '.'):
            raise CParseError('{!r} is not part of the supported subset of C'
                              .format(self.peek().text), self.peek().line)
        return node

    def primary(self):
        tok = self.peek()
        if tok.kind == 'num':
            self.take()
            return self.literal(tok)
        if tok.text == '(':
            # приведение типа или просто скобки
            nxt = self.peek(1)
            if (nxt.kind == 'name' and nxt.text in self.float_types
                    and self.peek(2).text == ')'):
                self.take('(')
                fmt = self.float_types[self.take().text]
                self.take(')')
                inner = self.unary()
                if fmt is inner.fmt:
                    return inner
                return Typed(_wrap(inner.tree, fmt), fmt)
            if nxt.kind == 'name' and nxt.text in ('int', 'long', 'short', 'char',
                                                   'unsigned', 'signed'):
                raise CParseError('a cast to an integer type discards the fractional part; '
                                  'this method has nothing to prove about it', tok.line)
            self.take('(')
            node = self.expr()
            self.take(')')
            return node
        if tok.kind == 'name':
            if self.peek(1).text == '(':
                return self.call()
            self.take()
            key = tok.text
            while self.peek().text in ('.', '->') and self.peek(1).kind == 'name':
                sep = self.take().text
                key = key + sep + self.take().text
            if key not in self.vars and key in self.consts:
                fmt = getattr(self.consts, 'fmts', {}).get(key, FLOAT64)
                return Typed(('num', float(self.consts[key])), fmt)
            if key not in self.vars and ('->' in key or '.' in key) and                     re.split(r'->|\.', key)[0] in self.opaque:
                # Поле непрозрачного объекта: величина есть, состав неизвестен.
                self.vars[key] = (key, FLOAT64)
                self.extra_inputs[key] = FLOAT64
            if key not in self.vars and key in self.globs:
                # Глобальное состояние — свободная величина: регистрируем как вход
                # и требуем диапазон наравне с аргументами.
                fmt = self.globs[key]
                self.vars[key] = (key, fmt)
                self.extra_inputs[key] = fmt
            if key not in self.vars:
                raise CParseError('unknown name {!r}: it is neither an argument nor a local '
                                  'variable of this function'.format(key), tok.line)
            name, fmt = self.vars[key]
            return Typed(('var', name), fmt)
        raise CParseError('cannot read an expression starting at {!r}'.format(tok.text),
                          tok.line)

    def literal(self, tok):
        text = tok.text
        fmt = None
        if text[-1] in 'fF':
            text, fmt = text[:-1], FLOAT32
        elif text[-1] in 'lL':
            text = text[:-1]
            fmt = FLOAT64
        elif '.' in text or 'e' in text or 'E' in text:
            fmt = FLOAT64          # `2.0` и `1e3` — double по стандарту
        else:
            fmt = INT_LITERAL      # `2` — int, тип ему даёт второй операнд
        try:
            value = float(text)
        except ValueError:
            raise CParseError('cannot read the number {!r}'.format(tok.text), tok.line)
        if fmt is FLOAT32:
            value = FLOAT32.round(value)
        return Typed(('num', value), fmt)

    def skip_statement(self):
        depth = 0
        while True:
            t = self.peek()
            if t.kind == 'eof':
                return
            if t.text in ('(', '{', '['):
                depth += 1
            elif t.text in (')', '}', ']'):
                depth -= 1
            elif t.text == ';' and depth <= 0:
                self.take(';')
                return
            self.take()

    def opaque_struct_local(self):
        """Локальная структура, полученная от того, чего мы не разбираем.

        Для УКАЗАТЕЛЯ на непрозрачный объект это правило уже действует: поля
        честно считаются неизвестными входами с обязательным диапазоном. Для
        структуры ПО ЗНАЧЕНИЮ его не было, и разбор падал на `qA.c` сообщением
        «неизвестное имя» — хотя случай тот же самый: значение пришло оттуда, где
        мы ничего не моделируем, а поля участвуют в расчёте.

        Это загрубление в безопасную сторону: границу оно может только расширить,
        потому что связь между полями и настоящими входами теряется. Цена честная
        и названная — зато диапазон для таких полей человек обязан указать, иначе
        разбор не пойдёт.

        Инициализатор пропускается целиком: вычислить его мы не можем, а делать
        вид, что можем, значит посчитать границу для другой программы.
        """
        start = self.i
        tname = self.take().text
        while tname in ('struct', 'union', 'const', 'static', 'volatile'):
            if self.peek().kind != 'name':
                self.i = start
                return None
            tname = self.take().text
        if tname not in self.structs or self.peek().kind != 'name':
            self.i = start
            return None
        vname = self.take().text
        if self.at('['):
            self.i = start
            return None            # массив — отдельный случай, здесь отказ
        if not self.at('='):
            self.i = start
            return None
        self.take('=')
        self.skip_statement()

        fields = [(f, fm) for f, fm in self.structs[tname].items()
                  if isinstance(fm, Format)]
        if not fields:
            self.i = start
            return None
        slots = {}
        for fname, fmt in fields:
            key = vname + '.' + fname
            self.vars[key] = (key, fmt)
            self.extra_inputs[key] = fmt
            slots[fname] = (key, fmt)
        self.struct_locals[vname] = (tname, slots)
        self.opaque.add(vname)
        return []

    def opaque_local(self):
        """Локальный объект, полученный откуда-то, чего мы не разбираем.

        В box2d это `b2JointSim* joint = ...;` — семьдесят восемь отказов из ста
        сорока. Само объявление нам ничего не даёт: значение приходит из поиска по
        структурам данных, которые мы не моделируем. Но поля такого объекта честно
        участвуют в расчёте, и правильный ответ — считать их НЕИЗВЕСТНЫМИ входами
        с обязательным диапазоном, ровно как глобальное состояние. Это загрубление
        в безопасную сторону: границу оно может только расширить.

        Инициализатор пропускаем целиком: вычислить его мы всё равно не можем, а
        делать вид, что можем, значит посчитать границу для другой программы.
        """
        start = self.i
        tname = self.take().text
        # Объявление может начинаться служебным словом: `struct cpContact *con = ...`.
        # Само слово типом не является, настоящее имя идёт следом.
        while tname in ('struct', 'union', 'const', 'static', 'volatile'):
            if self.peek().kind != 'name':
                self.i = start
                return False
            tname = self.take().text
        while self.at('*'):
            self.take('*')
        if self.peek().kind != 'name':
            self.i = start
            return False
        vname = self.take().text
        depth = 0
        while True:                       # пропускаем до конца объявления
            t = self.peek()
            if t.kind == 'eof':
                self.i = start
                return False
            if t.text in ('(', '{', '['):
                depth += 1
            elif t.text in (')', '}', ']'):
                depth -= 1
            elif t.text == ';' and depth <= 0:
                self.take(';')
                break
            self.take()
        flat = {}
        _expand(self.structs, tname, vname + '.', flat)
        _expand(self.structs, tname, vname + '->', flat)
        if not flat:
            # Тип непрозрачен: состав полей неизвестен. Величина всё равно есть,
            # поэтому помечаем имя, а поля заведём лениво при первом обращении.
            self.opaque.add(vname)
            return True
        for key, fmt in flat.items():
            self.vars[key] = (key, fmt)
            self.extra_inputs[key] = fmt
        return True

    def struct_bases(self):
        """Пути, за которыми стоит структура: v, если известно v.x, и con->r2,
        если известно con->r2.x.

        Структура приходит в вызов не только простым именем: в настоящем коде это
        сплошь и рядом путь к полю — cpvsub(con->r2, con->r1). Раньше такой
        аргумент не распознавался, и функция отвергалась из-за формы записи, а не
        из-за существа.
        """
        out = set()
        for k in self.vars:
            for sep in ('.', '->'):
                if sep in k:
                    out.add(k.rsplit(sep, 1)[0])
        return out

    def user_arg(self):
        """Аргумент пользовательского вызова: либо структура целиком, либо выражение.

        Структура передаётся по имени, а не выражением: в дереве её нет, есть
        только её поля. Поэтому `cpvdot(v, v)` читается как ссылка на набор полей,
        а не как попытка вычислить `v`.
        """
        bases = self.struct_bases()
        start = self.i
        if self.peek().kind == 'name':
            path = self.take().text
            while self.peek().text in ('.', '->') and self.peek(1).kind == 'name':
                sep = self.take().text
                path = path + sep + self.take().text
            if path in bases and self.peek().text in (',', ')'):
                return ('struct', path)
            self.i = start
        return ('expr', self.expr())

    def inline(self, cal, raw, tok):
        """Подставить тело вызванной функции вместо вызова."""
        groups, seen = [], set()
        for a in cal['order']:
            base = a.split('.', 1)[0] if '.' in a else a
            if base in seen:
                continue
            seen.add(base)
            fields = [x.split('.', 1)[1] for x in cal['order']
                      if x.startswith(base + '.')]
            groups.append((base, fields or None))
        if len(groups) != len(raw):
            raise CParseError('call to {} with {} argument(s) does not match its {} '
                              'parameter(s)'.format(cal['name'], len(raw), len(groups)),
                              tok.line)

        stmts = cal['stmts']
        if len(stmts) != 1 or stmts[0][0] != 'return':
            flat = flatten_program(stmts)
            if flat is not None:
                stmts = [('return', flat)]
            else:
                picked = as_select(stmts)
                if picked is None:
                    raise CParseError(
                        'the called function {} is not a single return expression, so it '
                        'cannot be substituted here'.format(cal['name']), tok.line)
                tree, _fmt = picked
                # Кладём ОБЫЧНОЕ дерево, а не Typed: ниже по этой же функции идёт
            # подстановка аргументов обходом узлов, и обёртка ломала её с
            # TypeError. Я написал здесь Typed в цикле 50 из заботы о формате —
            # и тем самым сломал ровно то, что включал: подстановка cpfmin и
            # cpfmax падала вместо работы, причём молча для тестов, потому что ни
            # один из них не подставлял функцию с выбором внутрь выражения.
            #
                # Формат и не требовался: возврат этой функции оборачивается в
                # Typed с cal['result'] на выходе, то есть тип берётся из
                # объявления вызванной функции, как и надо.
                stmts = [('return', tree)]

        sub = {}
        for (base, fields), (kind, val) in zip(groups, raw):
            if fields is None:
                if kind != 'expr':
                    raise CParseError('{} expects a number for {!r}, got a struct'
                                      .format(cal['name'], base), tok.line)
                sub[base] = val.tree
            else:
                if kind != 'struct':
                    raise CParseError('{} expects a struct for {!r}; pass the variable by '
                                      'name'.format(cal['name'], base), tok.line)
                for f in fields:
                    key = None
                    for sep in ('.', '->'):
                        if val + sep + f in self.vars:
                            key = val + sep + f
                            break
                    if key is None:
                        raise CParseError('{!r} has no field {!r} here'.format(val, f),
                                          tok.line)
                    sub[base + '.' + f] = ('var', self.vars[key][0])

        def walk(node):
            if node[0] == 'var':
                return sub.get(node[1], node)
            if node[0] in ('num',):
                return node
            return (node[0],) + tuple(walk(k) for k in node[1:])

        return Typed(walk(stmts[0][1]), cal['result'])

    def call(self):
        tok = self.take()
        name = self.macros.get(tok.text, tok.text) if self.macros else tok.text
        self.take('(')
        known = (set(MATH1) | set(MATH2) | set(MATH3) | {'pow', 'fma'}
                 | {n + 'f' for n in set(MATH1) | set(MATH2) | set(MATH3) | {'pow'}})
        cal = None
        if self.resolve is not None and name not in known:
            cal = self.resolve(name)
        if cal is not None:
            raw = []
            if not self.at(')'):
                raw.append(self.user_arg())
                while self.at(','):
                    self.take(',')
                    raw.append(self.user_arg())
            self.take(')')
            return self.inline(cal, raw, tok)
        args = []
        if not self.at(')'):
            args.append(self.expr())
            while self.at(','):
                self.take(',')
                args.append(self.expr())
        self.take(')')

        base, narrow = name, False
        if name.endswith('f') and name[:-1] in set(MATH1) | set(MATH2) | set(MATH3) | {'pow'}:
            base, narrow = name[:-1], True
        out_fmt = FLOAT32 if narrow else FLOAT64

        if base == 'pow':
            return self.power(args, out_fmt, tok)
        if base in MATH1 and len(args) == 1:
            arg = self.coerce(args[0], out_fmt)
            return Typed(_wrap((MATH1[base], arg), out_fmt), out_fmt)
        if base in MATH2 and len(args) == 2:
            a = self.coerce(args[0], out_fmt)
            b = self.coerce(args[1], out_fmt)
            return Typed(_wrap((MATH2[base], a, b), out_fmt), out_fmt)
        if base in MATH3 and len(args) == 3:
            a, b, c = (self.coerce(x, out_fmt) for x in args)
            # fma — одно округление на всю операцию, ровно это и означает узел fma
            return Typed(_wrap(('fma', a, b, c), out_fmt), out_fmt)
        if base in ('abs', 'floor', 'ceil', 'round',
                    'tan', 'asin', 'acos', 'sinh', 'cosh', 'tanh',
                    'log10', 'log2', 'exp2', 'cbrt', 'erf', 'tgamma', 'lgamma'):
            raise CParseError(
                'the function {} is not covered: this method needs a proven bound on the '
                'rounding error of every operation, and for {} it has none. Supported: '
                '{}'.format(name, name,
                            ', '.join(sorted(set(MATH1) | set(MATH2) | set(MATH3) | {'pow'}))),
                tok.line)
        raise CParseError('call to {} with {} argument(s) is not supported'
                          .format(name, len(args)), tok.line)

    def coerce(self, node, fmt):
        """Аргумент библиотечной функции приводится к её типу — как в C."""
        if node.fmt is fmt:
            return node.tree
        return _wrap(node.tree, fmt) if fmt is FLOAT32 else node.tree

    def power(self, args, out_fmt, tok):
        if len(args) != 2 or args[1].tree[0] != 'num':
            raise CParseError('pow is supported only with a constant integer exponent: the '
                              'general case has no elementary error bound', tok.line)
        e = args[1].tree[1]
        if e != int(e) or e < 0 or e > 64:
            raise CParseError('pow is supported for integer exponents from 0 to 64, got {}'
                              .format(e), tok.line)
        e = int(e)
        base = self.coerce(args[0], out_fmt)
        if e == 0:
            return Typed(('num', 1.0), out_fmt)
        node = base
        for _ in range(e - 1):
            node = _wrap(('*', node, base), out_fmt)
        return Typed(node, out_fmt)

    # --- условия ---
    def condition(self):
        """Условие: сравнения, соединённые && и ||.

        Возвращает дерево вида ('and', a, b) / ('or', a, b) / (отношение, л, п).
        Раскрытие в обычные ветвления делает `_lower_cond`: так разборщик остаётся
        разборщиком, а логика ветвлений живёт в одном месте.
        """
        node = self.cond_and()
        while self.at('||'):
            self.take('||')
            node = ('or', node, self.cond_and())
        return node

    def cond_and(self):
        node = self.cond_atom()
        while self.at('&&'):
            self.take('&&')
            node = ('and', node, self.cond_atom())
        return node

    def cond_atom(self):
        if self.at('('):
            # либо скобки вокруг условия, либо сравнение, начинающееся со скобки
            save = self.i
            self.take('(')
            try:
                inner = self.condition()
                if self.at(')'):
                    self.take(')')
                    if self.peek().text not in REL:
                        return inner
            except CParseError:
                pass
            self.i = save
        left = self.expr()
        tok = self.peek()
        if tok.text not in REL:
            raise CParseError('a condition must be a comparison; found {!r}. A bare numeric '
                              'value used as a truth test is not supported'
                              .format(tok.text), tok.line)
        rel = self.take().text
        right = self.expr()
        return (rel, left.tree, right.tree)

    # --- операторы ---
    def block(self):
        if self.at('{'):
            self.take('{')
            out = []
            while not self.at('}'):
                if self.peek().kind == 'eof':
                    raise CParseError('the function body ends without a closing brace',
                                      self.peek().line)
                out.extend(self.statement())
            self.take('}')
            return out
        return self.statement()

    def statement(self):
        tok = self.peek()
        if tok.text == ';':
            self.take(';')
            return []
        if tok.kind == 'name' and tok.text in ('const', 'volatile', 'register'):
            # `const float ratio = ...;` — объявление с квалификатором. На
            # вычисление квалификатор не влияет никак: это обещание не менять
            # переменную, а не иное поведение арифметики. Раньше такая строка
            # отвергала функцию целиком.
            nxt = self.peek(1)
            if nxt.kind == 'name' and (nxt.text in self.float_types
                                       or nxt.text in INT_TYPES):
                self.take()
                return self.statement()
        if tok.kind == 'name' and tok.text in self.float_types:
            return self.declaration()
        if tok.kind == 'name' and tok.text in ('int', 'long', 'short', 'unsigned', 'char',
                                               'signed', 'const', 'static', 'register'):
            if tok.text in INT_TYPES and self.peek(1).kind == 'name':
                # Локальное целое — обычная величина расчёта: ниже 2^53 точна.
                # Отвергать объявление, которое мы умеем прочитать, незачем;
                # опасный случай — счётчик цикла с неизвестным числом шагов —
                # отсекается там, где разбирается сам цикл.
                self.take()
                name = self.take().text
                if self.at('='):
                    self.take('=')
                    value = self.expr()
                    self.take(';')
                    self.vars[name] = (name, FLOAT64)
                    return [('let', name, self.coerce(value, FLOAT64))]
                self.take(';')
                self.vars[name] = (name, FLOAT64)
                return [('let', name, Typed(('num', 0.0), FLOAT64))]
            raise CParseError('declaration of {!r} inside the body is not supported (integer '
                              'and qualified declarations are outside the subset)'
                              .format(tok.text), tok.line)
        if tok.text == 'if':
            return self.if_statement()
        if tok.text == 'for':
            return self.for_statement()
        if tok.text == 'return':
            self.take('return')
            out = self.return_expr()
            self.take(';')
            return out
        if tok.text in ('while', 'do', 'switch', 'goto', 'break', 'continue'):
            raise CParseError('{!r} is not supported: this method proves bounds on '
                              'straight-line code, on conditionals and on loops with a known '
                              'trip count'.format(tok.text), tok.line)
        if tok.kind == 'name' and self.peek(1).text in ('=', '+=', '-=', '*=', '/='):
            return self.assignment()
        if tok.kind == 'name' and tok.text in self.struct_locals:
            got = self.struct_field_assignment()
            if got is not None:
                return got
        if tok.kind == 'name' and tok.text in self.structs:
            got = self.struct_local_init()
            if got is not None:
                return got
            got = self.struct_call_init()
            if got is not None:
                return got
            got = self.struct_copy_init()
            if got is not None:
                return got
            got = self.opaque_struct_local()
            if got is not None:
                return got
        if tok.text == '{':
            return self.block()
        if tok.kind == 'name' and (tok.text in self.structs
                                   or (tok.text in ('struct', 'union', 'const')
                                       and self.peek(1).kind == 'name')):
            if self.opaque_local():
                return []
        if tok.text == '(' and self.peek(1).text in ('void', 'char', 'int')                 and self.peek(2).text == ')':
            # `(void)x;` — пометка «параметр намеренно не используется». К расчёту
            # отношения не имеет, пропускаем целиком.
            self.skip_statement()
            return []
        if tok.kind in ('str', 'chr'):
            raise CParseError('a string or character literal takes part in this statement; '
                              'that is outside the numeric subset', tok.line)
        if tok.kind == 'name' and self.peek(1).text == '(' and _is_diagnostic(tok.text):
            # Вызов-проверка или вызов-лог отдельным оператором. Он не участвует в
            # вычислении и значения наших величин не меняет, поэтому пропускаем.
            # Узко и намеренно: любой ДРУГОЙ незнакомый вызов по-прежнему отказ,
            # потому что он может писать по указателю, а память мы не моделируем.
            self.skip_statement()
            return []
        raise CParseError('statement starting at {!r} is not supported'.format(tok.text),
                          tok.line)

    def declaration(self):
        fmt = self.float_types[self.take().text]
        out = []
        while True:
            name_tok = self.take()
            if name_tok.kind != 'name':
                raise CParseError('expected a variable name, found {!r}'.format(name_tok.text),
                                  name_tok.line)
            if self.at('['):
                raise CParseError('array declarations are not supported; a reduction over an '
                                  'array is a separate mode (pareto/reductions.py)',
                                  name_tok.line)
            if self.at('='):
                self.take('=')
                if self.ternary_question() is not None:
                    out.extend(self.ternary_let(name_tok.text, fmt,
                                                name_tok.line))
                    return out
                value = self.expr()
                tree = value.tree if value.fmt is fmt else _wrap(value.tree, fmt)
                inner = self.fresh(name_tok.text)
                out.append(('let', inner, tree))
                self.vars[name_tok.text] = (inner, fmt)
            else:
                raise CParseError('the variable {} is declared without a value. An '
                                  'uninitialised variable has no interval, so there is '
                                  'nothing to bound'.format(name_tok.text), name_tok.line)
            if self.at(','):
                self.take(',')
                continue
            self.take(';')
            return out

    def ternary_let(self, name, fmt, line):
        """`name = cond ? a : b;` — понижение до ветвления с присваиванием в ветках.

        Это самый частый идиом в живом коде, и до сих пор он отвергал функцию
        целиком: `invMag = mag > 0.0f ? 1.0f / mag : 0.0f;` в b2Normalize, и
        подобное в raylib.

        Понижение работает потому, что представление программы уже умеет хвост за
        ветвлением: `paths` при разборе `if` приписывает остаток операторов в
        обе ветви и выдаёт по пути на каждую. То есть управление «сходится
        обратно» только на вид — каждый путь остаётся прямолинейным, и никакого
        слияния значений не требуется.

        Единственная тонкость: внутреннее имя переменной должно быть ОДНО на обе
        ветви, иначе дальнейший код сослался бы на имя из одной из них. Поэтому
        свежее имя выдаётся здесь один раз, до разбора ветвей.
        """
        inner = self.fresh(name)
        cond = self.condition()
        self.take('?')
        then_val = self.expr()
        self.take(':')
        else_val = self.expr()
        self.take(';')

        def wrap(value):
            return value.tree if value.fmt is fmt else _wrap(value.tree, fmt)

        out = _lower_cond(cond,
                          [('let', inner, wrap(then_val))],
                          [('let', inner, wrap(else_val))])
        self.vars[name] = (inner, fmt)
        return out

    def struct_local_init(self):
        """`Vector2 result = { e1, e2 };` — структурная локальная с фигурным списком.

        Поля привязываются ПО ПОРЯДКУ объявления в структуре, как и требует C.
        Короткий список допустим: `{ 0 }` в C обнуляет всё остальное, и именно так
        написан Vector2Normalize в raylib.

        Отказ возвращается как False без потребления лексем, чтобы разбор мог
        попробовать другие правила: эта же форма начинается так же, как
        объявление непрозрачного локального объекта.
        """
        start = self.i
        tname = self.take().text
        if tname not in self.structs or self.peek().kind != 'name':
            self.i = start
            return None
        vname = self.take().text
        if not self.at('='):
            self.i = start
            return None
        self.take('=')
        if not self.at('{'):
            self.i = start
            return None
        self.take('{')
        values = []
        if not self.at('}'):
            while True:
                values.append(self.expr())
                if self.at(','):
                    self.take(',')
                    if self.at('}'):
                        break          # висячая запятая законна
                    continue
                break
        self.take('}')
        self.take(';')

        fields = self.structs[tname]
        out, slots = [], {}
        for idx, (fname, fmt) in enumerate(fields.items()):
            if not isinstance(fmt, Format):
                continue               # вложенная структура: пока не раскрываем
            if idx < len(values):
                val = values[idx]
            elif values and len(values) == 1 and _is_zero(values[0]):
                val = values[0]        # `{ 0 }` обнуляет всё
            elif values:
                val = Typed(('num', 0.0), fmt)
            else:
                val = Typed(('num', 0.0), fmt)
            tree = val.tree if val.fmt is fmt else _wrap(val.tree, fmt)
            inner = self.fresh(vname + '.' + fname)
            out.append(('let', inner, tree))
            slots[fname] = (inner, fmt)
        self.struct_locals[vname] = (tname, slots)
        for fname, (inner, fmt) in slots.items():
            self.vars[vname + '.' + fname] = (inner, fmt)
        return out

    def compound_literal(self):
        """`(b2Vec2){ e1, e2 }` или `B2_LITERAL(b2Vec2){ e1, e2 }` — список значений.

        Составной литерал по стандарту C99, и в box2d им написан возврат почти
        всей векторной математики. Имя макроса перед скобками допускается: B2_LITERAL
        разворачивается ровно в приведение типа, и для нас это шум, который надо
        пропустить, а не повод отвергнуть функцию.

        Возвращает (имя типа, список значений) либо None без потребления лексем.
        """
        start = self.i
        if self.peek().kind == 'name' and self.peek(1).text == '(':
            self.take()                    # имя макроса
        if not self.at('('):
            self.i = start
            return None
        self.take('(')
        if self.peek().kind != 'name':
            self.i = start
            return None
        tname = self.take().text
        if tname in ('struct', 'union') and self.peek().kind == 'name':
            tname = self.take().text
        if tname not in self.structs or not self.at(')'):
            self.i = start
            return None
        self.take(')')
        if not self.at('{'):
            self.i = start
            return None
        self.take('{')
        values = []
        if not self.at('}'):
            while True:
                values.append(self.expr())
                if self.at(','):
                    self.take(',')
                    if self.at('}'):
                        break
                    continue
                break
        self.take('}')
        return tname, values

    def literal_field(self, tname, values, field):
        """Значение нужного поля из позиционного списка составного литерала."""
        fields = [(f, fm) for f, fm in self.structs[tname].items()
                  if isinstance(fm, Format)]
        for idx, (fname, fmt) in enumerate(fields):
            if fname != field:
                continue
            if idx < len(values):
                val = values[idx]
            elif len(values) == 1 and _is_zero(values[0]):
                val = values[0]
            else:
                val = Typed(('num', 0.0), fmt)
            tree = val.tree if val.fmt is fmt else _wrap(val.tree, fmt)
            return Typed(tree, fmt)
        return None

    def struct_copy_init(self):
        """`Vector3 result = v;` — копия структуры из параметра или другой локальной.

        Так написан Vector3Normalize в raylib и ещё десятки функций: структура
        копируется целиком, а потом правятся отдельные поля. Копия в C — это
        побитовое присваивание, никакой арифметики, поэтому поля новой переменной
        просто получают выражения полей старой.

        Источником может быть и поле другой структуры (`b2Vec2 p = t.position;`),
        поэтому путь читается с точками и стрелками, как обычное имя.
        """
        start = self.i
        tname = self.take().text
        if tname not in self.structs or self.peek().kind != 'name':
            self.i = start
            return None
        vname = self.take().text
        if not self.at('='):
            self.i = start
            return None
        self.take('=')
        if self.peek().kind != 'name':
            self.i = start
            return None
        key = self.take().text
        while self.peek().text in ('.', '->') and self.peek(1).kind == 'name':
            sep = self.take().text
            key = key + sep + self.take().text
        if not self.at(';'):
            self.i = start
            return None

        fields = [(f, fm) for f, fm in self.structs[tname].items()
                  if isinstance(fm, Format)]
        out, slots = [], {}
        for fname, fmt in fields:
            found = None
            for sep in ('.', '->'):
                probe = key + sep + fname
                if probe in self.vars:
                    found = self.vars[probe]
                    break
            if found is None and key in self.struct_locals:
                found = self.struct_locals[key][1].get(fname)
            if found is None:
                self.i = start
                return None        # поле источника нам неизвестно — отказ целиком
            inner_src, fmt_src = found
            tree = ('var', inner_src)
            if fmt_src is not fmt:
                tree = _wrap(tree, fmt)
            inner = self.fresh(vname + '.' + fname)
            out.append(('let', inner, tree))
            slots[fname] = (inner, fmt)
        self.take(';')
        self.struct_locals[vname] = (tname, slots)
        for fname, (inner, fmt) in slots.items():
            self.vars[vname + '.' + fname] = (inner, fmt)
        return out

    def struct_call_init(self):
        """`b2Vec2 p = b2Add(a, b);` — структурная локальная от вызова функции.

        Самая частая форма в живом коде и до сих пор самая частая причина отказа:
        в box2d двенадцать функций падали на сообщении «неизвестное имя b2Vec2»,
        потому что разбор доходил до объявления и пытался читать его как
        выражение.

        Вызванная функция подставляется ПО ПОЛЯМ: `b2Add` даёт два скалярных
        выражения, по одному на координату, и каждое становится своей локальной
        величиной. Если хотя бы одно поле подставить не удалось, отказываемся
        целиком и откатываем разбор: половина подстановки хуже отказа, потому что
        даёт границу для программы, которой нет.
        """
        start = self.i
        tname = self.take().text
        if tname not in self.structs or self.peek().kind != 'name':
            self.i = start
            return None
        vname = self.take().text
        if not self.at('='):
            self.i = start
            return None
        self.take('=')
        if self.peek().kind != 'name' or self.peek(1).text != '(':
            self.i = start
            return None

        fields = [(f, fm) for f, fm in self.structs[tname].items()
                  if isinstance(fm, Format)]
        if not fields:
            self.i = start
            return None

        call_at = self.i
        out, slots = [], {}
        for fname, fmt in fields:
            self.i = call_at
            tok = self.take()
            cname = self.macros.get(tok.text, tok.text) if self.macros else tok.text
            self.take('(')
            cal = self.resolve(cname, fname) if self.resolve is not None else None
            if cal is None:
                self.i = start
                return None
            raw = []
            if not self.at(')'):
                raw.append(self.user_arg())
                while self.at(','):
                    self.take(',')
                    raw.append(self.user_arg())
            self.take(')')
            try:
                value = self.inline(cal, raw, tok)
            except CParseError:
                self.i = start
                return None
            tree = value.tree if value.fmt is fmt else _wrap(value.tree, fmt)
            inner = self.fresh(vname + '.' + fname)
            out.append(('let', inner, tree))
            slots[fname] = (inner, fmt)
        self.take(';')
        self.struct_locals[vname] = (tname, slots)
        for fname, (inner, fmt) in slots.items():
            self.vars[vname + '.' + fname] = (inner, fmt)
        return out

    def struct_field_assignment(self):
        """`result.x = expr;` — присваивание полю структурной локальной."""
        start = self.i
        vname = self.take().text
        if vname not in self.struct_locals or not self.at('.'):
            self.i = start
            return None
        self.take('.')
        if self.peek().kind != 'name':
            self.i = start
            return None
        fname = self.take().text
        if self.peek().text not in ('=', '+=', '-=', '*=', '/='):
            self.i = start
            return None
        tname, slots = self.struct_locals[vname]
        if fname not in slots:
            self.i = start
            return None
        old, fmt = slots[fname]
        op = self.take().text
        if op == '=' and self.ternary_question() is not None:
            key = vname + '.' + fname
            out = self.ternary_let(key, fmt, self.peek().line)
            slots[fname] = self.vars[key]
            return out
        value = self.expr()
        self.take(';')
        if op != '=':
            value = _binary(op[0], Typed(('var', old), fmt), value)
        tree = value.tree if value.fmt is fmt else _wrap(value.tree, fmt)
        inner = self.fresh(vname + '.' + fname)
        slots[fname] = (inner, fmt)
        self.vars[vname + '.' + fname] = (inner, fmt)
        return [('let', inner, tree)]

    def assignment(self):
        name_tok = self.take()
        name = name_tok.text
        if name not in self.vars:
            raise CParseError('assignment to unknown name {!r}'.format(name), name_tok.line)
        old, fmt = self.vars[name]
        op = self.take().text
        if op == '=' and self.ternary_question() is not None:
            return self.ternary_let(name, fmt, name_tok.line)
        value = self.expr()
        self.take(';')
        if op != '=':
            arith = op[0]
            value = _binary(arith, Typed(('var', old), fmt), value)
        tree = value.tree if value.fmt is fmt else _wrap(value.tree, fmt)
        inner = self.fresh(name)
        # Переприсваивание — это НОВОЕ имя, а старое остаётся жить в тех деревьях,
        # куда уже подставлено. Иначе `t = t + 1` дало бы рекурсивную подстановку.
        self.vars[name] = (inner, fmt)
        return [('let', inner, tree)]


    def match_paren(self, i):
        """Индекс закрывающей скобки, парной к открывающей на позиции i."""
        depth = 0
        while i < len(self.t):
            txt = self.t[i].text
            if txt in ('(', '['):
                depth += 1
            elif txt in (')', ']'):
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        return None

    def ternary_question(self):
        """Позиция `?` тернарника на верхнем уровне текущего выражения, иначе None.

        Выражение кончается на `;`, на `:` своей же ветки или на закрывающей
        скобке нулевой глубины. Вопрос внутри скобок верхним уровнем не считается:
        на завёрнутый в скобки тернарник есть отдельная развёртка.
        """
        depth, i = 0, self.i
        while i < len(self.t):
            txt = self.t[i].text
            if txt in ('(', '['):
                depth += 1
            elif txt in (')', ']'):
                if depth == 0:
                    return None
                depth -= 1
            elif depth == 0:
                if txt in (';', ':', ','):
                    return None
                if txt == '?':
                    return i
            i += 1
        return None

    def return_expr(self):
        """Выражение возврата, в котором тернарник понижается до ветвления.

        `return c ? a : b;` это ровно `if (c) return a; else return b;`, а механизм
        ветвления у нас уже есть: каждый путь получает своё условие достижимости и
        свою границу, плюс отдельно считается прыжок между ветками у границы
        условия. Поэтому тернарник не требует ни нового узла, ни нового правила —
        только понижения на разборе.

        Так принимаются b2MinFloat, b2MaxFloat, b2AbsFloat и b2ClampFloat из box2d,
        то есть именно те функции, в которых есть что считать. Тернарник в
        ПРИСВАИВАНИИ по-прежнему отвергается, и честно: там управление сходится
        обратно, и после схождения переменная имеет разные значения в разных
        ветках — подстановкой это не выражается.
        """
        # Выражение целиком в скобках: лишние скобки ничего не меняют, а
        # завёрнутый тернарник иначе остался бы незамеченным. Так читается
        # вложенный случай `a < lo ? lo : ( a > hi ? hi : a )`.
        if self.at('('):
            close = self.match_paren(self.i)
            if close is not None and self.t[close + 1].text in (';', ':', ')'):
                self.take('(')
                out = self.return_expr()
                self.take(')')
                return out
        # `return (b2Vec2){ e1, e2 };` — составной литерал прямо в возврате.
        if self.want_field:
            got = self.compound_literal()
            if got is not None:
                tname, values = got
                picked = self.literal_field(tname, values, self.want_field)
                if picked is None:
                    raise CParseError('{} has no field {!r}'.format(
                        tname, self.want_field), self.peek().line)
                return [('return', picked)]

        # `return result;` где result — структурная локальная: отдаём выражение
        # нужного поля. Какое именно поле нужно, разборщику сказали заранее.
        if (self.want_field and self.peek().kind == 'name'
                and self.peek().text in self.struct_locals
                and self.peek(1).text == ';'):
            vname = self.take().text
            _tname, slots = self.struct_locals[vname]
            if self.want_field not in slots:
                raise CParseError('{} has no field {!r}'.format(
                    vname, self.want_field), self.peek().line)
            inner, fmt = slots[self.want_field]
            return [('return', Typed(('var', inner), fmt))]
        if self.ternary_question() is None:
            return [('return', self.expr())]
        cond = self.condition()
        self.take('?')
        then_part = self.return_expr()
        self.take(':')
        else_part = self.return_expr()
        return _lower_cond(cond, then_part, else_part)

    def if_statement(self):
        self.take('if')
        self.take('(')
        cond = self.condition()
        self.take(')')

        # Ветви разбираются каждая со СВОЕЙ копией таблицы имён, а потом имена
        # сводятся. Раньше здесь стояло требование «каждая ветвь обязана
        # закончиться возвратом»: мол, иначе управление сходится обратно, и
        # переменная имеет разное значение в разных ветках.
        #
        # Требование оказалось лишним, и обнаружил это внешний судья. В raylib
        # написано `if (result > max) result = max;` — ветвь без else. Наш
        # разборщик давал переменной новое внутреннее имя внутри ветви, и на
        # пути «условие ложно» это имя не существовало: сравнение с clang
        # упало с KeyError на функции Clamp.
        #
        # Слияние выражается — потому что представление программы уже умеет
        # хвост за ветвлением: `paths` приписывает остаток операторов в обе
        # ветви. То есть каждый путь остаётся прямолинейным, и достаточно
        # сделать так, чтобы ОБЕ ветви заканчивались одним и тем же внутренним
        # именем. Ветвь, которая переменную не меняла, получает явное
        # присваивание прежнего значения.
        before = dict(self.vars)

        self.vars = dict(before)
        then_part = self.block()
        after_then = dict(self.vars)

        self.vars = dict(before)
        else_part = []
        if self.at('else'):
            self.take('else')
            else_part = self.block()
        after_else = dict(self.vars)

        merged = dict(before)
        for name in set(after_then) | set(after_else):
            t = after_then.get(name)
            e = after_else.get(name)
            if t == e:
                if t is not None:
                    merged[name] = t
                continue
            if t is None or e is None:
                # Переменная объявлена внутри одной ветви: за пределами
                # ветвления её нет, и выносить её наружу нельзя.
                continue
            fmt = t[1]
            inner = self.fresh(name)
            then_part = list(then_part) + [('let', inner, ('var', t[0]))]
            else_part = list(else_part) + [('let', inner, ('var', e[0]))]
            merged[name] = (inner, fmt)
        self.vars = merged
        return _lower_cond(cond, then_part, else_part)

    def for_statement(self):
        tok = self.take('for')
        self.take('(')
        # for (int i = 0; i < N; i++) — только такая форма и только с константами
        if self.peek().kind == 'name' and self.peek().text in ('int', 'long', 'unsigned'):
            self.take()
        var_tok = self.take()
        if var_tok.kind != 'name':
            raise CParseError('the loop counter must be a plain name', var_tok.line)
        self.take('=')
        start = self.const_int('the initial value of the loop counter', var_tok.line)
        self.take(';')
        cmp_name = self.take()
        if cmp_name.text != var_tok.text:
            raise CParseError('the loop condition must compare the same counter', cmp_name.line)
        rel = self.take().text
        if rel not in ('<', '<=', '>', '>='):
            raise CParseError('the loop condition must be one of < <= > >=', cmp_name.line)
        stop = self.const_int('the bound of the loop counter', cmp_name.line)
        self.take(';')
        step_tok = self.take()
        if step_tok.text != var_tok.text:
            raise CParseError('the loop step must advance the same counter', step_tok.line)
        nxt = self.take().text
        if nxt == '++':
            step = 1
        elif nxt == '--':
            step = -1
        elif nxt in ('+=', '-='):
            step = self.const_int('the loop step', step_tok.line)
            if nxt == '-=':
                step = -step
        else:
            raise CParseError('the loop step must be ++, --, += or -=', step_tok.line)
        self.take(')')

        if rel == '<=':
            stop = stop + 1
        elif rel == '>=':
            stop = stop - 1

        # Счётчик становится обычной вещественной величиной: внутри тела он
        # участвует в арифметике как число, и на каждом витке — своё.
        saved = dict(self.vars)
        counter_name = self.fresh(var_tok.text)
        self.vars[var_tok.text] = (counter_name, FLOAT64)
        body_template_start = self.i
        out = []
        count = 0
        i = start
        while (i < stop) if step > 0 else (i > stop):
            self.i = body_template_start
            self.vars[var_tok.text] = (counter_name, FLOAT64)
            body = self.block()
            out.append((i, body))
            count += 1
            i += step
            if count > 64:
                raise CParseError('the loop runs more than 64 times. Unrolling is the only '
                                  'sound way this method knows to handle a loop; for a longer '
                                  'one use a reduction scheme', tok.line)
        if count == 0:
            # тело всё равно нужно прочитать, чтобы не сбить позицию в токенах
            self.vars[var_tok.text] = (counter_name, FLOAT64)
            self.block()
            self.vars = saved
            return []
        self.vars.pop(var_tok.text, None)

        flat = []
        for value, body in out:
            subst = {counter_name: ('num', float(value))}
            for st in body:
                flat.append(_subst_counter(st, subst))
        return flat

    def const_int(self, what, line):
        tok = self.take()
        sign = 1
        if tok.text == '-':
            sign = -1
            tok = self.take()
        if tok.kind != 'num':
            raise CParseError('{} must be a literal constant, found {!r}. A loop whose trip '
                              'count is not known cannot be unrolled, and without unrolling '
                              'this method has nothing to say about it'
                              .format(what, tok.text), tok.line)
        try:
            value = float(tok.text.rstrip('fFlL'))
        except ValueError:
            raise CParseError('{} must be an integer'.format(what), tok.line)
        if value != int(value):
            raise CParseError('{} must be an integer, got {}'.format(what, value), tok.line)
        return sign * int(value)


def _subst_counter(st, env):
    from pareto.program import substitute
    kind = st[0]
    if kind == 'let':
        return ('let', st[1], substitute(st[2], env)) + tuple(st[3:])
    if kind == 'return':
        value = st[1]
        return ('return', Typed(substitute(value.tree, env), value.fmt))
    if kind == 'if':
        cond = st[1]
        return ('if', _subst_cond(cond, env),
                [_subst_counter(s, env) for s in st[2]],
                [_subst_counter(s, env) for s in st[3]])
    raise CParseError('unsupported statement inside a loop: {}'.format(kind))


def _subst_cond(cond, env):
    from pareto.program import substitute
    if cond[0] in ('and', 'or'):
        return (cond[0], _subst_cond(cond[1], env), _subst_cond(cond[2], env))
    return (cond[0], substitute(cond[1], env), substitute(cond[2], env))


def flatten_program(stmts):
    """Прямолинейная программа как ОДНО выражение. None, если есть ветвление.

    Нужно для подстановки вызова. Раньше подстановка требовала ровно один
    оператор `return`, и потому отказывала ЛЮБОЙ функции с локальной
    переменной — а так написано большинство: `cpFloat d = ...; return d*d;`.
    Локальные переменные подставляются внутрь, и смысл от этого не меняется:
    каждая из них по построению вычисляется один раз и больше не меняется
    (переприсваивание даёт новое внутреннее имя).
    """
    env, result = {}, None
    for st in stmts:
        if st[0] == 'let':
            env[st[1]] = st[2].tree if hasattr(st[2], 'tree') else st[2]
        elif st[0] == 'return':
            result = st[1].tree if hasattr(st[1], 'tree') else st[1]
        else:
            return None
    if result is None:
        return None

    def walk(node):
        if not isinstance(node, tuple):
            return node
        if node[0] == 'var':
            inner = env.get(node[1])
            return walk(inner) if inner is not None else node
        if node[0] == 'num':
            return node
        return (node[0],) + tuple(walk(k) for k in node[1:])

    return walk(result)


def as_select(stmts):
    """Свести тело из одного ветвления к выражению, если это выбор, а не разветвление.

    Понижение тернарника сделало b2MinFloat, b2MaxFloat и b2AbsFloat функциями с
    ветвлением — правильно по смыслу, но подстановка таких внутрь других функций
    сразу перестала работать: вставить две ветки в середину выражения нельзя.

    Однако ровно эти три вида ветвления выражением как раз записываются, и в
    IEEE-754 ровно теми операциями, что у нас уже есть:
        a < b ? a : b   это fmin(a, b)
        a > b ? a : b   это fmax(a, b)
        a < 0 ? -a : a  это fabs(a)
    Сведение делается только при ПОЛНОМ совпадении ветвей с частями сравнения:
    любое отличие — и мы отказываемся, потому что угадывать здесь нельзя. Что
    сведение верно побитово, проверяет сверка с компилятором, а не моё мнение.
    """
    if len(stmts) != 1 or stmts[0][0] != 'if':
        return None
    _, cond, then_part, else_part = stmts[0]
    if (len(then_part) != 1 or then_part[0][0] != 'return'
            or len(else_part) != 1 or else_part[0][0] != 'return'):
        return None
    if cond[0] not in ('<', '<=', '>', '>='):
        return None
    left, right = cond[1], cond[2]
    a = then_part[0][1]
    b = else_part[0][1]
    at = a.tree if hasattr(a, 'tree') else a
    bt = b.tree if hasattr(b, 'tree') else b
    lt = left.tree if hasattr(left, 'tree') else left
    rt = right.tree if hasattr(right, 'tree') else right

    def is_zero(node):
        return node[0] == 'num' and float(node[1]) == 0.0

    def negates(node, base):
        if node[0] == 'neg' and node[1] == base:
            return True
        # Запись через вычитание из нуля встречается не реже.
        return (node[0] == '-' and is_zero(node[1]) and node[2] == base)

    fmt = a.fmt if hasattr(a, 'fmt') else None
    less = cond[0] in ('<', '<=')

    # модуль: a < 0 ? -a : a
    if is_zero(rt) and at is not None and negates(at, lt) and bt == lt:
        return ('fabs', lt), fmt
    # модуль наоборот: a > 0 ? a : -a
    if is_zero(rt) and not less and at == lt and negates(bt, lt):
        return ('fabs', lt), fmt
    # минимум и максимум
    if at == lt and bt == rt:
        return (('fmin' if less else 'fmax'), lt, rt), fmt
    if at == rt and bt == lt:
        return (('fmax' if less else 'fmin'), lt, rt), fmt
    return None


def _lower_cond(cond, then_part, else_part):
    """Раскрыть && и || во вложенные ветвления.

    `if (a && b) T else E` — это `if (a) { if (b) T else E } else E`, а
    `if (a || b) T else E` — это `if (a) T else { if (b) T else E }`. Ветка E
    дублируется, и это правильно: каждый путь исполнения обязан быть представлен
    отдельно, иначе его условие достижимости не выразить.
    """
    kind = cond[0]
    if kind == 'and':
        return _lower_cond(cond[1], _lower_cond(cond[2], then_part, else_part), else_part)
    if kind == 'or':
        return _lower_cond(cond[1], then_part, _lower_cond(cond[2], then_part, else_part))
    return [('if', cond, then_part, else_part)]


# ---------- разбор файла ----------
_ANNOTATION = re.compile(
    r'@(?:domain|range)\s+([A-Za-z_][A-Za-z_0-9]*)\s*[:=]?\s*'
    r'\[?\s*(-?[0-9.eE+-]+)\s*(?:\.\.|,)\s*(-?[0-9.eE+-]+)\s*\]?')


def read_domains(src, before=None):
    """Диапазоны входов из комментариев: `// @domain x: 1 .. 2`.

    Диапазоны обязательны, и это не прихоть: без них нет ни одной границы ошибки,
    которую можно доказать. Держать их рядом с кодом, а не в командной строке,
    удобнее ровно потому, что они часть контракта функции, а не запуска.

    `before` ограничивает поиск текстом ДО указанной позиции, и это существенно, а
    не косметика: в файле с несколькими функциями у каждой свои диапазоны для
    одноимённых аргументов. Без ограничения выигрывал последний в файле, и функция
    молча анализировалась на чужой области — граница печаталась верная, но не для
    того, что человек спрашивал. Побеждает ближайшее объявление выше по тексту.
    """
    text = src if before is None else src[:before]
    out = {}
    for name, lo, hi in _ANNOTATION.findall(text):
        try:
            out[name] = (float(lo), float(hi))
        except ValueError:
            continue
    return out


def _signature_re(types):
    """Регулярка сигнатуры по НАБОРУ вещественных типов, а не по словам double и float.

    05.10.2026: Chipmunk2D объявляет cpFloat через typedef, и поиск по двум
    буквальным словам не нашёл в проекте ни одной функции — ноль из тридцати трёх
    файлов. Это худший вид отказа: инструмент молчит, и молчание читается как
    «тут всё чисто».
    """
    alt = "|".join(re.escape(n).replace(r"\ ", r"\s+")
                   for n in sorted(types, key=len, reverse=True))
    return re.compile(r"\b(" + alt + r")\s+([A-Za-z_][A-Za-z_0-9]*)\s*\(([^)]*)\)\s*\{")


_SIGNATURE = _signature_re(FLOAT_TYPES)

def _any_signature_re(types, table):
    """Сигнатура функции, возвращающей скаляр ИЛИ структуру.

    Пока перечислитель знал только скалярные возвраты, вся векторная математика
    была для нас невидима — и, что хуже, не попадала даже в число отвергнутых.
    Цифра охвата считалась по урезанной вселенной: в raymath.h 109 функций из 146
    возвращают Vector2, Matrix или Quaternion.
    """
    names = sorted(set(types) | set(table), key=len, reverse=True)
    if not names:
        return None
    alt = "|".join(re.escape(n).replace(r"\ ", r"\s+") for n in names)
    return re.compile(r"\b(" + alt + r")\s+([A-Za-z_][A-Za-z_0-9]*)\s*\(([^)]*)\)\s*\{")


def struct_result_fields(src, name, types=None, table=None):
    """Поля результата функции, если она возвращает структуру. Иначе пустой список.

    Нужно, чтобы вызывающая сторона знала, сколько скалярных выходов разбирать:
    одна функция, отдающая Vector3, — это три отдельные программы с тремя
    отдельными границами.
    """
    types = types or scalar_types(src)
    merged = StructTable()
    # Имена цикла нарочно не name: параметр с этим именем означает ФУНКЦИЮ, и
    # затенение его уже стоило молчаливого «структурных полей нет» — сравнение
    # шло с последним именем структуры вместо имени функции, и 110 функций
    # raymath.h отвергались без видимой причины.
    for _tn, _flds in (table or {}).items():
        merged[_tn] = _flds
    merged.conflicts.update(getattr(table, 'conflicts', {}) or {})
    local = structs(src, types)
    for _tn, _flds in local.items():
        _record_struct(merged, _tn, _flds, keep_first=True)
    merged.conflicts.update(getattr(local, 'conflicts', {}) or {})
    table = merged
    rx = _any_signature_re(types, table)
    if rx is None:
        return []
    for m in rx.finditer(src):
        if m.group(2) != name:
            continue
        tname = " ".join(m.group(1).split())
        if tname in types:
            return []
        fields = table.get(tname) or {}
        return [f for f, fmt in fields.items() if isinstance(fmt, Format)]
    return []



_STRUCT = re.compile(
    r"typedef\s+struct\s*(?:[A-Za-z_]\w*\s*)?\{(.*?)\}\s*([A-Za-z_]\w*)\s*;", re.S)
# Вторая форма объявления: `struct cpBody { ... };` без typedef. Chipmunk пишет
# именно так, и без неё указатель на cpBody остаётся для нас пустым типом —
# 05.10.2026 на этом не сдвинулся охват, хотя указатели уже читались.
# Тело БЕЗ вложенных фигурных скобок. С нежадным `.*?` выражение захватывало
# несколько структур подряд: поиск шёл от `struct b2Vec2 {` до первой `};` в
# файле, и в поля b2Vec2 попадали lowerBound и upperBound из b2AABB. Пока
# действовало правило «первое объявление главнее», мусор не был виден; стоило
# начать замечать расхождения объявлений — и он вылез сразу.
_STRUCT_NAMED = re.compile(
    r"struct\s+([A-Za-z_]\w*)\s*\{([^{}]*)\}\s*;", re.S)
_ALIAS = re.compile(r"typedef\s+([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*;")
_FIELD = re.compile(r"\b(double|float)\s+([^;\{\}]+);")


_TYPEDEF = re.compile(r"typedef\s+([A-Za-z_][A-Za-z_0-9\s]*?)\s+([A-Za-z_]\w*)\s*;")


def scalar_types(src, base=None):
    """Псевдонимы вещественных типов: `typedef float cpFloat;` и цепочки из них.

    Без этого настоящие проекты для нас невидимы. Chipmunk2D объявляет cpFloat,
    box2d — свои имена, и поиск по буквальным словам double и float не находит в
    таком файле ни одной функции: не «мы её не поняли», а «мы её не увидели».
    Разница принципиальная — молчание инструмента читается как «тут всё чисто».
    """
    out = dict(base or FLOAT_TYPES)
    for _ in range(3):                      # цепочки typedef в два-три звена
        for m in _TYPEDEF.finditer(src):
            src_t = " ".join(m.group(1).split())
            dst_t = m.group(2)
            if src_t in out and dst_t not in out:
                out[dst_t] = out[src_t]
    return out


_DEFINE = re.compile(r"^\s*#\s*define\s+([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*$", re.M)


def macro_aliases(texts):
    """Простые псевдонимы вида `#define cpfsqrt sqrt`.

    Chipmunk зовёт математику только через такие имена, и без них `cpvlength`
    отвергается на ровном месте: внутри честный sqrt, просто названный иначе.
    Разворачиваем в цепочку, но только имя-в-имя — макросы с аргументами и с
    телом-выражением не трогаем, про них ничего доказать нельзя.
    """
    out = {}
    for src in texts:
        for m in _DEFINE.finditer(src):
            out.setdefault(m.group(1), m.group(2))
    for _ in range(3):
        for k, v in list(out.items()):
            if v in out and out[v] != k:
                out[k] = out[v]
    return out


# Числовой `#define`. Скобки и приведение типа вокруг значения допускаются:
# в Chipmunk число пи записано как `((cpFloat)3.14159...)`, и из-за одних только
# скобок с приведением пять его функций отвергались сообщением «неизвестное имя
# CP_PI». Приведение здесь значимо и для формата: `(float)3.14` это float32,
# а `3.14` без суффикса — double.
_DEF_NUM = re.compile(
    r"^\s*#\s*define\s+([A-Za-z_]\w*)\s+"
    r"\(*\s*(?:\(\s*([A-Za-z_]\w*)\s*\)\s*)?"
    r"([-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)([fFlL]?)\s*\)*\s*$",
    re.M)
_GLOBAL = re.compile(
    r"^\s*(?:static\s+)?(?:const\s+)?([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*=\s*"
    r"([-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)([fFlL]?)\s*;", re.M)


class ConstTable(dict):
    """Числовые константы файла вместе с их форматом.

    Обычный dict, чтобы не ломать ни одного места, которое просто берёт значение,
    плюс поле fmts с форматом там, где он известен из записи литерала.

    Формат здесь не украшение. В box2d написано `2.0f * B2_PI * hertz`, где
    B2_PI это `3.14159265359f`. Компилятор округляет произведение двух float-ов
    до float32 ДО умножения на hertz, а мы держали константу в двойной точности
    и округляли позже. Разница ровно в одном округлении — и она всплыла при
    побитовой сверке с clang 06.10.2026. Это худший вид ошибки: граница верна,
    но для выражения, которого в коде нет.
    """

    __slots__ = ('fmts',)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fmts = {}


def _suffixed(text, suffix, table, name, declared=None):
    """Значение литерала с учётом суффикса: у `f` оно округляется к float32."""
    value = float(text)
    fmt = FLOAT32 if suffix in ('f', 'F') else declared
    if fmt is FLOAT32:
        value = FLOAT32.round(value)
        table.fmts[name] = FLOAT32
    return value


def constants(texts, types):
    """Числовые константы уровня файла: `#define` и инициализированные глобалы.

    В настоящем коде половина формул опирается на такие имена, и отвергать функцию
    из-за `b2_lengthUnitsPerMeter` — отвергать её из-за ничего. Берём только те,
    у которых значение — число прямо в объявлении: вычисляемое выражение или
    значение, присваиваемое где-то ещё, константой не является и сюда не попадает.
    """
    out = ConstTable()
    for src in texts:
        for m in _DEF_NUM.finditer(src):
            name, cast, val, suffix = (m.group(1), m.group(2), m.group(3),
                                       m.group(4))
            if name in out:
                continue
            declared = None
            if cast:
                if cast == 'float' or types.get(cast) is FLOAT32:
                    declared = FLOAT32
                elif cast in ('double', 'long') or types.get(cast) is FLOAT64:
                    declared = FLOAT64
                else:
                    continue        # приведение к целому или к незнакомому типу
            out[name] = _suffixed(val, suffix, out, name, declared=declared)
        for m in _GLOBAL.finditer(src):
            tname, name, val, suffix = (m.group(1), m.group(2), m.group(3), m.group(4))
            if name in out:
                continue
            if tname in types or tname in ("int", "unsigned", "long", "short"):
                fmt = FLOAT32 if types.get(tname) is FLOAT32 else None
                out[name] = _suffixed(val, suffix, out, name, declared=fmt)
    return out


_GLOBAL_DECL = re.compile(
    r"^\s*(?:static\s+)?(?:const\s+)?([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*(?:=[^;]*)?;", re.M)


def globals_of(texts, types, table):
    """Глобальное состояние как вход функции, а не как повод для отказа.

    raylib читает CORE.Time.frame и GESTURES.current — шестьдесят одна функция
    отвергалась именно из-за этого. Но такое имя не «неизвестное», а настоящий
    вход расчёта: результат от него зависит, значит и граница ошибки зависит.
    Честно — считать его свободной величиной и требовать для неё диапазон, как
    для любого аргумента. Молча подставить значение было бы обманом: мы не знаем,
    что туда положили до вызова.
    """
    out = {}
    for src in texts:
        for m in _GLOBAL_DECL.finditer(src):
            tname, name = m.group(1), m.group(2)
            if name in out or tname in ('return', 'typedef', 'struct', 'union', 'else'):
                continue
            if tname in types:
                out[name] = types[tname]
            elif tname in table:
                flat = {}
                _expand(table, tname, name + '.', flat)
                out.update(flat)
    return out


def make_resolver(texts, types, table, depth=4, macros=None, consts=None,
                  globs=None):
    """Разрешатель вызовов: по имени возвращает разобранную соседнюю функцию.

    Нужен, чтобы `cpvlength(v)` не отвергался только потому, что внутри зовёт
    `cpvdot`. Разбор идёт по тем же правилам, с тем же контекстом и с тем же
    разрешателем — то есть вложенные вызовы тоже подставляются.

    Глубина ограничена: рекурсивная функция иначе уведёт разбор в бесконечность,
    а доказать про неё этими средствами всё равно нечего. На пределе возвращаем
    None, и вызов честно отвергается как обычный незнакомый.
    """
    cache = {}
    stack = []

    def resolve(name, field=None):
        # Поле нужно для вызываемых функций, отдающих структуру: `b2Vec2 p =
        # b2Add(a, b);` это две скалярные программы, по одной на координату.
        key = (name, field)
        if key in cache:
            return cache[key]
        if len(stack) >= depth or name in stack:
            return None
        stack.append(name)
        try:
            for src in texts:
                if not re.search(r"\b" + re.escape(name) + r"\s*\(", src):
                    continue
                try:
                    prog = parse_function(src, name, types, table, resolve, macros,
                                          consts, globs, field=field)
                except Exception:
                    continue
                cache[key] = prog
                return prog
            cache[key] = None
            return None
        finally:
            stack.pop()

    return resolve


def collect_context(paths):
    """Типы и структуры всего проекта, а не одного файла.

    Структуры и псевдонимы живут в заголовках, а разбираем мы .c — поэтому читать
    только текущий файл значит отвергать почти всё по причине «тип не объявлен
    здесь». Собираем один раз по дереву и передаём в разбор.
    """
    texts = []
    for path in paths:
        try:
            texts.append(Path(path).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    # Сначала ВСЕ типы: поле структуры может быть объявлено псевдонимом, который
    # определён в другом заголовке, и одного прохода тут не хватает.
    types = dict(FLOAT_TYPES)
    for _ in range(2):
        for src in texts:
            types = scalar_types(src, types)
    # Таблица проекта хранит и СПОРЫ объявлений. Их надо переносить между
    # файлами: одно и то же имя под условной сборкой встречается в одном
    # заголовке, а зависящая от него функция — в другом. Терялся список здесь, и
    # отказ по спорному типу не срабатывал, хотя сам спор уже находился.
    table = StructTable()
    for src in texts:
        part = structs(src, types)
        for name, fields in part.items():
            _record_struct(table, name, fields, keep_first=True)
        table.conflicts.update(getattr(part, 'conflicts', {}) or {})
    return types, table


_FIELD_ANY = re.compile(r"\b([A-Za-z_]\w*)\s+([A-Za-z_][^;\{\}]*);")


def _expand(table, tname, prefix, out, depth=0):
    """Разложить структуру в плоский набор скалярных полей.

    У cpBody поле `cpVect p`, и выражение body->p.x раньше отвергалось: в таблице
    лежали только вещественные поля, а составные пропускались. Разворачиваем
    вглубь с ограничением: кольцевые ссылки через указатели в структурах бывают,
    и без предела разбор уходит в бесконечность.
    """
    if depth > 3 or tname not in table:
        return
    for fld, fmt in table[tname].items():
        key = prefix + fld
        if isinstance(fmt, tuple) and fmt[0] == 'struct':
            _expand(table, fmt[1], key + '.', out, depth + 1)
        else:
            out[key] = fmt


# Логические поля идут сюда же: `bool` это 0 или 1, величина точная, и
# отвергать функцию из-за `joint->enableSpring` значит терять её на флаге.
# Псевдонимы вроде b2Bool и cpBool тоже встречаются, поэтому имя допускается
# любое, оканчивающееся на bool или Bool.
_INT_FIELD = re.compile(
    r"\b(?:unsigned\s+|signed\s+)?(int|long|short|char|size_t|ptrdiff_t|"
    r"u?int(?:8|16|32|64)_t|_Bool|[A-Za-z_]\w*[bB]ool)\s+([^;\{\}]+);")


def _field_re(types):
    alt = "|".join(re.escape(n).replace(r"\ ", r"\s+")
                   for n in sorted(types, key=len, reverse=True))
    return re.compile(r"\b(" + alt + r")\s+([^;\{\}]+);")


def _record_struct(out, name, fields, keep_first=False):
    """Положить структуру, а при РАЗНЫХ объявлениях одного имени — запомнить спор.

    keep_first нужен для второго прохода по именованным структурам: у typedef-а
    объявление полнее, и перезаписывать им уже найденное нельзя. Спор при этом
    всё равно отмечается — именно он и важен.
    """
    old = out.get(name)
    if old is not None and old != fields:
        differing = sorted(set(old) ^ set(fields))
        if not differing:
            differing = sorted(f for f in old
                               if f in fields and old[f] is not fields[f])
        if differing:
            out.conflicts[name] = differing
        if keep_first:
            return
    elif old is not None and keep_first:
        return
    out[name] = fields


class StructTable(dict):
    """Структуры проекта плюс имена, объявленные ПО-РАЗНОМУ дважды.

    Мы не выполняем препроцессор, и это имеет цену. В box2d написано так:

        #if defined( BOX2D_DOUBLE_PRECISION )
        typedef struct b2Pos { double x, y; } b2Pos;
        #else
        typedef struct b2Pos { float x, y; } b2Pos;
        #endif

    Оба объявления лежат в одном файле. Мы читали их подряд и оставляли то, что
    попалось последним, — то есть выбирали точность за компилятора. 07.10.2026
    побитовая сверка показала итог: наш разбор считал разность в двойной
    точности, компилятор в одинарной, и пять функций box2d расходились с ним.

    Молча угадывать здесь нельзя: граница вышла бы верной для программы, которой
    в сборке нет. Поэтому противоречие запоминается, и функция, зависящая от
    такого типа, честно отвергается с указанием имени — человек знает свой флаг
    сборки и может сказать его нам.
    """

    __slots__ = ('conflicts',)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.conflicts = {}


def structs(src, types=None):
    """Структуры файла как наборы вещественных полей.

    Нужно затем, чтобы `Vector3 v` в сигнатуре перестал быть стеной. Поля
    становятся обычными скалярами с именами `v.x`, `v.y`, `v.z`, и дальше всё
    работает как раньше. Типы полей берутся из объявления в том же файле, а не
    угадываются: инструмент, который предположил тип поля, посчитает границу для
    программы, которой в файле нет.

    Нефещественные поля (int, указатели, вложенные структуры) просто не попадают
    в таблицу — обращение к ним потом честно отвергается по имени.
    """
    types = types or FLOAT_TYPES
    field_re = _field_re(types)
    out = StructTable()
    for m in _STRUCT.finditer(src):
        body, name = m.group(1), m.group(2)
        fields = {}
        for fm in field_re.finditer(body):
            fmt = types[" ".join(fm.group(1).split())]
            for raw in fm.group(2).split(","):
                fn = raw.strip()
                if re.fullmatch(r"[A-Za-z_]\w*", fn or ""):
                    fields[fn] = fmt
        for fm in _INT_FIELD.finditer(body):
            # Целое поле — такая же величина расчёта, как вещественное: ниже 2^53
            # оно представимо точно. Без него music.frameCount оставался
            # «неизвестным именем», и на этом падали сто тринадцать функций.
            for raw in fm.group(2).split(','):
                fn = raw.strip().lstrip('*').strip()
                if re.fullmatch(r"[A-Za-z_]\w*", fn or "") and fn not in fields:
                    fields[fn] = FLOAT64
        for fm in _FIELD_ANY.finditer(body):
            tname2, rest = fm.group(1), fm.group(2)
            if tname2 in ('struct', 'union'):
                # `struct cpShapeMassInfo massInfo;` — слово struct перед именем
                # типа в C законно и встречается. Раньше такое поле просто
                # пропускалось, и выражение shape->massInfo.m отвергалось как
                # неизвестное имя: поля не было в таблице. На Chipmunk это пять
                # функций, в которых есть что считать.
                head = rest.split(None, 1)
                if len(head) != 2:
                    continue
                tname2, rest = head[0], head[1]
            elif tname2 in types or tname2 in ('const', 'static', 'unsigned'):
                continue
            for raw in rest.split(','):
                fn = raw.strip().lstrip('*').strip()
                if re.fullmatch(r"[A-Za-z_]\w*", fn or "") and fn not in fields:
                    fields[fn] = ('struct', tname2)
        if fields:
            _record_struct(out, name, fields)
    for m in _STRUCT_NAMED.finditer(src):
        name, body = m.group(1), m.group(2)
        fields = {}
        for fm in field_re.finditer(body):
            fmt = types[" ".join(fm.group(1).split())]
            for raw in fm.group(2).split(","):
                fn = raw.strip()
                if re.fullmatch(r"[A-Za-z_]\w*", fn or ""):
                    fields[fn] = fmt
        for fm in _INT_FIELD.finditer(body):
            # Целое поле — такая же величина расчёта, как вещественное: ниже 2^53
            # оно представимо точно. Без него music.frameCount оставался
            # «неизвестным именем», и на этом падали сто тринадцать функций.
            for raw in fm.group(2).split(','):
                fn = raw.strip().lstrip('*').strip()
                if re.fullmatch(r"[A-Za-z_]\w*", fn or "") and fn not in fields:
                    fields[fn] = FLOAT64
        for fm in _FIELD_ANY.finditer(body):
            tname2, rest = fm.group(1), fm.group(2)
            if tname2 in ('struct', 'union'):
                # То же, что и для typedef-структур: слово struct перед именем
                # типа законно и встречается. У cpShape поле объявлено как
                # `struct cpShapeMassInfo massInfo;`, и из-за этого
                # shape->massInfo.m отвергалось как неизвестное имя. Один и тот
                # же пробел пришлось закрыть в двух местах — цикла разбора
                # структур здесь два, для typedef и для именованных, и я
                # поправил сначала только первый.
                head = rest.split(None, 1)
                if len(head) != 2:
                    continue
                tname2, rest = head[0], head[1]
            elif tname2 in types or tname2 in ('const', 'static', 'unsigned'):
                continue
            for raw in rest.split(','):
                fn = raw.strip().lstrip('*').strip()
                if re.fullmatch(r"[A-Za-z_]\w*", fn or "") and fn not in fields:
                    fields[fn] = ('struct', tname2)
        if fields:
            _record_struct(out, name, fields, keep_first=True)
    # Псевдонимы вида `typedef Vector4 Quaternion;` — тот же набор полей.
    for _ in range(3):
        for m in _ALIAS.finditer(src):
            src_t, dst_t = m.group(1), m.group(2)
            if src_t not in out:
                continue
            if dst_t not in out:
                out[dst_t] = dict(out[src_t])
            elif out[dst_t] != out[src_t]:
                # Имя объявлено и структурой, и псевдонимом на ДРУГУЮ структуру.
                # В box2d это b2Pos: под BOX2D_DOUBLE_PRECISION он struct с
                # double, без него — псевдоним b2Vec2 с float. Выбрать за
                # компилятора мы не можем и не будем.
                out.conflicts[dst_t] = sorted(
                    set(out[dst_t]) | set(out[src_t]))
    return out


def functions(src, types=None, table=None):
    """Имена вещественных функций файла — чтобы можно было выбрать нужную.

    Со структурным возвратом тоже: `Vector2 Vector2Add(...)` это такая же
    вещественная функция, просто у неё два скалярных выхода вместо одного.
    Передайте table, иначе структурные возвраты останутся невидимыми — ровно
    как было до 07.10.2026.
    """
    types = types or scalar_types(src)
    if table:
        rx = _any_signature_re(types, table)
        if rx is not None:
            return [m.group(2) for m in rx.finditer(src)]
    return [m.group(2) for m in _signature_re(types).finditer(src)]


def parse_function(src, name=None, types=None, table=None, resolve=None,
                   macros=None, consts=None, globs=None, field=None):
    """Разобрать одну функцию файла в программу.

    Возвращает словарь: имя, формат результата, аргументы (имя -> формат),
    операторы в формате pareto/program.py и найденные в комментариях диапазоны.
    """
    # Типы и структуры могут прийти снаружи — собранные по всему проекту, а не по
    # одному файлу: объявления живут в заголовках, а разбираем мы .c.
    types = types or scalar_types(src)
    # Таблица структур с сохранением СПОРОВ объявлений: обычный dict терял
    # список, и проверка ниже оказывалась мёртвой.
    merged = StructTable()
    for _tn, _flds in (table or {}).items():
        merged[_tn] = _flds
    merged.conflicts.update(getattr(table, 'conflicts', {}) or {})
    local = structs(src, types)
    for _tn, _flds in local.items():
        _record_struct(merged, _tn, _flds, keep_first=True)
    merged.conflicts.update(getattr(local, 'conflicts', {}) or {})
    table = merged
    rx = _any_signature_re(types, table) or _signature_re(types)
    matches = list(rx.finditer(src))
    if not matches:
        raise CParseError('no function returning double or float found in the file')
    chosen = None
    for m in matches:
        if name is None or m.group(2) == name:
            chosen = m
            break
    if chosen is None:
        raise CParseError('function {!r} not found. The file defines: {}'.format(
            name, ', '.join(m.group(2) for m in matches)))

    ret_tname = " ".join(chosen.group(1).split())
    fname = chosen.group(2)
    params = chosen.group(3).strip()
    line0 = src.count('\n', 0, chosen.start()) + 1

    # Тип, объявленный в проекте по-разному под условной сборкой, — повод
    # отказаться, а не угадать. Иначе граница окажется верной для программы,
    # которой в сборке нет; ровно это и случилось с b2Pos до 07.10.2026: под
    # BOX2D_DOUBLE_PRECISION это структура с double, без него — псевдоним
    # b2Vec2 с float. Мы читали оба объявления и брали одно из них молча.
    conflicts = getattr(table, 'conflicts', {}) or {}
    if conflicts:
        used = {ret_tname}
        for part in params.split(','):
            for w in part.replace('*', ' ').split():
                used.add(w)
        bad = sorted(used & set(conflicts))
        if bad:
            raise CParseError(
                'type {} is declared more than once with different fields in this '
                'project (conditional compilation). Which one applies depends on '
                'your build flags, and guessing would mean bounding a different '
                'program'.format(', '.join(bad)), line0)
    want_field = None
    if ret_tname in types:
        ret_fmt = types[ret_tname]
    else:
        # Возврат структуры. Разбираем ОДНО поле: у каждого своё выражение и своя
        # граница, а вся остальная машина продолжает получать скалярную
        # программу. Поле обязательно указать явно — угадывать «наверное, первое»
        # значило бы молча посчитать не то, что спросили.
        fields = {f: fm for f, fm in (table.get(ret_tname) or {}).items()
                  if isinstance(fm, Format)}
        if not fields:
            raise CParseError('function {} returns {}, and its scalar fields are '
                              'unknown here'.format(chosen.group(2), ret_tname))
        if field is None:
            raise CParseError(
                'function {} returns {}: specify which field to analyse, one of {}'
                .format(chosen.group(2), ret_tname, ', '.join(fields)))
        if field not in fields:
            raise CParseError('{} has no scalar field {!r}; it has {}'.format(
                ret_tname, field, ', '.join(fields)))
        ret_fmt = fields[field]
        want_field = field

    args = {}
    order = []
    opaque = set()
    if params and params != 'void':
        for part in params.split(','):
            # const и volatile на тип не влияют — убираем, иначе `const Vector3 v`
            # отваливается только из-за лишнего слова в сигнатуре.
            bits = [w for w in part.replace('*', ' * ').split()
                    if w not in ('const', 'volatile', 'register')]
            if '[' in part or ']' in part:
                raise CParseError('function {} takes an array. A reduction over an array is '
                                  'a separate mode'.format(fname), line0)
            if '*' in bits:
                # Указатель на структуру — это чтение её полей, и для границы ошибки
                # он ничем не отличается от структуры по значению: в дереве живут
                # только поля. Псевдонимы (два указателя на один объект) границу
                # только завышают, потому что мы считаем поля независимыми, а
                # завышение — законная сторона. Запись через указатель отвергается
                # отдельно: память мы не моделируем.
                bits = [w for w in bits if w != '*']
                if len(bits) != 2:
                    raise CParseError(
                        'function {} takes a pointer the shape of which is not readable here'
                        .format(fname), line0)
                if bits[0] in types or bits[0] in INT_TYPES:
                    # Указатель на число — это массив или выходной параметр.
                    # И то и другое мы не моделируем, отказ остаётся.
                    raise CParseError(
                        'function {} takes a pointer to a number: that is an array or an '
                        'output parameter, and neither is modelled here'.format(fname), line0)
                if bits[0] not in table:
                    # Непрозрачный тип: объявлен вперёд, тело скрыто (cpSpace, b2World).
                    # Полей мы не знаем, но код их читает, и каждое такое чтение —
                    # неизвестная величина. Регистрируем лениво, при первом обращении:
                    # так в список входов попадут ровно те поля, которые функция
                    # действительно трогает, а не выдуманный нами состав структуры.
                    opaque.add(bits[1])
                    continue
                flat = {}
                _expand(table, bits[0], bits[1] + '->', flat)
                for key, fmt in flat.items():
                    args[key] = fmt
                    order.append(key)
                continue
            if len(bits) != 2:
                raise CParseError('parameter {!r} of {} is not a plain double or float'
                                  .format(part.strip(), fname), line0)
            tname, vname = bits[0], bits[1]
            if tname in INT_TYPES:
                args[vname] = FLOAT64
                order.append(vname)
                continue
            if tname in types:
                args[vname] = types[tname]
                order.append(vname)
            elif tname in table:
                # Структура входит как набор своих вещественных полей: Vector3 v
                # превращается в v.x, v.y, v.z. Диапазон задаётся каждому полю
                # отдельно — у координат он обычно разный, и усреднять их значит
                # соврать в обе стороны сразу.
                flat = {}
                _expand(table, tname, vname + '.', flat)
                for key, fmt in flat.items():
                    args[key] = fmt
                    order.append(key)
            else:
                # Тип неизвестен: объявлен в другом месте или вообще не числовой.
                # Отказывать нельзя — сто тринадцать функций трёх проектов падали
                # именно здесь, хотя читают из такого параметра обычные числа.
                # Поля заводятся лениво, по факту обращения: в список входов
                # попадёт ровно то, что функция действительно трогает.
                opaque.add(vname)
                continue

    body = _body_text(src, chosen.end() - 1)
    toks = tokenize(body)
    p = _Parser(toks, resolve=resolve, macros=macros, consts=consts,
                globs=globs, float_types=types)
    p.want_field = want_field
    p.structs = table
    p.opaque = opaque
    for a in order:
        p.vars[a] = (a, args[a])
    stmts = p.block()
    if p.peek().kind != 'eof':
        raise CParseError('unexpected {!r} after the end of the function body'
                          .format(p.peek().text), p.peek().line)

    stmts = _finish_returns(stmts, ret_fmt)
    for k, v in p.extra_inputs.items():       # глобалы стали входами
        if k not in args:
            args[k] = v
            order.append(k)
    return {'name': fname, 'result': ret_fmt, 'args': args, 'order': order,
            'stmts': stmts, 'domains': read_domains(src, before=chosen.start()),
            'body_span': body_span(src, chosen.end() - 1), 'line': line0}


def body_span(src, brace_pos):
    """Границы тела функции в исходнике: от `{` до парной `}` включительно.

    Нужны не разбору, а обратной записи: чтобы вставить переписанное тело в файл
    пользователя, надо знать, какие именно байты заменять. Всё остальное в файле —
    комментарии, includes, соседние функции — остаётся при этом дословно на месте.
    """
    depth = 0
    i = brace_pos
    n = len(src)
    while i < n:
        c = src[i]
        # Комментарии пропускаются целиком. Без этого апостроф в обычной
        # английской фразе — `// Put the ray into the edge's frame` — принимался
        # за открытие символьного литерала и съедал полтела функции вместе с
        # настоящей закрывающей скобкой. Четыре функции box2d отвергались
        # сообщением «тело без закрывающей скобки», и причина выглядела как
        # неразбираемый код, хотя код был безупречен.
        if c == '/' and i + 1 < n and src[i + 1] == '/':
            j = src.find(chr(10), i)
            i = n if j < 0 else j + 1
            continue
        if c == '/' and i + 1 < n and src[i + 1] == '*':
            j = src.find('*/', i + 2)
            i = n if j < 0 else j + 2
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return (brace_pos, i + 1)
        elif c == '"' or c == "'":
            # Строка внутри тела — почти всегда текст проверки или лога, и к
            # арифметике отношения не имеет. Раньше она отвергала функцию целиком
            # (23 функции Chipmunk из 110). Здесь просто проходим её насквозь,
            # чтобы не сбиться на скобках внутри кавычек; что делать с самим
            # оператором, решает разбор ниже — и неизвестный вызов он по-прежнему
            # отвергает.
            quote = c
            i += 1
            while i < len(src) and src[i] != quote:
                i += 2 if src[i] == chr(92) else 1
            i += 1
            continue
        i += 1
    raise CParseError('the function body has no matching closing brace')


def _body_text(src, brace_pos):
    """Текст тела функции вместе с фигурными скобками, по балансу скобок."""
    lo, hi = body_span(src, brace_pos)
    return src[lo:hi]


def _finish_returns(stmts, ret_fmt):
    """Возврат приводится к типу результата функции, как того требует C."""
    out = []
    for st in stmts:
        if st[0] == 'return':
            value = st[1]
            tree = value.tree if value.fmt is ret_fmt else _wrap(value.tree, ret_fmt)
            out.append(('return', tree))
        elif st[0] == 'if':
            out.append(('if', st[1], _finish_returns(st[2], ret_fmt),
                        _finish_returns(st[3], ret_fmt)))
        else:
            out.append(st)
    return out
