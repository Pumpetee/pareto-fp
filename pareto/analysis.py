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
    # Библиотечные функции, которыми Herbie закрывает малые аргументы и переполнение.
    # Стоят примерно как их обычные собратья: та же реализация плюс поправочный шаг.
    'expm1': 22.0, 'log1p': 22.0, 'hypot': 12.0,
}


# ---------- интервальная арифметика ----------
def iv_add(a, b): return (a[0] + b[0], a[1] + b[1])


def iv_sub(a, b):
    """[a0, a1] − [b0, b1] = [a0 − b1, a1 − b0].

    ⛔ Здесь была ошибка, обесценивавшая главное заявление проекта. Написано было
    покоординатно, (a0 − b0, a1 − b1), и на [1,2] − [1,2] это давало (0, 0) вместо
    (−1, 1). Интервал схлопывался в точку, ошибка округления считается как
    U·max|интервал| — и «доказанная граница» выходила нулевой там, где реальная
    ошибка достигала 6.7e-16. Ровно на примере из README: x*x − y*y при x,y ∈ [1,2].
    Тесты этого не ловили: sq_diff сидит на узком домене около 1000, где сломанный
    интервал всё равно оставался ненулевым и случайно накрывал замер.
    """
    return (a[0] - b[1], a[1] - b[0])


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


def iv_expm1(a):
    return (math.expm1(min(a[0], 700.0)), math.expm1(min(a[1], 700.0)))


def iv_log1p(a):
    if a[0] <= -1.0:
        return (-INF, INF)
    return (math.log1p(a[0]), math.log1p(a[1]))


def iv_hypot(a, b):
    """Монотонна по |x| и |y|, поэтому границы берутся по модулям."""
    lo = math.hypot(iv_abs_min(a), iv_abs_min(b))
    hi = math.hypot(iv_abs_max(a), iv_abs_max(b))
    return (lo, hi)


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
    if op == 'expm1': return iv_expm1(kids[0])
    if op == 'log1p': return iv_log1p(kids[0])
    if op == 'hypot': return iv_hypot(kids[0], kids[1])
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
    if op == 'expm1':
        # производная expm1 это exp(x); на домене её максимум равен 1 + max|expm1|
        a = kid_ivs[0]
        slope = math.exp(min(iv_abs_max(a), 700.0))
        return slope * kid_errs[0] + round_off
    if op == 'log1p':
        # производная 1/(1+x); знаменатель берём по наименьшему |1+x| на домене
        a = kid_ivs[0]
        dmin = iv_abs_min((1.0 + a[0], 1.0 + a[1]))
        if dmin == 0.0:
            return INF
        return kid_errs[0] / dmin + round_off
    if op == 'hypot':
        # |d hypot / dx| <= 1 и |d hypot / dy| <= 1, поэтому ошибки просто складываются.
        # Главное свойство: промежуточных квадратов нет, значит нет и переполнения,
        # из-за которого sqrt(x*x + y*y) даёт бесконечность задолго до предела результата.
        return kid_errs[0] + kid_errs[1] + round_off
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
                elif node[0] == 'approx':
                    # лист-приближение: интервал берём у его внутреннего дерева,
                    # расширив на остаток метода в обе стороны
                    try:
                        _, _, inner_iv, _, _ = tree_cost(node[1], domain)
                    except Exception:
                        continue
                    rem = float(node[2])
                    cand = (inner_iv[0] - rem, inner_iv[1] + rem)
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
    if op == 'approx':
        # ('approx', дерево, остаток) — приближение с собственной погрешностью метода.
        # Нужен, чтобы разложить ПОДвыражение в ряд и честно протащить остаток наружу
        # через всё оставшееся дерево: дальше он распространяется обычными правилами,
        # как любая другая входная ошибка.
        cost, err, iv, work, lat = tree_cost(tree[1], domain)
        return cost, err + float(tree[2]), iv, work, lat
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


def pareto_extract(eg, root, domain, keep=8, rounds=10, series=True):
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

    # Пересчёт границ по извлечённым деревьям.
    #
    # Снизу вверх ошибка копится по e-КЛАССАМ, а интервал класса общий для всех
    # эквивалентных форм внутри него — то есть для конкретного дерева он может быть
    # и шире, и уже нужного. Расхождение измерено 25.09.2026: на (a+b)^2-(a-b)^2
    # фронт приписывал форме 4ab границу 1.2e+14 при честных 1.3e-14, а на формуле
    # корней квадратного уравнения, наоборот, занижал в 16 раз. Ни разу не ниже
    # реальной ошибки, но доверять несогласованным числам нельзя, и выбор формы по
    # ним бессмысленен. Дерево уже собрано, поэтому просто считаем по нему честно.
    out = []
    for cost, err, tree, work, lat in front.get(eg.uf.find(root), []):
        try:
            c2, e2, _, w2, l2 = tree_cost(tree, domain)
        except (ValueError, ZeroDivisionError, OverflowError):
            out.append((cost, err, tree, work, lat))
            continue
        out.append((c2, e2, tree, w2, l2))

    # Приближения рядом Тейлора добавляются кандидатами, а не сливаются с e-графом:
    # e-граф хранит тождества, а ряд тождеством не является. Их граница уже включает
    # остаточный член, поэтому сравнивать их с точными формами можно напрямую.
    if out and series:
        from pareto.taylor import series_candidates
        # пробуем КАЖДУЮ форму фронта: разложение умеет раскрывать верхний узел, а
        # нужная запись может лежать любой точкой — expm1(x) и (exp(x) + -1) это
        # один класс, но разложить можно только первую
        from pareto.taylor import rewrite_candidates
        extra = []
        for _, _, seed, _, _ in out:
            extra.extend(series_candidates(seed, domain))
        # Разложение подвыражений заметно дороже: внутри своя саторация на каждого
        # кандидата. Поэтому берём не весь фронт, а три опорные формы — самую дешёвую,
        # самую точную и среднюю. Разложение всё равно смотрит на структуру, а она у
        # эквивалентных форм повторяется.
        ordered = sorted(out, key=lambda p: (p[0], p[1]))
        seeds = {id(ordered[0]): ordered[0], id(ordered[-1]): ordered[-1],
                 id(ordered[len(ordered) // 2]): ordered[len(ordered) // 2]}
        for _, _, seed, _, _ in seeds.values():
            extra.extend(rewrite_candidates(seed, domain))
        out.extend(extra)

    # после пересчёта часть точек может оказаться доминируемой — фронт пересобираем
    final = []
    for cost, err, tree, work, lat in sorted(out, key=lambda p: (p[0], p[1])):
        if any(o[0] <= cost and o[1] <= err and (o[0] < cost or o[1] < err) for o in final):
            continue
        final = [o for o in final if not (cost <= o[0] and err <= o[1])]
        final.append((cost, err, tree, work, lat))
    return final, iv
