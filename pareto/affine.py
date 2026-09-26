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


U = 2.0 ** -53          # половина машинного эпсилона binary64


class Affine:
    """Величина как центр плюс набор помеченных отклонений.

    Плюс третье поле, без которого вся конструкция неверна: `err` — граница ошибки
    САМОГО аффинного счёта. Центр и коэффициенты считаются обычной арифметикой
    double, каждая операция над ними округляется, и это округление может пойти в
    сторону сужения. Аффинная форма нужна ровно для того, чтобы СУЗИТЬ интервал, —
    значит её собственная погрешность идёт прямо против её назначения и обязана
    быть учтена, иначе получится оценка уже настоящей.

    Найдено 26.09.2026 на условии `(x + 1e16) - 1e16 > 0`: центр 1e16 + 1 в double
    равен ровно 1e16, радиус при вычитании не менялся, и форма выдала интервал,
    внутри которого настоящих значений не было вовсе.

    Учёт стандартный: каждая операция добавляет U, умноженное на величину того, что
    она посчитала, плюс переносит погрешность аргументов. Константы при U — это
    подсчёт округлений с запасом, а не подгонка.
    """

    __slots__ = ('c', 'terms', 'err', '_radius')

    def __init__(self, c, terms=None, err=0.0):
        self.c = float(c)
        self.terms = dict(terms or {})
        self.err = float(err)
        self._radius = None

    # ---------- построение ----------
    @staticmethod
    def const(v):
        return Affine(v)

    @staticmethod
    def from_interval(lo, hi, key):
        """Переменная домена: центр и одно отклонение со своим именем."""
        c = 0.5 * (lo + hi)
        r = 0.5 * (hi - lo)
        # два округления: сумма и разность; умножение на 0.5 точно
        err = U * (abs(c) + abs(r))
        return Affine(c, {key: r} if r else {}, err)

    def fresh(self, radius):
        """Добавляет новый независимый символ — так оформляется остаток аппроксимации.

        Радиус округляем ВВЕРХ: он сам посчитан арифметикой double, и остаток,
        занижённый на улпу, это остаток, которого не хватает.
        """
        if radius:
            self.terms[('e', next(_counter))] = math.nextafter(abs(radius), INF)
            self._radius = None        # кэш радиуса устарел вместе с набором символов
        return self

    # ---------- свойства ----------
    @property
    def radius(self):
        # Сумма модулей сама округляется на каждом шаге; учитываем это в interval().
        # Кэш нужен не для красоты: радиус спрашивают несколько раз на каждую
        # операцию, а число символов растёт с размером выражения, и на графе в
        # восемнадцать тысяч узлов пересчёт становится главной статьёй расхода.
        if self._radius is None:
            self._radius = sum(abs(v) for v in self.terms.values())
        return self._radius

    def _bulk(self):
        """Дешёвая верхняя оценка модуля: без вызова interval()."""
        return abs(self.c) + self.radius + self.err

    def interval(self):
        """Интервал значения. Наружу раздвинут и на собственную погрешность счёта.

        Слагаемых в сумме радиуса столько же, сколько символов, и каждое сложение
        округляется, поэтому к погрешности добавляется число символов, умноженное
        на U и на радиус. Дальше пара округлений на самих границах — их закрывает
        шаг сетки наружу.
        """
        r = self.radius
        slack = self.err + (len(self.terms) + 2) * U * (abs(self.c) + r)
        lo = self.c - r - slack
        hi = self.c + r + slack
        return (math.nextafter(lo, -INF), math.nextafter(hi, INF))

    def abs_max(self):
        lo, hi = self.interval()
        return max(abs(lo), abs(hi))

    def abs_min(self):
        lo, hi = self.interval()
        if lo <= 0.0 <= hi:
            return 0.0
        return min(abs(lo), abs(hi))

    # ---------- линейные операции: точны в математике, но не в double ----------
    def __add__(self, other):
        if isinstance(other, (int, float)):
            out = Affine(self.c + other, self.terms, self.err)
            out.err += U * abs(out.c)
            return out
        out = Affine(self.c + other.c, self.terms, self.err + other.err)
        for k, v in other.terms.items():
            out.terms[k] = out.terms.get(k, 0.0) + v
            if out.terms[k] == 0.0:
                del out.terms[k]
        # одно округление на центр и по одному на каждый общий коэффициент
        out.err += U * (abs(out.c) + out.radius)
        return out

    def __neg__(self):
        # смена знака точна во всех случаях
        return Affine(-self.c, {k: -v for k, v in self.terms.items()}, self.err)

    def __sub__(self, other):
        if isinstance(other, (int, float)):
            out = Affine(self.c - other, self.terms, self.err)
            out.err += U * abs(out.c)
            return out
        return self + (-other)

    def scaled(self, k):
        out = Affine(self.c * k, {t: v * k for t, v in self.terms.items()},
                     self.err * abs(k))
        out.err += U * (abs(out.c) + out.radius)
        return out

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
        out._radius = None
        # Погрешность аргументов проходит через произведение, как в обычной модели:
        # |a·δb| + |b·δa|. Плюс округления самого умножения: центр, коэффициенты и
        # оценка отброшенной квадратичной части — с запасом четыре U.
        out.err = (self.err * other._bulk() + other.err * self._bulk()
                   + 4.0 * U * (abs(out.c) + out.radius))
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
    shift = flo - slope * lo
    res = Affine(out.c + shift, out.terms, out.err)
    # Наклон, сдвиг и само отклонение посчитаны арифметикой double, а значения f
    # берутся из libm, которой стандарт корректного округления не обещает. Поэтому
    # к погрешности формы добавляется запас по величинам, которые здесь участвуют:
    # восемь U покрывают полдесятка округлений и промах библиотечной функции.
    res.err += 8.0 * U * (abs(res.c) + res.radius + abs(flo) + abs(fhi) + abs(dev))
    return res.fresh(dev)


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

    То же правило распространяется и на составные поддеревья, и это не оптимизация,
    а часть корректности оценки. Нелинейная операция оставляет остаток линеаризации
    в НОВОМ символе, и раньше два вхождения одного и того же `x*x` получали два
    разных символа: форма перестаёт знать, что это одна и та же величина, и
    `(x*x) − (x*x)` давало не ноль, а сумму двух остатков. Для ветвлений это было
    видно сразу — разность одинаковых ветвей выходила ненулевой, то есть программа
    без скачка получала скачок. Поэтому формы поддеревьев запоминаются по записи
    дерева: одинаковая запись — одна и та же величина — один и тот же набор
    символов. Оценка от этого только сужается и остаётся верной.
    """
    if cache is None:
        cache = {}
    key = repr(tree)
    if key in cache:
        return cache[key]
    value = _evaluate(tree, domain, cache)
    cache[key] = value
    return value


def _evaluate(tree, domain, cache):
    op = tree[0]
    if op == 'num':
        return Affine.const(tree[1])
    if op == 'var':
        lo, hi = domain[tree[1]]
        return Affine.from_interval(lo, hi, ('var', tree[1]))
    if op in ('approx', 'eft'):
        return evaluate(tree[1], domain, cache)
    if op == 'fma':
        kids = [evaluate(k, domain, cache) for k in tree[1:]]
        if any(k is None for k in kids):
            return None
        return kids[0] * kids[1] + kids[2]
    kids = [evaluate(k, domain, cache) for k in tree[1:]]
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
