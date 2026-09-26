# -*- coding: utf-8 -*-
"""Смешанная точность: где в программе стоят округления и можно ли их сузить.

Главная мысль, без которой дальше ничего не сходится: **узлы округления — это не
часть математики, а часть реализации.** У выражения есть идеальное вещественное
значение, и оно не зависит от того, в каком формате мы считали промежуточные
величины. Граница ошибки в этом проекте всегда означает одно — расстояние от
напечатанной программы до этого идеального значения.

Отсюда всё остальное:

* в e-граф подаётся выражение БЕЗ округлений. Тогда все алгебраические правила
  работают как раньше — они и записаны для математики, а не для реализации;
* формат РЕЗУЛЬТАТА — это контракт, а не выбор. Функция объявлена возвращающей
  `float`, значит наружное округление обязано остаться в любом ответе, иначе мы
  предложим человеку программу, которая не компилируется в его же сигнатуру;
* а вот ВНУТРЕННИЕ округления — это уже выбор, и обычно их лучше убрать. Ровно
  это и есть самый частый рецепт в настоящем численном коде: считай в double,
  округляй один раз на выходе. Наш инструмент теперь может не просто посоветовать
  такое, а показать доказанную границу до и после.

Чего мы здесь НЕ утверждаем. Модель стоимости не обещает ускорения от узкой
точности: на скалярном x86 mulss и mulsd имеют одинаковую латентность, а выигрыш
float живёт в пропускной способности памяти и в ширине вектора, которых модель не
считает. Поэтому вопрос «а можно ли тут обойтись float» решается не фронтом
Парето, а прямым подбором: какая самая узкая расстановка форматов ещё
удерживает заданную границу ошибки.
"""
from __future__ import annotations

import math

from pareto.precision import ROUND_OPS, format_by_name, round_op_for

LEAF_OPS = ('num', 'var', 'approx', 'eft')


def strip_rounding(tree):
    """Убрать ВСЕ узлы округления: остаётся идеальное математическое выражение."""
    if not isinstance(tree, tuple):
        return tree
    op = tree[0]
    if op in ROUND_OPS:
        return strip_rounding(tree[1])
    if op in LEAF_OPS:
        return tree
    return (op,) + tuple(strip_rounding(k) for k in tree[1:])


def output_round_op(tree):
    """Формат результата, если он задан наружным округлением. Иначе None (binary64)."""
    return tree[0] if isinstance(tree, tuple) and tree[0] in ROUND_OPS else None


def wrap(tree, round_op):
    """Надеть округление результата. Двойное округление не плодим."""
    if round_op is None:
        return tree
    if isinstance(tree, tuple) and tree[0] == round_op:
        return tree
    return (round_op, tree)


def count_rounding(tree):
    """Сколько узлов округления в дереве — мера «насколько программа узкая»."""
    if not isinstance(tree, tuple) or tree[0] in LEAF_OPS:
        return 0
    n = 1 if tree[0] in ROUND_OPS else 0
    return n + sum(count_rounding(k) for k in tree[1:])


def inner_rounding_free(tree):
    """Дерево с наружным округлением на месте и без единого внутреннего.

    Это и есть рецепт «считай широко, округли один раз»: самая точная из всех
    расстановок форматов при заданном формате результата.
    """
    top = output_round_op(tree)
    return wrap(strip_rounding(tree), top)


# ---------- подбор точности ----------
def _sites(tree):
    """Места, куда можно вставить округление: все внутренние узлы, снизу вверх.

    Листья пропускаем: округлять переменную бессмысленно — она и так лежит в
    памяти в своём формате, а объявление типа мы не меняем.
    """
    out = []

    def walk(path, node):
        if not isinstance(node, tuple) or node[0] in LEAF_OPS:
            return
        for i, k in enumerate(node[1:], start=1):
            walk(path + (i,), k)
        out.append(path)

    walk((), tree)
    return out


def _at(tree, path):
    node = tree
    for i in path:
        node = node[i]
    return node


def _replace(tree, path, new):
    if not path:
        return new
    i = path[0]
    kids = list(tree)
    kids[i] = _replace(tree[i], path[1:], new)
    return tuple(kids)


def tune(tree, domain, target, formats=('float32',), bound_fn=None, out_round=None):
    """Самая узкая расстановка форматов, при которой граница не превышает target.

    Алгоритм тот же, что у Precimonious и FPTuner: жадно опускаем точность
    подвыражений, пока проверка проходит. Разница принципиальная — у них проверка
    это прогон на выборке входов, то есть надежда, а у нас ДОКАЗАННАЯ граница на
    всём домене. Поэтому здесь не бывает ответа «на моих тестах хватило».

    Порядок обхода — снизу вверх: узкий формат внизу дерева сбивает точность
    сильнее всего, зато и памяти экономит больше. Каждое сужение проверяется
    отдельно и откатывается, если граница вылезла за target.

    Возвращает (дерево, граница, сколько узлов удалось сузить).
    """
    if bound_fn is None:
        from pareto.analysis import combined_bound
        bound_fn = combined_bound

    ops = []
    for name in formats:
        op = round_op_for(format_by_name(name))
        if op is None:
            continue                     # binary64 своего узла не имеет
        ops.append(op)
    if not ops:
        return tree, bound_fn(tree, domain), 0

    best = wrap(strip_rounding(tree), out_round if out_round is not None
                else output_round_op(tree))
    try:
        best_err = bound_fn(best, domain)
    except (ValueError, ZeroDivisionError, OverflowError, KeyError):
        return tree, float('inf'), 0
    if not (math.isfinite(best_err) and best_err <= target):
        return best, best_err, 0

    narrowed = 0
    # Пути считаются один раз, и это корректно именно из-за порядка обхода. Обёртка
    # на узле p удлиняет пути только внутрь p. Обход снизу вверх: когда до p дошла
    # очередь, все его потомки уже обработаны, а у предков, соседей и других ветвей
    # путь не менялся вовсе. Значит устаревших путей в списке не остаётся.
    for path in _sites(best):
        node = _at(best, path)
        if node[0] in ROUND_OPS:
            continue
        for op in ops:
            cand = _replace(best, path, (op, node))
            try:
                err = bound_fn(cand, domain)
            except (ValueError, ZeroDivisionError, OverflowError, KeyError):
                continue
            if math.isfinite(err) and err <= target:
                best, best_err, narrowed = cand, err, narrowed + 1
                break
    return best, best_err, narrowed


def uniform_candidates(tree, domain, formats=('float32', 'float16'), bound_fn=None):
    """Границы для однородных вариантов: всё в double, всё в float, всё в half.

    Нужно для короткого ответа на вопрос «влезет ли это в float целиком».
    Возвращает список (имя формата, дерево, граница).
    """
    if bound_fn is None:
        from pareto.analysis import combined_bound
        bound_fn = combined_bound
    ideal = strip_rounding(tree)
    out = []
    try:
        out.append(('float64', ideal, bound_fn(ideal, domain)))
    except (ValueError, ZeroDivisionError, OverflowError, KeyError):
        pass
    for name in formats:
        op = round_op_for(format_by_name(name))
        if op is None:
            continue
        cand = _all_rounded(ideal, op)
        try:
            out.append((name, cand, bound_fn(cand, domain)))
        except (ValueError, ZeroDivisionError, OverflowError, KeyError):
            continue
    return out


def _all_rounded(tree, op):
    """Каждая операция считается в узком формате: округление после каждой."""
    if not isinstance(tree, tuple) or tree[0] in LEAF_OPS:
        return tree
    if tree[0] in ROUND_OPS:
        return _all_rounded(tree[1], op)
    kids = tuple(_all_rounded(k, op) for k in tree[1:])
    return (op, (tree[0],) + kids)
