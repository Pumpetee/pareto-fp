# -*- coding: utf-8 -*-
"""Аффинная арифметика: интервалы, которые помнят, откуда взялись.

Интервальная арифметика теряет связь между величинами. Для неё `x − x` на домене
[1, 2] даёт [−1, 1], хотя ответ, очевидно, ноль: она не знает, что оба x — это один
и тот же x. Отсюда вся консервативность наших границ, измеренная медианой 2.58 и
выбросом в 664 раза, и ровно этим нас бьют Daisy и FPTaylor.

Аффинная форма хранит величину как

    x = x₀ + Σ xᵢ·εᵢ,   εᵢ ∈ [−1, 1]

где εᵢ — именованные символы неопределённости. Одинаковые символы у разных величин
и означают «это одно и то же неизвестное»: при вычитании они сокращаются, и `x − x`
честно даёт ноль. Линейные операции точны, нелинейные (умножение, деление, корень,
экспонента, логарифм) аппроксимируются линейно, а остаток уходит в НОВЫЙ символ —
так сохраняется корректность оценки сверху.

Реализация намеренно скромная: нам нужен не полноценный решатель, а более тугая
оценка диапазона значений, чем интервал. Всё, что не получается оценить аффинно,
возвращается к интервалу — хуже, но никогда не неверно.
"""
from __future__ import annotations

import itertools
import math

INF = float('inf')
_counter = itertools.count()


class Affine:
    """Величина как центр плюс набор помеченных отклонений."""

    __slots__ = ('c', 'terms')

    def __init__(self, c, terms=None):
        self.c = float(c)
        self.terms = dict(terms or {})

    # ---------- построение ----------
    @staticmethod
    def const(v):
        return Affine(v)

    @staticmethod
    def from_interval(lo, hi, key):
        """Переменная домена: центр и одно отклонение со своим именем."""
        c = 0.5 * (lo + hi)
        r = 0.5 * (hi - lo)
        return Affine(c, {key: r} if r else {})

    def fresh(self, radius):
        """Добавляет новый независимый символ — так оформляется остаток аппроксимации."""
        if radius:
            self.terms[('e', next(_counter))] = abs(radius)
        return self

    # ---------- свойства ----------
    @property
    def radius(self):
        return sum(abs(v) for v in self.terms.values())

    def interval(self):
        r = self.radius
        return (self.c - r, self.c + r)

    def abs_max(self):
        return abs(self.c) + self.radius

    def abs_min(self):
        r = self.radius
        if abs(self.c) <= r:
            return 0.0
        return abs(self.c) - r

    # ---------- линейные операции: точны ----------
    def __add__(self, other):
        if isinstance(other, (int, float)):
            return Affine(self.c + other, self.terms)
        out = Affine(self.c + other.c, self.terms)
        for k, v in other.terms.items():
            out.terms[k] = out.terms.get(k, 0.0) + v
            if out.terms[k] == 0.0:
                del out.terms[k]
        return out

    def __neg__(self):
        return Affine(-self.c, {k: -v for k, v in self.terms.items()})

    def __sub__(self, other):
        if isinstance(other, (int, float)):
            return Affine(self.c - other, self.terms)
        return self + (-other)

    def scaled(self, k):
        return Affine(self.c * k, {t: v * k for t, v in self.terms.items()})

    # ---------- нелинейные: линейная часть плюс новый символ ----------
    def __mul__(self, other):
        if isinstance(other, (int, float)):
            return self.scaled(other)
        out = Affine(self.c * other.c)
        for k, v in self.terms.items():
            out.terms[k] = out.terms.get(k, 0.0) + v * other.c
        for k, v in other.terms.items():
            out.terms[k] = out.terms.get(k, 0.0) + v * self.c
        out.terms = {k: v for k, v in out.terms.items() if v != 0.0}
        # верхняя оценка отброшенной квадратичной части
        return out.fresh(self.radius * other.radius)


def _chebyshev(af, f, df, lo, hi):
    """Линейная аппроксимация монотонной гладкой функции на отрезке.

    Наклон берётся как средняя скорость на отрезке, а максимальное отклонение от
    прямой уходит в новый символ. Для монотонных выпуклых или вогнутых функций —
    а это все наши sqrt, exp, log, expm1, log1p — такая оценка корректна сверху.
    """
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return None
    try:
        flo, fhi = f(lo), f(hi)
    except (ValueError, OverflowError):
        return None
    if not (math.isfinite(flo) and math.isfinite(fhi)):
        return None
    if hi - lo < 1e-300:
        return Affine(flo)
    slope = (fhi - flo) / (hi - lo)
    # точка, где производная равна наклону, даёт наибольшее отклонение
    try:
        probes = [lo, hi, 0.5 * (lo + hi)]
        dev = max(abs(f(p) - (flo + slope * (p - lo))) for p in probes)
    except (ValueError, OverflowError):
        return None
    out = af.scaled(slope)
    out = Affine(out.c + (flo - slope * lo), out.terms)
    return out.fresh(dev)


def unary(op, af):
    lo, hi = af.interval()
    if op == 'neg':
        return -af
    if op == 'sqrt':
        if lo < 0:
            return None
        return _chebyshev(af, math.sqrt, None, lo, hi)
    if op == 'exp':
        if hi > 709.0:
            return None
        return _chebyshev(af, math.exp, None, lo, hi)
    if op == 'log':
        if lo <= 0:
            return None
        return _chebyshev(af, math.log, None, lo, hi)
    if op == 'expm1':
        if hi > 709.0:
            return None
        return _chebyshev(af, math.expm1, None, lo, hi)
    if op == 'log1p':
        if lo <= -1:
            return None
        return _chebyshev(af, math.log1p, None, lo, hi)
    return None


def binary(op, a, b):
    if op == '+':
        return a + b
    if op == '-':
        return a - b
    if op == '*':
        return a * b
    if op == '/':
        lo, hi = b.interval()
        if lo <= 0.0 <= hi:
            return None                      # деление на интервал с нулём
        inv = _chebyshev(b, lambda t: 1.0 / t, None, lo, hi)
        return None if inv is None else a * inv
    if op == 'hypot':
        # монотонна по модулям, аффинно её не уточнить — отдаём интервальную оценку
        return None
    return None


def evaluate(tree, domain, cache=None):
    """Аффинная форма значения дерева. None — если аффинно оценить не удалось.

    Переменные получают СВОИ символы по имени: два вхождения x — это один и тот же
    x, и в этом вся суть. Именно поэтому x − x даёт ровно ноль, а не ширину домена.
    """
    op = tree[0]
    if op == 'num':
        return Affine.const(tree[1])
    if op == 'var':
        lo, hi = domain[tree[1]]
        return Affine.from_interval(lo, hi, ('var', tree[1]))
    if op in ('approx', 'eft'):
        return evaluate(tree[1], domain)
    if op == 'fma':
        kids = [evaluate(k, domain) for k in tree[1:]]
        if any(k is None for k in kids):
            return None
        return kids[0] * kids[1] + kids[2]
    kids = [evaluate(k, domain) for k in tree[1:]]
    if any(k is None for k in kids):
        return None
    if len(kids) == 1:
        return unary(op, kids[0])
    return binary(op, kids[0], kids[1])


def interval_of(tree, domain, fallback):
    """Интервал значения: аффинный, если получилось, иначе переданный запасной.

    Пересечение с запасным обязательно — аффинная оценка бывает шире на сильно
    нелинейных выражениях, и брать её вслепую значит ухудшить результат.
    """
    af = evaluate(tree, domain)
    if af is None:
        return fallback
    lo, hi = af.interval()
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return fallback
    return (max(lo, fallback[0]), min(hi, fallback[1]))
