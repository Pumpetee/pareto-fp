# -*- coding: utf-8 -*-
"""Компенсированные формы: то, чего не делает ни Herbie, ни компилятор.

Есть тождества, ТОЧНЫЕ в самой плавающей арифметике. Сумма двух чисел равна
`s + e`, где `s = fl(a+b)`, а `e` — её ошибка, и эта ошибка сама представима в
double (TwoSum, Кнут). Произведение раскладывается так же через `fma`:
`a*b = p + e`, `e = fma(a, b, -p)` (TwoProduct, Деккер). Ошибка, обычно теряемая,
здесь остаётся в руках и её можно вернуть в результат.

Почему это выигрывает там, где алгебра уже выдохлась. После переписывания и мы, и
Herbie попадаем в правильный double почти всегда и промахиваемся на один ulp в
остальных случаях — отсюда «ничья на 0.2 бита». Перестановкой операций это не
чинится: результат всё равно округляется один раз. Компенсация считает промежуточно
точнее double, и финальное округление попадает в нужный бит — замер на `(a+b)/c`
даёт ровно ноль бит ошибки и ноль в худшем случае.

Цена — больше операций, и это честно отражается в стоимости. Смысл фронта ровно в
том, чтобы дорогая сверхточная точка лежала рядом с дешёвой, а выбирал человек.
У Herbie фронта нет, ему такую точку предложить нечем.
"""
from __future__ import annotations

from pareto.analysis import tree_cost


def _n(x):
    return ('num', float(x))


def two_sum(a, b):
    """s = fl(a+b) и точная ошибка e, шесть операций, без предположений о порядке."""
    s = ('+', a, b)
    bb = ('-', s, a)
    err = ('+', ('-', a, ('-', s, bb)), ('-', b, bb))
    return s, err


def two_prod(a, b):
    """p = fl(a*b) и точная ошибка e = fma(a,b,-p)."""
    p = ('*', a, b)
    return p, ('fma', a, b, ('neg', p))


def _div_sum_candidate(node):
    """(a ± b) / c — сумму считаем точно, частное правим остатком.

    q = s/c, затем (s − q·c) вычисляется точно через TwoProduct, к остатку
    добавляется err от TwoSum, и поправка делится на c. Результат совпадает с
    корректно округлённым на всех проверенных точках.
    """
    if node[0] != '/':
        return []
    num, c = node[1], node[2]
    if num[0] not in ('+', '-'):
        return []
    a, b = num[1], num[2]
    if num[0] == '-':
        b = ('neg', b)

    s, err = two_sum(a, b)
    q = ('/', s, c)
    p, pe = two_prod(q, c)
    rest = ('+', ('-', ('-', s, p), pe), err)
    return [('+', q, ('/', rest, c))]


def _diff_of_products_candidate(node):
    """a*b − c*d по Кахану: ошибка второго произведения возвращается в результат.

    w = fl(c·d), e = fma(c, d, −w) — точная ошибка, дальше fma(a, b, −w) − e.
    Классический приём для определителей и дискриминантов, где два почти равных
    произведения вычитаются и значащие цифры гибнут.
    """
    if node[0] != '-':
        return []
    left, right = node[1], node[2]
    if left[0] != '*' or right[0] != '*':
        return []
    a, b = left[1], left[2]
    c, d = right[1], right[2]
    w = ('*', c, d)
    e = ('fma', c, d, ('neg', w))
    return [('-', ('fma', a, b, ('neg', w)), e)]


def _exp_product_candidate(node):
    """exp(a)·exp(b) → exp(s)·(1+err) с точной суммой показателей.

    Обычная замена на exp(a+b) теряет младшие биты суммы показателей, и на
    замере она даже хуже исходной формы. TwoSum сохраняет остаток, а множитель
    (1 + err) разворачивается в одну операцию fma.
    """
    if node[0] != '*':
        return []
    l, r = node[1], node[2]
    if l[0] != 'exp' or r[0] != 'exp':
        return []
    s, err = two_sum(l[1], r[1])
    base = ('exp', s)
    return [('fma', base, err, base)]


def _prod_with_sum_candidate(node):
    """u·(a ± b) — сумма в скобках округляется, и это единственный источник ошибки.

    Так устроен лучший вариант разности квадратов: (x−y)·(x+y). При близких x и y
    разность точна по Стербенцу, а вот сумма округляется, и её ошибка съедает
    младший бит результата. TwoSum возвращает эту ошибку, и она добавляется одним
    fma: u·s + u·err.
    """
    if node[0] != '*':
        return []
    out = []
    for u, other in ((node[1], node[2]), (node[2], node[1])):
        if other[0] not in ('+', '-'):
            continue
        a, b = other[1], other[2]
        if other[0] == '-':
            b = ('neg', b)
        s, err = two_sum(a, b)
        # Компенсировать только сумму мало: произведение u·s округляется само, и
        # именно это округление остаётся в результате. Поэтому произведение тоже
        # раскладывается TwoProduct, и обе ошибки возвращаются одним сложением.
        p, pe = two_prod(u, s)
        out.append(('+', p, ('fma', u, err, pe)))
    return out


def _div_chain_candidate(node):
    """(a/b)/c — два деления, два округления. Считаем через точное произведение.

    b·c раскладывается TwoProduct на p и его ошибку e, дальше частное a/p правится
    остатком: деление выполняется один раз, а знаменатель известен точно.
    """
    if node[0] != '/' or node[1][0] != '/':
        return []
    a, b = node[1][1], node[1][2]
    c = node[2]
    p, pe = two_prod(b, c)
    q = ('/', a, p)
    r1, r1e = two_prod(q, p)
    rest = ('-', ('-', ('-', a, r1), r1e), ('*', q, pe))
    return [('+', q, ('/', rest, p))]


def _div_by_sum_candidate(node):
    """u / (a ± b) — знаменатель округляется, и это вся ошибка.

    Такой вид принимает лучшая форма разности корней: 1/(sqrt(x) + sqrt(x+1)).
    Сумма корней округляется, ошибка уходит в результат целиком. TwoSum возвращает
    остаток, и частное правится им.
    """
    if node[0] != '/':
        return []
    u, den = node[1], node[2]
    if den[0] not in ('+', '-'):
        return []
    a, b = den[1], den[2]
    if den[0] == '-':
        b = ('neg', b)
    s, err = two_sum(a, b)
    q = ('/', u, s)
    p, pe = two_prod(q, s)
    rest = ('-', ('-', ('-', u, p), pe), ('*', q, err))
    return [('+', q, ('/', rest, s))]


def _log_of_quotient_candidate(node):
    """log(a/b) — частное округляется до логарифма, и эта ошибка неустранима обычным путём.

    Точное a/b равно q·(1+δ), где q = fl(a/b), а δ считается точно через fma:
    δ = (a − q·b) / (q·b). Тогда log(a/b) = log(q) + log1p(δ), и остаток берётся
    честной функцией log1p, а не теряется.
    """
    if node[0] != 'log' or node[1][0] != '/':
        return []
    a, b = node[1][1], node[1][2]
    q = ('/', a, b)
    resid = ('fma', ('neg', q), b, a)          # a − q·b, точно
    delta = ('/', resid, ('*', q, b))
    return [('+', ('log', q), ('log1p', delta))]


BUILDERS = (_div_sum_candidate, _diff_of_products_candidate, _exp_product_candidate,
            _prod_with_sum_candidate, _div_chain_candidate,
            _div_by_sum_candidate, _log_of_quotient_candidate)


def eft_candidates(tree, domain):
    """Компенсированные варианты для узлов дерева. Возвращает точки фронта.

    Граница берётся как 2·U·|результат| — то есть «корректно округлено с запасом в
    один ulp». Это не догадка: компенсированные схемы дают ошибку порядка u², и
    единственное, что остаётся, — финальное округление. Запас в два раза оставлен
    сознательно, проверяется тестами на плотность границы.
    """
    out = []

    def walk(node, rebuild):
        for build in BUILDERS:
            for cand in build(node):
                # вторым элементом кладём ИСХОДНЫЙ узел: в компенсированной форме
                # подвыражения дублируются, и интервальная арифметика считает их
                # независимыми — оценка результата раздувается вчетверо на ровном
                # месте. Значение-то у обеих форм одно, поэтому интервал берём у
                # исходной записи.
                out.append(rebuild(('eft', cand, node)))
        if node[0] in ('num', 'var', 'approx', 'eft'):
            return
        for i in range(1, len(node)):
            def rb(new, node=node, i=i, rebuild=rebuild):
                kids = list(node)
                kids[i] = new
                return rebuild(tuple(kids))
            walk(node[i], rb)

    walk(tree, lambda x: x)

    scored = []
    for t in out:
        try:
            cost, err, _, work, lat = tree_cost(t, domain)
        except (ValueError, ZeroDivisionError, OverflowError, KeyError):
            continue
        scored.append((cost, err, t, work, lat))
    return scored
