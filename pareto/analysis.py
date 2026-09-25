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

# Сколько U стоит округление самой операции.
#
# Найдено фаззером 26.09.2026 и это был девятый дефект границы: на
# exp((x*y)*(y*y)) напечатанная граница 1.110e-16 оказалась НИЖЕ измеренной
# ошибки 1.341e-16. Причина — модель давала полулпы КАЖДОЙ операции. Для
# +, -, *, /, sqrt и fma это верно: IEEE-754 требует корректного округления.
# Для exp, log и прочей библиотечной математики стандарт не требует ничего,
# и реальные реализации промахиваются больше полулпы (на Windows тот кейс дал
# 1.21 U, на Linux тот же кейс прошёл — то есть дефект ещё и плавающий).
#
# Поэтому трансцендентным операциям даём целую улпу, то есть 2 U. Соперники
# в своих статьях обычно считают элементарные функции корректно округлёнными,
# так что на таких задачах наше сравнение теперь консервативно не в нашу пользу.
# Это сознательный размен: лучше проиграть в таблице, чем напечатать границу,
# которая не граница.
LIBM_ULP = float(__import__('os').environ.get('PARETO_LIBM_ULP', 2.0))

OP_ULP = {
    '+': 1.0, '-': 1.0, '*': 1.0, '/': 1.0, 'sqrt': 1.0, 'fma': 1.0, 'neg': 0.0,
    'exp': LIBM_ULP, 'log': LIBM_ULP, 'expm1': LIBM_ULP,
    'log1p': LIBM_ULP, 'hypot': LIBM_ULP,
}


def half_ulp(mag):
    """Настоящая граница ошибки округления к ближайшему: половина улпы.

    Мы годами писали U * |значение|, и это верно, но слабо. Для числа v из двоичного
    порядка [2^e, 2^(e+1)) половина улпы равна 2^(e-53), а U * |v| доходит до
    2^(e-52) — то есть ровно вдвое больше у чисел в верхней части порядка. На
    плотных многочленах таких операций десяток подряд, и набегает полтора раза.

    Найдено 26.09.2026 замером запаса: на sqroot FPTaylor давал 1.01 от реально
    измеренной ошибки, а мы 1.41. Вся разница сидела здесь.

    Берём улпу по МАКСИМУМУ модуля на домене: улпа монотонна по величине, значит
    это и есть максимум ошибки округления на всём домене. math.ulp правильно
    обрабатывает денормали, где шаг фиксирован и относительная модель врёт сильнее
    всего.
    """
    if not math.isfinite(mag):
        return INF
    if mag == 0.0:
        return 0.0
    return math.ulp(mag) / 2.0


def op_unit(op):
    """Бюджет округления операции в единицах U. Неизвестной операции — худшее."""
    return OP_ULP.get(op, LIBM_ULP)
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
    """Переполнение — это бесконечность, а не обрезанный максимум.

    ⛔ Здесь стояло min(arg, 700): аргумент молча зажимался, интервал выходил
    заниженным, и граница ошибки вместе с ним. Фаззинг 25.09.2026 поймал это на
    exp(y) при y ∈ [549, 702]: граница 3.4e288 при реальной ошибке 8.1e288.
    Верхний предел double для exp — около 709.78, дальше честная бесконечность.
    """
    if a[1] > 709.78:
        return (math.exp(a[0]) if a[0] <= 709.78 else INF, INF)
    return (math.exp(a[0]), math.exp(a[1]))


def iv_log(a):
    if a[0] <= 0:
        return (-INF, INF)
    return (math.log(a[0]), math.log(a[1]))


def iv_expm1(a):
    """То же ограничение, что и у exp: зажимать аргумент нельзя."""
    if a[1] > 709.78:
        return (math.expm1(a[0]) if a[0] <= 709.78 else INF, INF)
    return (math.expm1(a[0]), math.expm1(a[1]))


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


def _is_pow2(x):
    """Точная степень двойки (со знаком). Умножение на неё не округляет вовсе."""
    if x == 0.0 or not math.isfinite(x):
        return False
    m, _ = math.frexp(abs(x))
    return m == 0.5


def _normal_range(iv):
    """Результат далеко от денормалей и от переполнения — там точность гарантирована."""
    lo, hi = abs(iv[0]), abs(iv[1])
    top = max(lo, hi)
    return math.isfinite(top) and top < 1e290 and (top == 0.0 or top > 1e-290)


def exact_op(tree, kid_ivs, kid_errs, out_iv):
    """Округляет ли операция вообще. Две классические ситуации, когда нет.

    Обе давно известны и обе используются серьёзными анализаторами; мы платили за
    них полной ценой округления и ровно поэтому проигрывали FPTaylor там, где
    ветвление уже ничего не давало — на многочленах с двоичными коэффициентами.

    1. Умножение и деление на точную степень двойки только двигают экспоненту.
       Мантисса не меняется, округления нет (пока не задели денормали и переполнение).
       В наборе FPBench таких коэффициентов полно: 0.5, 0.125, 0.0625, 2, 4.
    2. Лемма Штербенца: если b/2 <= a <= 2b и оба одного знака, то a − b
       представимо точно. Это сердце всех компенсированных схем, и в разложениях
       функций такие вычитания встречаются постоянно.

    Проверка идёт по интервалам, причём с запасом на СОБСТВЕННУЮ ошибку аргументов:
    условие должно выполняться не для идеальных значений, а для тех чисел, которые
    реально окажутся в регистрах.
    """
    op = tree[0]
    if not _normal_range(out_iv):
        return False

    if op in ('*', '/'):
        for pos, kid in enumerate(tree[1:]):
            if kid[0] == 'num' and _is_pow2(float(kid[1])):
                # для деления точна только правая позиция: 2/x округляет
                if op == '/' and pos == 0:
                    continue
                other = kid_ivs[1 - pos]
                if _normal_range(other):
                    return True
        return False

    if op == '+':
        for pos, kid in enumerate(tree[1:]):
            if kid[0] == 'num' and float(kid[1]) == 0.0:
                return True
        return False

    if op == '-':
        (a0, a1), (b0, b1) = kid_ivs
        ea, eb = kid_errs[0], kid_errs[1]
        if not (math.isfinite(ea) and math.isfinite(eb)):
            return False
        # Раздвигаем интервалы на собственную ошибку: в регистрах лежат не идеальные
        # значения, а вычисленные, и лемма должна держаться именно для них.
        a0, a1 = a0 - ea, a1 + ea
        b0, b1 = b0 - eb, b1 + eb
        if b0 > 0.0 and a0 > 0.0:
            return a0 >= b1 / 2.0 and a1 <= 2.0 * b0
        if b1 < 0.0 and a1 < 0.0:
            return a1 <= b0 / 2.0 and a0 >= 2.0 * b1
        return False

    return False


def propagate_error(op, kid_ivs, kid_errs, out_iv, round_scale=1.0):
    """Верхняя граница АБСОЛЮТНОЙ ошибки результата.

    Модель стандартная: каждая операция в binary64 даёт относительную
    погрешность не больше U, плюс переносятся ошибки аргументов.
    """
    # round_scale=0 означает «округление этой операции скомпенсировано»: ошибки
    # аргументов при этом переносятся как обычно, потому что компенсация их не трогает
    round_off = round_scale * op_unit(op) * half_ulp(iv_abs_max(out_iv))
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
        top = iv_abs_max(a)
        if top > 709.78:
            return INF
        slope = math.exp(top)
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
def cse_work(tree):
    """Суммарная работа с учётом общих подвыражений: каждое считается один раз."""
    seen = set()
    total = 0.0

    def walk(node):
        nonlocal total
        key = repr(node)
        if key in seen:
            return
        seen.add(key)
        op = node[0]
        if op in ('num', 'var'):
            return
        if op in ('approx', 'eft'):
            walk(node[1])
            return
        total += COST.get(op, 1.0)
        for k in node[1:]:
            walk(k)

    walk(tree)
    return total


def partial_cost(tree, domain, depth):
    """Как tree_cost, но на верхних `depth` уровнях округление считается снятым.

    Нужно компенсированным формам: они убирают погрешность собственных операций,
    но не трогают то, что им подали на вход.
    """
    op = tree[0]
    if op == 'num':
        v = float(tree[1])
        return 0.0, 0.0, (v, v), 0.0, 0.0
    if op == 'var':
        return 0.0, 0.0, domain[tree[1]], 0.0, 0.0
    if depth <= 0 or op in ('approx', 'eft'):
        return tree_cost(tree, domain)
    kids = [partial_cost(k, domain, depth - 1) for k in tree[1:]]
    ivs = [k[2] for k in kids]
    out_iv = eval_interval(op, ivs)
    err = propagate_error(op, ivs, [k[1] for k in kids], out_iv, round_scale=0.0)
    work = COST[op] + sum(k[3] for k in kids)
    lat = COST[op] + max(k[4] for k in kids)
    return work + lat, err, out_iv, work, lat


def tighten(tree, domain, iv):
    """Сужает интервал узла аффинной оценкой, если та оказалась уже.

    Интервальная арифметика не помнит, что два вхождения x — это один и тот же x,
    и раздувает оценку тем сильнее, чем чаще повторяются переменные. Аффинная
    форма это помнит: x − x у неё ровно ноль, а не ширина домена. Берём пересечение
    двух оценок — хуже ни одна из них не делает, а уже почти всегда делает вторая.
    Именно этим наши границы отставали от Daisy и FPTaylor.
    """
    try:
        from pareto.affine import interval_of
        return interval_of(tree, domain, iv)
    except (ValueError, ZeroDivisionError, OverflowError, KeyError, RecursionError):
        return iv


def tree_cost(tree, domain):
    """(стоимость, граница абсолютной ошибки, интервал, работа, критический путь)."""
    op = tree[0]
    if op == 'num':
        v = float(tree[1])
        return 0.0, 0.0, (v, v), 0.0, 0.0
    if op == 'var':
        return 0.0, 0.0, domain[tree[1]], 0.0, 0.0
    if op == 'eft':
        # Компенсированная форма. Схема снимает округление ТОЛЬКО тех операций, из
        # которых она построена, — глубина указана самим генератором. Всё, что ниже,
        # считается обычным способом: компенсация деления не уточняет exp, поданный
        # ей на вход. Фаззинг ловит обе крайности — и завышенную границу, и лживую.
        inner = tree[1]
        orig = tree[2] if len(tree) > 2 else inner
        depth = tree[3] if len(tree) > 3 else 1
        _, args_err, iv, _, _ = partial_cost(orig, domain, depth)
        # Остаточное округление считается не от результата, а от НАИБОЛЬШЕЙ
        # промежуточной величины схемы. При сокращении близких чисел промежуточные
        # значения на порядки больше ответа, и ulp от результата занижает границу —
        # фаззинг ловил это на выражениях вида (y*(x*y)) + (y*y).
        scale = iv_abs_max(iv)
        for kid in orig[1:]:
            try:
                scale = max(scale, iv_abs_max(tree_cost(kid, domain)[2]))
            except (ValueError, ZeroDivisionError, OverflowError, KeyError):
                return INF, INF, iv, 0.0, 0.0
        work = cse_work(inner)
        _, _, _, _, lat = tree_cost(inner, domain)
        return work + lat, args_err + 2.0 * U * scale, iv, work, lat
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
    out_iv = tighten(tree, domain, out_iv)
    errs = [k[1] for k in kids]
    scale = 0.0 if exact_op(tree, ivs, errs, out_iv) else 1.0
    err = propagate_error(op, ivs, errs, out_iv, round_scale=scale)
    work = COST[op] + sum(k[3] for k in kids)
    lat = COST[op] + max(k[4] for k in kids)
    return work + lat, err, out_iv, work, lat


# ---------- ветвление по домену ----------
#
# Здесь мы проигрывали FPTaylor на всех девяти задачах FPBench, а на турбинах —
# в 14 раз. Причина не в модели ошибки, а в том, что коэффициенты оценивались
# интервалами СРАЗУ НА ВСЁМ домене: на широком прямоугольнике интервальная
# арифметика переоценивает величины на порядки. FPTaylor максимизирует остаток
# численно, поэтому и выигрывает.
#
# Ответ простой и строго корректный: разбить домен на части, посчитать границу на
# каждой и взять максимум. Это законно, потому что максимум ошибки на объединении
# равен максимуму из максимумов по частям, а интервальная арифметика монотонна по
# вложению — на более узком домене граница не может вырасти. Значит разбиение
# либо улучшает оценку, либо оставляет её прежней, и никогда не делает её ложной.
#
# Разбиение адаптивное: делим ту коробку, которая сейчас даёт худшую границу, и ту
# переменную в ней, у которой шире относительный разброс. Тратить деления на
# спокойные участки домена бессмысленно — вся ошибка сидит в одном углу.

SPLIT_BOXES = int(__import__('os').environ.get('PARETO_SPLIT_BOXES', 64))


def _rel_width(lo, hi):
    w = hi - lo
    if not math.isfinite(w) or w <= 0.0:
        return -1.0
    scale = max(abs(lo), abs(hi), 1e-300)
    return w / scale


def _widest_var(domain):
    best, bw = None, 0.0
    for k, (lo, hi) in domain.items():
        r = _rel_width(lo, hi)
        if r > bw:
            bw, best = r, k
    return best


def _halve(domain, var):
    lo, hi = domain[var]
    mid = lo + (hi - lo) / 2.0
    if not (lo < mid < hi):          # домен уже неделим в double
        return None
    a = dict(domain); a[var] = (lo, mid)
    b = dict(domain); b[var] = (mid, hi)
    return a, b


def combined_bound(tree, domain):
    """Лучшая из двух честных оценок на одном домене.

    Интервальная и символическая считают одно и то же разными путями: первая
    копит худшие случаи по узлам, вторая держит округления именованными символами
    и сокращает те, что физически одно и то же событие. Обе верны сверху, поэтому
    минимум тоже верен. На выражениях с повторяющимися подвыражениями символическая
    бьёт интервальную в сотни раз, на остальных — наоборот.
    """
    e = tree_cost(tree, domain)[1]
    try:
        from pareto.symbolic_cost import symbolic_bound
        sb = symbolic_bound(tree, domain)
        if sb is not None and sb < e:
            e = sb
    except (RecursionError, ValueError, ZeroDivisionError, OverflowError, KeyError):
        pass
    return e


def tree_cost_refined(tree, domain, boxes=None):
    """То же, что tree_cost, но граница уточнена ветвлением по домену.

    Стоимость, работа и критический путь от домена не зависят — берём из общего
    прогона. Возвращаемый интервал — оболочка по частям.
    """
    base = tree_cost(tree, domain)
    base_err = combined_bound(tree, domain)
    n_boxes = SPLIT_BOXES if boxes is None else boxes
    if n_boxes <= 1 or not domain or not math.isfinite(base_err) or base_err == 0.0:
        return base[0], base_err, base[2], base[3], base[4]

    work = [(base_err, domain, base[2])]
    made = 1
    while made < n_boxes:
        i = max(range(len(work)), key=lambda k: work[k][0])
        err_i, dom_i, _ = work[i]
        if not math.isfinite(err_i):
            break
        v = _widest_var(dom_i)
        if v is None:
            break
        halves = _halve(dom_i, v)
        if halves is None:
            break
        try:
            r1 = tree_cost(tree, halves[0])
            r2 = tree_cost(tree, halves[1])
            e1 = combined_bound(tree, halves[0])
            e2 = combined_bound(tree, halves[1])
        except (ValueError, ZeroDivisionError, OverflowError, KeyError):
            break
        work[i] = (e1, halves[0], r1[2])
        work.append((e2, halves[1], r2[2]))
        made += 1

    err = max(w[0] for w in work)
    lo = min(w[2][0] for w in work)
    hi = max(w[2][1] for w in work)
    # Подстраховка: если из-за tighten или модели рядов где-то нарушилась
    # монотонность, берём лучшее из двух — обе оценки сами по себе состоятельны.
    return base[0], min(err, base_err), (lo, hi), base[3], base[4]


def refine_front(front, domain, boxes=None):
    """Уточняет границы у готовых точек фронта.

    Поиск идёт на дешёвой оценке — иначе каждая из тысяч вариаций платила бы за
    ветвление. Ветвление применяется один раз к тем восьми формам, которые реально
    поедут в отчёт. Порядок точек сохраняется.
    """
    out = []
    for pt in front:
        cost, err, tree = pt[0], pt[1], pt[2]
        try:
            # Во фронте лежит ГОТОВАЯ к печати формула, и для разложений в ряд это
            # уже очищенный многочлен — остаток метода в нём не виден, он был учтён
            # при анализе обёрнутого дерева. Пересчёт по такой формуле даёт границу
            # без остатка, то есть враньё в тысячи раз: на exp(-0.723) вышло
            # 1.078e-16 против реальных 5.307e-02. Поэтому уточняем только те точки,
            # где пересчёт воспроизводит исходную границу, — значит дерево то самое.
            plain = combined_bound(tree, domain)
            same = (math.isfinite(plain) and math.isfinite(err)
                    and abs(plain - err) <= 1e-12 * max(abs(plain), abs(err), 1e-300))
            err2 = tree_cost_refined(tree, domain, boxes=boxes)[1] if same else err
        except (ValueError, ZeroDivisionError, OverflowError, KeyError):
            err2 = err
        out.append((cost, err2) + tuple(pt[2:]))
    # Порядок НЕ трогаем. Пересортировка здесь стоила часа разбора 26.09.2026:
    # вызывающий код держит формы и границы двумя параллельными списками, и
    # перестановка тихо сдвинула границы относительно форм — фаззинг показал
    # тринадцать «нарушений», которых на деле не было.
    return out


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
        # Символическая форма ошибки учитывает, что одинаковые подвыражения
        # округляются одинаково, и на выражениях с повторами даёт куда более тугую
        # оценку — на (x*x−1)/(x−1) в 478 раз. Берём лучшую из двух: обе верны
        # сверху, поэтому минимум тоже верен.
        try:
            from pareto.symbolic_cost import symbolic_bound
            sb = symbolic_bound(tree, domain)
            if sb is not None and sb < e2:
                e2 = sb
        except (RecursionError, ValueError, ZeroDivisionError, OverflowError, KeyError):
            pass
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
        from pareto.eft import eft_candidates
        for _, _, seed, _, _ in out:
            extra.extend(eft_candidates(seed, domain))
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
