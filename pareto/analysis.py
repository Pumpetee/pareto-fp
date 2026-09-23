# -*- coding: utf-8 -*-
"""Две стоимости узла: машинная (скорость) и численная (граница ошибки).

Ключевое наблюдение, на котором всё держится: все варианты внутри одного
e-класса вычисляют МАТЕМАТИЧЕСКИ ОДНО И ТО ЖЕ значение. Значит их можно
сравнивать по АБСОЛЮТНОЙ границе ошибки округления напрямую, без
нормировки — нормировать не на что, истинное значение общее.
"""
from __future__ import annotations

import math
import os

U = 2.0 ** -53          # машинный эпсилон / 2 для binary64
INF = float('inf')

# веса под суперскалярный x86 и JIT V8: примерная латентность в тактах
COST = {
    '+': 1.0, '-': 1.0, '*': 1.0,
    '/': 6.0, 'neg': 0.5,
    'sqrt': 8.0, 'exp': 20.0, 'log': 20.0,
    # fma(a,b,c) = a*b + c с ОДНИМ округлением на всю операцию: промежуточное
    # произведение не округляется, поэтому по точности fma выгоден почти всегда.
    # А вот по скорости — нет. Аппаратной FMA нет в базовом x86-64, и без явного
    # разрешения (-mfma, -march=native, x86-64-v3) вызов уходит в libm: замер показал
    # 1.43 мс против 0.33 мс у обычной схемы Горнера, то есть вчетверо дороже.
    # Поэтому по умолчанию fma считается ДОРОЖЕ пары умножение-сложение, и выбирается
    # только когда реально окупается точностью. Машина с разрешённой FMA — ставь
    # PARETO_FMA_COST=1.0 и получай схемы Горнера на fma.
    'fma': float(os.environ.get('PARETO_FMA_COST', 2.4)),
}


# ---------- интервальная арифметика ----------
def iv_add(a, b): return (a[0] + b[0], a[1] + b[1])
def iv_sub(a, b): return (a[0] - b[0], a[1] - b[1])


def iv_mul(a, b):
    c = [a[0] * b[0], a[0] * b[1], a[1] * b[0], a[1] * b[1]]
    return (min(c), max(c))


def iv_div(a, b):
    if b[0] <= 0.0 <= b[1]:
        return (-INF, INF)
    c = [a[0] / b[0], a[0] / b[1], a[1] / b[0], a[1] / b[1]]
    return (min(c), max(c))


def iv_sqrt(a):
    if a[1] < 0:
        return (float('nan'), float('nan'))
    lo = math.sqrt(max(a[0], 0.0))
    return (lo, math.sqrt(max(a[1], 0.0)))


def iv_exp(a):
    return (math.exp(min(a[0], 700.0)), math.exp(min(a[1], 700.0)))


def iv_log(a):
    if a[0] <= 0:
        return (-INF, INF)
    return (math.log(a[0]), math.log(a[1]))


def iv_abs_max(a):
    return max(abs(a[0]), abs(a[1]))


def iv_abs_min(a):
    if a[0] <= 0.0 <= a[1]:
        return 0.0
    return min(abs(a[0]), abs(a[1]))


def eval_interval(op, kids):
    if op == '+': return iv_add(*kids)
    if op == '-': return iv_sub(*kids)
    if op == '*': return iv_mul(*kids)
    if op == '/': return iv_div(*kids)
    if op == 'neg': return (-kids[0][1], -kids[0][0])
    if op == 'sqrt': return iv_sqrt(kids[0])
    if op == 'exp': return iv_exp(kids[0])
    if op == 'log': return iv_log(kids[0])
    if op == 'fma': return iv_add(iv_mul(kids[0], kids[1]), kids[2])
    raise ValueError(op)


def propagate_error(op, kid_ivs, kid_errs, out_iv):
    """Верхняя граница АБСОЛЮТНОЙ ошибки результата.

    Модель стандартная: каждая операция в binary64 даёт относительную
    погрешность не больше U, плюс переносятся ошибки аргументов.
    """
    round_off = U * iv_abs_max(out_iv)
    if op in ('+', '-'):
        return kid_errs[0] + kid_errs[1] + round_off
    if op == 'neg':
        return kid_errs[0]
    if op == 'fma':
        # одно округление на всю операцию: произведение внутрь не округляется,
        # поэтому round_off добавляется ровно один раз, а не дважды
        a, b, _ = kid_ivs
        return (iv_abs_max(a) * kid_errs[1] + iv_abs_max(b) * kid_errs[0]
                + kid_errs[2] + round_off)
    if op == '*':
        a, b = kid_ivs
        return iv_abs_max(a) * kid_errs[1] + iv_abs_max(b) * kid_errs[0] + round_off
    if op == '/':
        a, b = kid_ivs
        bmin = iv_abs_min(b)
        if bmin == 0.0:
            return INF
        return (kid_errs[0] * iv_abs_max(b) + iv_abs_max(a) * kid_errs[1]) / (bmin * bmin) + round_off
    if op == 'sqrt':
        amin = iv_abs_min(kid_ivs[0])
        if amin == 0.0:
            return INF if kid_errs[0] > 0 else round_off
        return kid_errs[0] / (2.0 * math.sqrt(amin)) + round_off
    if op == 'exp':
        return iv_abs_max(out_iv) * kid_errs[0] + round_off
    if op == 'log':
        amin = iv_abs_min(kid_ivs[0])
        if amin == 0.0:
            return INF
        return kid_errs[0] / amin + round_off
    raise ValueError(op)


# ---------- интервалы e-классов ----------
def class_intervals(eg, domain, rounds=6):
    """Интервал значения для каждого e-класса.

    Варианты внутри класса эквивалентны, поэтому итоговый интервал —
    ПЕРЕСЕЧЕНИЕ интервалов всех вариантов: каждая форма даёт свою оценку
    сверху, а истина лежит во всех сразу.
    """
    iv = {}
    for _ in range(rounds):
        changed = False
        for eid in list(eg.classes):
            root = eg.uf.find(eid)
            best = iv.get(root)
            for node in eg.classes.get(root, ()):
                if node[0] == 'num':
                    cand = (node[1], node[1])
                elif node[0] == 'var':
                    cand = domain[node[1]]
                else:
                    kids = [iv.get(eg.uf.find(k)) for k in node[1:]]
                    if any(k is None for k in kids):
                        continue
                    try:
                        cand = eval_interval(node[0], kids)
                    except (ValueError, ZeroDivisionError, OverflowError):
                        continue
                if cand is None or any(math.isnan(x) for x in cand):
                    continue
                best = cand if best is None else (max(best[0], cand[0]), min(best[1], cand[1]))
            if best is not None and iv.get(root) != best:
                iv[root] = best
                changed = True
        if not changed:
            break
    return iv


# ---------- те же две метрики, но прямо по дереву (для baseline) ----------
def tree_cost(tree, domain):
    """(стоимость, граница абсолютной ошибки, интервал, работа, критический путь)."""
    op = tree[0]
    if op == 'num':
        v = float(tree[1])
        return 0.0, 0.0, (v, v), 0.0, 0.0
    if op == 'var':
        return 0.0, 0.0, domain[tree[1]], 0.0, 0.0
    kids = [tree_cost(k, domain) for k in tree[1:]]
    ivs = [k[2] for k in kids]
    out_iv = eval_interval(op, ivs)
    err = propagate_error(op, ivs, [k[1] for k in kids], out_iv)
    work = COST[op] + sum(k[3] for k in kids)
    lat = COST[op] + max(k[4] for k in kids)
    return work + lat, err, out_iv, work, lat


# ---------- извлечение фронта Парето ----------
def _dominated(cand, front):
    for p in front:
        c, e = p[0], p[1]
        if c <= cand[0] and e <= cand[1] and (c < cand[0] or e < cand[1]):
            return True
    return False


def _insert(front, cost, err, tree, keep, work=0.0, lat=0.0):
    if any(math.isnan(x) for x in (cost, err)):
        return front
    if _dominated((cost, err), front):
        return front
    front = [p for p in front if not (cost <= p[0] and err <= p[1])]
    front.append((cost, err, tree, work, lat))
    front.sort(key=lambda p: (p[0], p[1]))
    return front[:keep]


def pareto_extract(eg, root, domain, keep=8, rounds=10):
    """Для каждого класса — недоминируемые пары (стоимость, ошибка) с деревом."""
    iv = class_intervals(eg, domain)
    front = {}
    for _ in range(rounds):
        changed = False
        for eid in list(eg.classes):
            r = eg.uf.find(eid)
            cur = front.get(r, [])
            for node in eg.classes.get(r, ()):
                op = node[0]
                if op == 'num':
                    cand = [(0.0, 0.0, ('num', node[1]), 0.0, 0.0)]
                elif op == 'var':
                    cand = [(0.0, 0.0, ('var', node[1]), 0.0, 0.0)]
                else:
                    kid_fronts = [front.get(eg.uf.find(k)) for k in node[1:]]
                    if any(not f for f in kid_fronts):
                        continue
                    kid_ivs = [iv.get(eg.uf.find(k)) for k in node[1:]]
                    out_iv = iv.get(r)
                    if out_iv is None or any(x is None for x in kid_ivs):
                        continue
                    cand = []
                    from itertools import product as _p
                    for combo in _p(*kid_fronts):
                        # work — суммарная работа, lat — критический путь.
                        # Суперскалярный процессор выполняет независимые операции
                        # параллельно, поэтому одной суммы операций мало: схема
                        # Горнера экономит умножения, но удлиняет цепочку зависимостей.
                        work = COST[op] + sum(c[3] for c in combo)
                        lat = COST[op] + max(c[4] for c in combo)
                        cost = work + lat
                        try:
                            err = propagate_error(op, kid_ivs, [c[1] for c in combo], out_iv)
                        except (ValueError, ZeroDivisionError, OverflowError):
                            continue
                        cand.append((cost, err, (op,) + tuple(c[2] for c in combo), work, lat))
                for c, e, t, w, l in cand:
                    new = _insert(cur, c, e, t, keep, w, l)
                    if new is not cur:
                        cur = new
                        changed = True
            if cur:
                front[r] = cur
        if not changed:
            break
    return front.get(eg.uf.find(root), []), iv
