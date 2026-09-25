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

def _with_center(iv):
    """Интервал, расширенный до точки разложения (нуля).

    Остаток по Лагранжу берётся в точке ξ МЕЖДУ центром разложения и аргументом,
    поэтому максимум производной надо искать на отрезке, включающем ноль. Фаззинг
    25.09.2026 поймал обратное: для exp на домене [−0.53, −0.41] максимум брался
    как exp(−0.41)=0.66 вместо exp(0)=1, остаток выходил 1.6e-2 при реальной
    ошибке 2.2e-2 — граница оказывалась ниже факта.
    """
    return (min(0.0, iv[0]), max(0.0, iv[1]))


def _exp_family(kind):
    def coeffs(n):
        c = [1.0 / math.factorial(k) for k in range(n + 1)]
        if kind == 'expm1':
            c[0] = 0.0
        return c

    def deriv_max(iv, n):
        # производная exp любого порядка это exp; максимум ищем на отрезке,
        # включающем точку разложения
        lo, hi = _with_center(iv)
        return math.exp(min(hi, 700.0))

    return coeffs, deriv_max


def _log1p_family():
    def coeffs(n):
        # log(1+x) = x - x^2/2 + x^3/3 - ...
        return [0.0] + [((-1.0) ** (k + 1)) / k for k in range(1, n + 1)]

    def deriv_max(iv, n):
        # |d^(n+1) log(1+x)| = n! / |1+x|^(n+1), худшее при наименьшем |1+x|;
        # отрезок расширяем до точки разложения
        a, b = _with_center(iv)
        lo = min(abs(1.0 + a), abs(1.0 + b))
        if a <= -1.0 <= b or lo == 0.0:
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
        a, b = _with_center(iv)
        lo = min(abs(1.0 + a), abs(1.0 + b))
        if a <= -1.0 <= b or lo == 0.0:
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


def _sqrt_shift_candidates(node, domain, orders):
    """sqrt(u*u - v) при малом v/u² — разложение по малому параметру.

    Классическая катастрофа численного анализа: в формуле корней квадратного
    уравнения при b² >> 4ac разность -b + sqrt(b²-4ac) съедает все значащие цифры.
    Алгебра тут бессильна, нужен ряд: sqrt(u²−v) = u·sqrt(1−t) при t = v/u², а
    sqrt(1−t) раскладывается вокруг нуля. Остаток по Лагранжу умножается на |u| и
    уезжает наружу в узле approx — дальше его протащит обычный анализ.
    """
    if node[0] != 'sqrt':
        return []
    inner = node[1]
    if inner[0] not in ('-', '+'):
        return []
    sq, rest = inner[1], inner[2]
    if not (sq[0] == '*' and sq[1] == sq[2]):
        return []
    u = sq[1]
    sign = -1.0 if inner[0] == '-' else 1.0

    try:
        u_iv = tree_cost(u, domain)[2]
        v_iv = tree_cost(rest, domain)[2]
    except Exception:
        return []
    umin = min(abs(u_iv[0]), abs(u_iv[1]))
    if umin == 0.0 or u_iv[0] * u_iv[1] < 0:       # u меняет знак — |u| не выразить
        return []
    vmax = iv_abs_max(v_iv)
    t_max = vmax / (umin * umin)
    if not math.isfinite(t_max) or t_max > 0.25:   # ряд имеет смысл только при малом t
        return []

    coeffs_fn, deriv_fn = _sqrt1p_family()
    t_tree = ('/', rest, ('*', u, u))
    if sign < 0:
        t_tree = ('neg', t_tree)
    t_iv = (-t_max, t_max)

    out = []
    for n in orders:
        cs = _strip_zero_tail(coeffs_fn(n))
        dmax = deriv_fn(t_iv, n)
        if not math.isfinite(dmax):
            continue
        remainder = dmax * t_max ** (n + 1) / math.factorial(n + 1) * iv_abs_max(u_iv)
        for build in (_horner, _horner_fma):
            poly = build(cs, t_tree)
            out.append(('approx', ('*', u, poly), remainder))
    return out


def _split_sqrt_minus(node):
    """Разбирает узел вида sqrt(u*u ± v) − u (в любом порядке записи).

    Возвращает (u, v, sign) или None. sign = −1 для sqrt(u²−v), +1 для sqrt(u²+v).
    """
    if node[0] not in ('-', '+'):
        return None
    a, b = node[1], node[2]
    # приводим к виду «корень и вычитаемое u»
    if node[0] == '-':
        root, sub = a, b
    elif b[0] == 'neg':
        root, sub = a, b[1]
    elif a[0] == 'neg':
        root, sub = b, a[1]
    else:
        return None
    if root[0] != 'sqrt':
        return None
    inner = root[1]
    if inner[0] not in ('-', '+'):
        return None
    sq, rest = inner[1], inner[2]
    if not (sq[0] == '*' and sq[1] == sq[2] and sq[1] == sub):
        return None
    return sub, rest, (-1.0 if inner[0] == '-' else 1.0)


def _sqrt_minus_candidates(node, domain, orders):
    """sqrt(u*u − v) − u при малом v/u² — главный кейс катастрофического сокращения.

    Подставить ряд вместо корня мало: разность двух почти равных величин остаётся на
    месте, и все значащие цифры по-прежнему гибнут. Поэтому сокращение делается сразу
    на уровне паттерна:

        sqrt(u² − v) − u = u·(sqrt(1−t) − 1) = −(v/u)·(1/2 + t/8 + t²/16 + …),  t = v/u²

    Вычитания больше нет — есть произведение малой величины на ряд, и ошибка падает
    с сорока семи бит до долей бита. Остаток Лагранжа умножается на |u| и уезжает в
    узел approx, откуда его протащит обычный анализ.
    """
    parsed = _split_sqrt_minus(node)
    if parsed is None:
        return []
    u, v, sign = parsed

    try:
        u_iv = tree_cost(u, domain)[2]
        v_iv = tree_cost(v, domain)[2]
    except Exception:
        return []
    umin = min(abs(u_iv[0]), abs(u_iv[1]))
    if umin == 0.0 or u_iv[0] * u_iv[1] < 0:
        return []
    t_max = iv_abs_max(v_iv) / (umin * umin)
    if not math.isfinite(t_max) or t_max > 0.25:
        return []

    coeffs_fn, deriv_fn = _sqrt1p_family()
    t_tree = ('/', v, ('*', u, u))
    if sign < 0:
        t_tree = ('neg', t_tree)
    t_iv = (-t_max, t_max)

    out = []
    for n in orders:
        cs = coeffs_fn(n)
        # (sqrt(1+t) − 1)/t = c1 + c2·t + c3·t² + …  — ряд без свободного члена
        tail = _strip_zero_tail(cs[1:])
        if not tail:
            continue
        dmax = deriv_fn(t_iv, n)
        if not math.isfinite(dmax):
            continue
        remainder = dmax * t_max ** (n + 1) / math.factorial(n + 1) * iv_abs_max(u_iv)
        # u·t сокращается до ±v/u сразу здесь: оставлять его в виде u·(v/u²) значит
        # тащить в код лишнее умножение и лишнее деление, за которые никто не платит
        head = ('/', v, u)
        if sign < 0:
            head = ('neg', head)
        for build in (_horner, _horner_fma):
            poly = build(tail, t_tree)
            out.append(('approx', ('*', head, poly), remainder))
    return out


def _has_sqrt(node):
    """Быстрая проверка: есть ли в дереве корень вообще.

    Без неё разложение подвыражений запускалось на каждом выражении подряд, включая
    те, где раскладывать нечего, и тянуло за собой локальную саторацию на каждого
    кандидата. Прогон отчёта по плотности границ вырос с минуты до четверти часа —
    ровно на этой пустой работе.
    """
    if node[0] == 'sqrt':
        return True
    if node[0] in ('num', 'var', 'approx'):
        return False
    return any(_has_sqrt(k) for k in node[1:])


def rewrite_candidates(tree, domain, orders=range(2, 7)):
    """Разложения ПОДвыражений: обходим дерево и подменяем по одному узлу за раз."""
    if not _has_sqrt(tree):
        return []
    out = []

    def walk(node, rebuild):
        for cand in _sqrt_shift_candidates(node, domain, orders):
            out.append(rebuild(cand))
        for cand in _sqrt_minus_candidates(node, domain, orders):
            out.append(rebuild(cand))
        if node[0] in ('num', 'var', 'approx'):
            return
        for i in range(1, len(node)):
            def rb(new, node=node, i=i, rebuild=rebuild):
                kids = list(node)
                kids[i] = new
                return rebuild(tuple(kids))
            walk(node[i], rb)

    walk(tree, lambda x: x)

    # Подстановка ряда сама по себе катастрофу не лечит: в формуле корней остаётся
    # b*(1 - t/2 - …) - b, то есть то же вычитание близких величин. Лечит алгебра
    # ПОСЛЕ подстановки, поэтому каждый кандидат прогоняется через свой e-граф, где
    # approx — непрозрачный лист. Там b сокращается, и остаётся -b*t/2*(1 + t/4).
    from pareto.egraph import EGraph
    from pareto.rules import CANCEL_RULES, RULES

    scored = []
    seen = set()
    for t in out:
        variants = [t]
        try:
            eg = EGraph()
            root = eg.add_expr(t)
            # сокращение общего множителя нужно именно здесь: после подстановки
            # ряда в числителе и знаменателе остаётся один и тот же множитель
            eg.saturate(RULES + CANCEL_RULES, iters=4, node_limit=8000, domain=domain)
            sub, _ = _extract_simple(eg, root, domain)
            variants.extend(sub)
        except Exception:
            pass
        for v in variants:
            key = repr(v)
            if key in seen:
                continue
            seen.add(key)
            try:
                cost, err, _, work, lat = tree_cost(v, domain)
            except (ValueError, ZeroDivisionError, OverflowError, KeyError):
                continue
            if math.isfinite(err):
                scored.append((cost, err, v, work, lat))
    return scored


def _extract_simple(eg, root, domain, keep=6):
    """Фронт для локального e-графа, без повторного захода в генерацию кандидатов.

    Отдельная функция нужна, чтобы не уйти в рекурсию: pareto_extract сам зовёт
    разложение рядами, и вызов его отсюда закрутил бы бесконечный цикл.
    """
    from pareto.analysis import pareto_extract
    front, iv = pareto_extract(eg, root, domain, keep=keep, series=False)
    return [p[2] for p in front], iv


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
    radius = iv_abs_max(_with_center(iv))
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
