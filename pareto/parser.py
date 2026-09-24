# -*- coding: utf-8 -*-
"""Разбор обычной записи формулы в дерево движка.

Нужен ровно для одного: чтобы человек мог дать свою формулу строкой, а не
вписывать кортежи в словарь внутри исходника. Без этого инструментом не
пользуется никто, кроме автора.

    parse('sqrt(x+1) - sqrt(x)')  ->  ('-', ('sqrt', ('+', ('var','x'), ('num',1.0))), ('sqrt', ('var','x')))

Поддержано: + - * / , унарный минус, скобки, степень с целым показателем
(x^3 и x**3 разворачиваются в умножения), функции sqrt, exp, log.
"""
from __future__ import annotations

FUNCS = ('sqrt', 'exp', 'log')


class ParseError(ValueError):
    pass


def tokenize(s):
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        if c.isdigit() or (c == '.' and i + 1 < n and s[i + 1].isdigit()):
            j = i
            while j < n and (s[j].isdigit() or s[j] == '.'):
                j += 1
            # экспоненциальная запись: 1e-9, 2.5E+3
            if j < n and s[j] in 'eE':
                k = j + 1
                if k < n and s[k] in '+-':
                    k += 1
                if k < n and s[k].isdigit():
                    j = k
                    while j < n and s[j].isdigit():
                        j += 1
            out.append(('num', s[i:j]))
            i = j
            continue
        if c.isalpha() or c == '_':
            j = i
            while j < n and (s[j].isalnum() or s[j] == '_'):
                j += 1
            out.append(('name', s[i:j]))
            i = j
            continue
        if s.startswith('**', i):
            out.append(('op', '^'))
            i += 2
            continue
        if c in '+-*/^()':
            out.append(('op', c))
            i += 1
            continue
        raise ParseError('unexpected character {!r} at position {}'.format(c, i))
    return out


class _P:
    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self, kind=None, val=None):
        k, v = self.peek()
        if k is None:
            raise ParseError('expression ends unexpectedly')
        if (kind and k != kind) or (val and v != val):
            raise ParseError('expected {}, got {!r}'.format(val or kind, v))
        self.i += 1
        return v

    def expr(self):
        node = self.term()
        while self.peek() == ('op', '+') or self.peek() == ('op', '-'):
            op = self.take('op')
            node = (op, node, self.term())
        return node

    def term(self):
        node = self.power()
        while self.peek() == ('op', '*') or self.peek() == ('op', '/'):
            op = self.take('op')
            node = (op, node, self.power())
        return node

    def power(self):
        base = self.unary()
        if self.peek() == ('op', '^'):
            self.take('op')
            k, v = self.peek()
            neg = False
            if (k, v) == ('op', '-'):
                self.take('op')
                neg = True
                k, v = self.peek()
            if k != 'num':
                raise ParseError('powers are supported with integer exponents only')
            self.take('num')
            e = float(v)
            if e != int(e) or neg or e < 0:
                raise ParseError('powers must be non-negative integers, got {}'.format(
                    ('-' if neg else '') + v))
            e = int(e)
            if e == 0:
                return ('num', 1.0)
            node = base
            for _ in range(e - 1):
                node = ('*', node, base)
            return node
        return base

    def unary(self):
        if self.peek() == ('op', '-'):
            self.take('op')
            return ('neg', self.unary())
        if self.peek() == ('op', '+'):
            self.take('op')
            return self.unary()
        return self.primary()

    def primary(self):
        k, v = self.peek()
        if k == 'num':
            self.take('num')
            return ('num', float(v))
        if k == 'name':
            self.take('name')
            if self.peek() == ('op', '('):
                if v not in FUNCS:
                    raise ParseError('function {} is not supported, available: {}'.format(v, ', '.join(FUNCS)))
                self.take('op', '(')
                arg = self.expr()
                self.take('op', ')')
                return (v, arg)
            return ('var', v)
        if (k, v) == ('op', '('):
            self.take('op', '(')
            node = self.expr()
            self.take('op', ')')
            return node
        raise ParseError('cannot parse the start of the expression: {!r}'.format(v))


def parse(text):
    p = _P(tokenize(text))
    node = p.expr()
    if p.i != len(p.t):
        raise ParseError('trailing input: {!r}'.format(p.t[p.i][1]))
    return node


def variables(tree):
    """Имена переменных в порядке появления — по ним спрашиваем диапазоны."""
    seen, out = set(), []

    def walk(n):
        if n[0] == 'var':
            if n[1] not in seen:
                seen.add(n[1])
                out.append(n[1])
            return
        if n[0] == 'num':
            return
        for k in n[1:]:
            walk(k)

    walk(tree)
    return out
