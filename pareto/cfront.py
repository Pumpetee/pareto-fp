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

from pareto.precision import FLOAT32, FLOAT64, round_op_for

FLOAT_TYPES = {'double': FLOAT64, 'float': FLOAT32, 'long double': FLOAT64}

# Функции, которые мы умеем анализировать. Суффикс f — вариант для float.
MATH1 = {'sqrt': 'sqrt', 'exp': 'exp', 'log': 'log', 'expm1': 'expm1', 'log1p': 'log1p'}
MATH2 = {'hypot': 'hypot'}
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
    | (?P<lcomment>//[^\n]*)
    | (?P<bcomment>/\*.*?\*/)
    | (?P<num>(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?[fFlL]?)
    | (?P<name>[A-Za-z_][A-Za-z_0-9]*)
    | (?P<op><<=|>>=|<=|>=|==|!=|&&|\|\||\+\+|--|\+=|-=|\*=|/=|[-+*/%<>=(){};,!&|?:\[\].])
""", re.VERBOSE | re.DOTALL)


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
        if kind in ('ws', 'lcomment', 'bcomment'):
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
    op = round_op_for(fmt)
    return tree if op is None else (op, tree)


def _binary(op, a, b):
    """Обычное арифметическое преобразование C: шире из двух типов, и округление в него."""
    fmt = FLOAT64 if (a.fmt is FLOAT64 or b.fmt is FLOAT64) else a.fmt
    return Typed(_wrap((op, a.tree, b.tree), fmt), fmt)


class _Parser:
    def __init__(self, toks):
        self.t, self.i = toks, 0
        self.vars = {}          # имя в C -> (имя в дереве, формат)
        self.counter = 0

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
            if nxt.kind == 'name' and nxt.text in FLOAT_TYPES and self.peek(2).text == ')':
                self.take('(')
                fmt = FLOAT_TYPES[self.take().text]
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
            if tok.text not in self.vars:
                raise CParseError('unknown name {!r}: it is neither an argument nor a local '
                                  'variable of this function'.format(tok.text), tok.line)
            name, fmt = self.vars[tok.text]
            return Typed(('var', name), fmt)
        raise CParseError('cannot read an expression starting at {!r}'.format(tok.text),
                          tok.line)

    def literal(self, tok):
        text = tok.text
        fmt = FLOAT64
        if text[-1] in 'fF':
            text, fmt = text[:-1], FLOAT32
        elif text[-1] in 'lL':
            text = text[:-1]
        try:
            value = float(text)
        except ValueError:
            raise CParseError('cannot read the number {!r}'.format(tok.text), tok.line)
        if fmt is FLOAT32:
            value = FLOAT32.round(value)
        # Целочисленный литерал в вещественном выражении сам по себе точен, а тип
        # ему даёт контекст; для нашей арифметики это просто число.
        return Typed(('num', value), fmt)

    def call(self):
        tok = self.take()
        name = tok.text
        self.take('(')
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
        if base in ('fabs', 'abs', 'fmin', 'fmax', 'floor', 'ceil', 'round', 'sin', 'cos',
                    'tan', 'atan', 'atan2', 'asin', 'acos', 'sinh', 'cosh', 'tanh',
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
        if tok.kind == 'name' and tok.text in FLOAT_TYPES:
            return self.declaration()
        if tok.kind == 'name' and tok.text in ('int', 'long', 'short', 'unsigned', 'char',
                                               'signed', 'const', 'static', 'register'):
            raise CParseError('declaration of {!r} inside the body is not supported (integer '
                              'and qualified declarations are outside the subset)'
                              .format(tok.text), tok.line)
        if tok.text == 'if':
            return self.if_statement()
        if tok.text == 'for':
            return self.for_statement()
        if tok.text == 'return':
            self.take('return')
            value = self.expr()
            self.take(';')
            return [('return', value)]
        if tok.text in ('while', 'do', 'switch', 'goto', 'break', 'continue'):
            raise CParseError('{!r} is not supported: this method proves bounds on '
                              'straight-line code, on conditionals and on loops with a known '
                              'trip count'.format(tok.text), tok.line)
        if tok.kind == 'name' and self.peek(1).text in ('=', '+=', '-=', '*=', '/='):
            return self.assignment()
        if tok.text == '{':
            return self.block()
        raise CParseError('statement starting at {!r} is not supported'.format(tok.text),
                          tok.line)

    def declaration(self):
        fmt = FLOAT_TYPES[self.take().text]
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

    def assignment(self):
        name_tok = self.take()
        name = name_tok.text
        if name not in self.vars:
            raise CParseError('assignment to unknown name {!r}'.format(name), name_tok.line)
        old, fmt = self.vars[name]
        op = self.take().text
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

    def if_statement(self):
        self.take('if')
        self.take('(')
        cond = self.condition()
        self.take(')')
        then_part = self.block()
        else_part = []
        if self.at('else'):
            self.take('else')
            else_part = self.block()
        # Каждая ветвь обязана закончиться возвратом. Иначе управление сходится
        # обратно, и после схождения переменная имеет разное значение в разных
        # ветках — это уже не путь, а слияние, и подстановкой оно не выражается.
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


_SIGNATURE = re.compile(
    r'\b(double|float)\s+([A-Za-z_][A-Za-z_0-9]*)\s*\(([^)]*)\)\s*\{')


def functions(src):
    """Имена вещественных функций файла — чтобы можно было выбрать нужную."""
    return [m.group(2) for m in _SIGNATURE.finditer(src)]


def parse_function(src, name=None):
    """Разобрать одну функцию файла в программу.

    Возвращает словарь: имя, формат результата, аргументы (имя -> формат),
    операторы в формате pareto/program.py и найденные в комментариях диапазоны.
    """
    matches = list(_SIGNATURE.finditer(src))
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

    ret_fmt = FLOAT_TYPES[chosen.group(1)]
    fname = chosen.group(2)
    params = chosen.group(3).strip()
    line0 = src.count('\n', 0, chosen.start()) + 1

    args = {}
    order = []
    if params and params != 'void':
        for part in params.split(','):
            bits = part.replace('*', ' * ').split()
            if '*' in bits or '[' in part or ']' in part:
                raise CParseError('function {} takes a pointer or an array. This mode reads '
                                  'scalar arguments only'.format(fname), line0)
            if len(bits) != 2 or bits[0] not in FLOAT_TYPES:
                raise CParseError('parameter {!r} of {} is not a plain double or float'
                                  .format(part.strip(), fname), line0)
            args[bits[1]] = FLOAT_TYPES[bits[0]]
            order.append(bits[1])

    body = _body_text(src, chosen.end() - 1)
    toks = tokenize(body)
    p = _Parser(toks)
    for a in order:
        p.vars[a] = (a, args[a])
    stmts = p.block()
    if p.peek().kind != 'eof':
        raise CParseError('unexpected {!r} after the end of the function body'
                          .format(p.peek().text), p.peek().line)

    stmts = _finish_returns(stmts, ret_fmt)
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
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return (brace_pos, i + 1)
        elif c == '"' or c == "'":
            raise CParseError('the function body contains a string or character literal; '
                              'that is outside the numeric subset')
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
