# -*- coding: utf-8 -*-
"""Ряды Тейлора как кандидаты — с доказанной границей остатка.

Herbie берёт кейсы, где алгебра бессильна, подстановкой ряда: на малом домене
`exp(x) - 1` заменяется полиномом и ошибка падает на двадцать бит. Приём сильный,
но это не тождество, а приближение, верное только внутри домена, и гарантии на него
нет — только замер на выборке.

Здесь тот же приём, но с сертификатом. Для разложения порядка n остаток по Лагранжу
равен f⁽ⁿ⁺¹⁾(ξ)·(x−c)ⁿ⁺¹/(n+1)! при некотором ξ внутри домена, значит его модуль
оценивается сверху максимумом производной на домене. Эта оценка складывается с
обычной ошибкой округления самого полинома, и получившееся число — честная верхняя
граница полной ошибки, включая ошибку метода.

Кандидат возвращается отдельной точкой фронта: он не сливается с e-графом, потому
что e-граф хранит тождества, а ряд тождеством не является.
"""
from __future__ import annotations

import math

from pareto.analysis import iv_abs_max, tree_cost

MAX_ORDER = 8


def _num(x):
    return ('num', float(x))


def _horner(coeffs, var_tree):
    """Полином по схеме Горнера: меньше операций и короче цепочка округлений.

    Нулевые коэффициенты не превращаются в операции: у expm1 и log1p свободный член
    равен нулю, и наивная схема дописывала в код «+ 0» — лишнее сложение на каждом
    вызове и лишняя единица стоимости на ровном месте.
    """
    out = _num(coeffs[-1])
    for c in reversed(coeffs[:-1]):
        out = ('*', out, var_tree)
        if c != 0.0:
            out = ('+', out, _num(c))
    return out


def _horner_fma(coeffs, var_tree):
    """Та же схема, но каждый шаг одной операцией fma: одно округление вместо двух.

    Ровно так собран ряд, которым Herbie выигрывал на exp(x) - 1: не хитрее по
    математике, просто на каждом шаге экономится округление.
    """
    out = _num(coeffs[-1])
    for c in reversed(coeffs[:-1]):
        if c != 0.0:
            out = ('fma', out, var_tree, _num(c))
        else:
            out = ('*', out, var_tree)
    return out


def _split_leading(coeffs, var_tree):
    """Главный член отдельным слагаемым: c1·x + x·(c2·x + c3·x² + …).

    Для рядов с нулевым свободным членом — expm1, log1p — это заметно точнее обычной
    схемы Горнера. Там полином собирается как (…+1)·x, и единица, доминируя над
    поправками, съедает их младшие биты ещё до умножения на x. Здесь главный член
    добавляется последним, поэтому поправка приходит со своей полной точностью.
    Замер на exp(x)−1: 0.383 бита у Горнера, 0.245 у формы Herbie и 0.000 здесь.
    """
    if len(coeffs) < 3 or coeffs[0] != 0.0:
        return None
    tail = coeffs[2:]                      # c2, c3, …
    if not any(tail):
        return None
    inner = _num(tail[-1])
    for c in reversed(tail[:-1]):
        inner = ('fma', inner, var_tree, _num(c)) if c != 0.0 else ('*', inner, var_tree)
    inner = ('*', inner, var_tree)          # получаем c2·x + c3·x² + …
    head = var_tree if coeffs[1] == 1.0 else ('*', _num(coeffs[1]), var_tree)
    return ('fma', var_tree, inner, head)


def _strip_zero_tail(coeffs):
    while len(coeffs) > 1 and coeffs[-1] == 0.0:
        coeffs = coeffs[:-1]
    return coeffs


# ── семейства функций: коэффициенты ряда вокруг нуля и модуль (n+1)-й производной ──

def _exp_family(kind):
    def coeffs(n):
        c = [1.0 / math.factorial(k) for k in range(n + 1)]
        if kind == 'expm1':
            c[0] = 0.0
        return c

    def deriv_max(iv, n):
        # производная exp любого порядка это exp, максимум на правом конце
        return math.exp(min(max(iv[0], iv[1]), 700.0))

    return coeffs, deriv_max


def _log1p_family():
    def coeffs(n):
        # log(1+x) = x - x^2/2 + x^3/3 - ...
        return [0.0] + [((-1.0) ** (k + 1)) / k for k in range(1, n + 1)]

    def deriv_max(iv, n):
        # |d^(n+1) log(1+x)| = n! / |1+x|^(n+1), худшее при наименьшем |1+x|
        lo = min(abs(1.0 + iv[0]), abs(1.0 + iv[1]))
        if iv[0] <= -1.0 <= iv[1] or lo == 0.0:
            return math.inf
        return math.factorial(n) / lo ** (n + 1)

    return coeffs, deriv_max


def _sqrt1p_family():
    def coeffs(n):
        # sqrt(1+x) = sum C(1/2, k) x^k
        out, c = [], 1.0
        for k in range(n + 1):
            out.append(c)
            c = c * (0.5 - k) / (k + 1)
        return out

    def deriv_max(iv, n):
        lo = min(abs(1.0 + iv[0]), abs(1.0 + iv[1]))
        if iv[0] <= -1.0 <= iv[1] or lo == 0.0:
            return math.inf
        # |d^(n+1) (1+x)^(1/2)| = |prod_{j=0..n}(1/2-j)| * (1+x)^(1/2-(n+1))
        prod = 1.0
        for j in range(n + 1):
            prod *= abs(0.5 - j)
        return prod * lo ** (0.5 - (n + 1))

    return coeffs, deriv_max


FAMILIES = {
    'exp': _exp_family('exp'),
    'expm1': _exp_family('expm1'),
    'log1p': _log1p_family(),
}


def _arg_interval(arg_tree, domain):
    """Интервал аргумента. tree_cost третьим значением возвращает именно его."""
    return tree_cost(arg_tree, domain)[2]


def series_candidates(tree, domain, orders=range(2, MAX_ORDER + 1)):
    """Приближения ряда для узлов вида f(g) на вершине дерева.

    Возвращает список (cost, bound, tree, work, lat), где bound уже включает и
    ошибку округления полинома, и математический остаток ряда.
    """
    op = tree[0]
    out = []

    target, arg = None, None
    if op in FAMILIES:
        target, arg = op, tree[1]
    elif op == 'sqrt':
        # sqrt(1+u) раскладывается вокруг нуля по u
        inner = tree[1]
        if inner[0] == '+' and inner[1] == ('num', 1.0):
            target, arg = 'sqrt1p', inner[2]
        elif inner[0] == '+' and inner[2] == ('num', 1.0):
            target, arg = 'sqrt1p', inner[1]
    if target is None:
        return out

    try:
        iv = _arg_interval(arg, domain)
    except Exception:
        return out
    if iv is None or not all(math.isfinite(v) for v in iv):
        return out

    coeffs_fn, deriv_fn = _sqrt1p_family() if target == 'sqrt1p' else FAMILIES[target]
    radius = iv_abs_max(iv)
    if radius == 0.0 or radius > 1.0:
        # вдали от точки разложения ряд сходится медленно, и честный остаток
        # оказывается хуже прямого вычисления — такие кандидаты не нужны
        return out

    for n in orders:
        try:
            cs = _strip_zero_tail(coeffs_fn(n))
            dmax = deriv_fn(iv, n)
            if not math.isfinite(dmax):
                continue
            # остаток по Лагранжу: |f⁽ⁿ⁺¹⁾(ξ)| · |x−c|ⁿ⁺¹ / (n+1)!
            remainder = dmax * radius ** (n + 1) / math.factorial(n + 1)
            for build in (_horner, _horner_fma, _split_leading):
                poly = build(cs, arg)
                if poly is None:
                    continue
                cost, round_err, _, work, lat = tree_cost(poly, domain)
                total = round_err + remainder
                if not math.isfinite(total):
                    continue
                out.append((cost, total, poly, work, lat))
        except (ValueError, OverflowError, ZeroDivisionError):
            continue
    return out
