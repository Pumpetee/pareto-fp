# -*- coding: utf-8 -*-
"""Программа, а не одна формула: локальные переменные, ветвления, циклы.

Зачем. До этого модуля инструмент принимал ровно одно арифметическое выражение.
Настоящий численный код так не выглядит: там объявлены промежуточные величины,
есть `if` на границе области определения и есть циклы. Натравить анализ на чужой
файл было нельзя — только на выписанную из файла формулу. Здесь это закрывается.

Что поддержано и как именно:

* **локальные переменные** — подстановкой. Величина, вычисленная один раз и
  использованная трижды, это ОДНО округление, а не три, и подстановка это
  сохраняет: в дереве появляются три одинаковых поддерева, а символическая форма
  ошибки даёт им один символ округления. То есть модель видит переиспользование
  правильно, без отдельного понятия «переменная»;
* **ветвления** — перечислением путей. У каждого пути своё выражение и своя
  область достижимости, граница считается на каждом и берётся максимум. Это
  законно: максимум по объединению равен максимуму из максимумов;
* **неустойчивый тест** — отдельным слагаемым, и это главная тонкость всей темы.
  Условие проверяется по ВЫЧИСЛЕННЫМ значениям, у которых есть своя погрешность.
  Значит вблизи границы программа может уйти не в ту ветку, и тогда ошибка
  относительно идеального значения включает целый СКАЧОК между ветками, а не
  погрешности округления. Без этого слагаемого любая граница для кода с `if` —
  ложь, и именно на этом ломаются наивные реализации;
* **циклы** — двумя способами. Цикл с известным числом шагов разворачивается и
  дальше считается как прямолинейный код. Цикл-редукция (накопление суммы по
  массиву) уходит в `pareto/reductions.py`, где границы схем суммирования взяты
  из Higham. Цикл с неизвестным числом шагов и зависимостью между итерациями
  честно отклоняется: доказать про него что-то этими средствами нельзя.

Формат операторов (кортежи, как и всё остальное в проекте):

    ('let', имя, выражение)            промежуточная величина в binary64
    ('let', имя, выражение, 'f32')     она же, но объявленная как float
    ('if', условие, [ветка], [ветка])  условие: (отношение, слева, справа)
    ('return', выражение)
"""
from __future__ import annotations

import math

from pareto.analysis import (INF, combined_bound, iv_abs_max, iv_abs_min,
                            tree_cost, tree_cost_refined)
from pareto.mixed import strip_rounding
from pareto.precision import ROUND_OPS

RELOPS = ('<', '<=', '>', '>=', '==', '!=')
LEAF_OPS = ('num', 'var', 'approx', 'eft')

# Ограничители. Все три существуют, чтобы отказ был громким, а не в виде часа
# молчания и съеденной памяти.
MAX_PATHS = int(64)
MAX_NODES = int(20000)
MAX_UNROLL = int(64)


class ProgramError(ValueError):
    """Программа вне того, что метод умеет доказывать. Отказ, а не догадка."""


# ---------- подстановка локальных переменных ----------
def substitute(tree, env):
    """Заменить переменные из env их деревьями. Остальные оставить как есть."""
    if not isinstance(tree, tuple):
        return tree
    op = tree[0]
    if op == 'var':
        return env.get(tree[1], tree)
    if op in ('num', 'approx', 'eft'):
        return tree
    return (op,) + tuple(substitute(k, env) for k in tree[1:])


def node_count(tree):
    if not isinstance(tree, tuple) or tree[0] in LEAF_OPS:
        return 1
    return 1 + sum(node_count(k) for k in tree[1:])


# ---------- перечисление путей ----------
class Path:
    """Один путь исполнения: набор решённых условий и возвращаемое выражение."""

    __slots__ = ('guards', 'expr', 'label')

    def __init__(self, guards, expr, label):
        self.guards = tuple(guards)      # [(условие, True/False), ...]
        self.expr = expr
        self.label = label

    def __repr__(self):
        return 'Path({}, {})'.format(self.label, self.guards)


def paths(stmts, env=None, guards=(), label=''):
    """Все пути исполнения с уже подставленными локальными переменными."""
    env = dict(env or {})
    out = []
    for i, st in enumerate(stmts):
        kind = st[0]
        if kind == 'let':
            name, expr = st[1], st[2]
            value = substitute(expr, env)
            if len(st) > 3 and st[3]:
                op = st[3]
                if op not in ROUND_OPS:
                    raise ProgramError('unknown storage format: {}'.format(op))
                value = (op, value)
            if node_count(value) > MAX_NODES:
                raise ProgramError(
                    'after inlining the local variables the expression for {} has more '
                    'than {} nodes. Straight-line code this large is out of reach for '
                    'this method; split the function.'.format(name, MAX_NODES))
            env[name] = value
            continue
        if kind == 'if':
            cond, then_s, else_s = st[1], st[2], st[3]
            cond = (cond[0], substitute(cond[1], env), substitute(cond[2], env))
            if cond[0] not in RELOPS:
                raise ProgramError('unsupported comparison: {}'.format(cond[0]))
            rest = stmts[i + 1:]
            out += paths(list(then_s) + list(rest), env,
                         guards + ((cond, True),), label + 'T')
            out += paths(list(else_s) + list(rest), env,
                         guards + ((cond, False),), label + 'F')
            if len(out) > MAX_PATHS:
                raise ProgramError(
                    'more than {} execution paths. Every path is analysed separately, '
                    'so the cost grows with their number; split the function or reduce '
                    'the nesting of the conditionals.'.format(MAX_PATHS))
            return out
        if kind == 'return':
            return [Path(guards, substitute(st[1], env), label or 'main')]
        raise ProgramError('unsupported statement: {}'.format(kind))
    raise ProgramError('a branch of the function ends without returning a value')


# ---------- разбиение домена под условия ----------
def _widest(domain):
    best, bw = None, 0.0
    for k, (lo, hi) in domain.items():
        w = hi - lo
        scale = max(abs(lo), abs(hi), 1e-300)
        r = w / scale if math.isfinite(w) and w > 0 else -1.0
        if r > bw:
            bw, best = r, k
    return best


def _halve(domain, var):
    lo, hi = domain[var]
    mid = lo + (hi - lo) / 2.0
    if not (lo < mid < hi):
        return None
    a, b = dict(domain), dict(domain)
    a[var] = (lo, mid)
    b[var] = (mid, hi)
    return a, b


def guard_status(cond, box):
    """Решено ли условие на этой коробке и может ли оно «сорваться».

    Возвращает (значение или None, может_ли_сорваться). Значение None означает,
    что на коробке условие и так и так возможно.

    «Сорваться» — это и есть неустойчивый тест: сравниваются не идеальные
    величины, а вычисленные, с погрешностью. Если разность сторон по модулю может
    оказаться не больше собственной погрешности, программа имеет право уйти в
    любую ветку, и это надо учесть отдельным слагаемым.
    """
    rel, lhs, rhs = cond
    diff = ('-', lhs, rhs)
    try:
        # Погрешность СРАВНЕНИЯ — это сумма погрешностей сторон, и ничего больше.
        # Считать её как погрешность вычитания было бы неверно: процессор не
        # вычитает, он сравнивает два лежащих в регистрах числа, и делает это
        # точно. Лишнее округление здесь объявляло неустойчивым даже `x > 1`, где
        # округления нет вовсе, и в границу уезжал скачок между ветками, которого
        # не бывает.
        _, err_l, _, _, _ = tree_cost(lhs, box)
        _, err_r, _, _, _ = tree_cost(rhs, box)
        err = err_l + err_r
        # А вот интервал самой разности берём через дерево вычитания: там работает
        # аффинная арифметика, которая помнит общие подвыражения сторон и не
        # раздувает оценку там, где стороны почти одинаковы.
        iv = tree_cost(diff, box)[2]
    except (ValueError, ZeroDivisionError, OverflowError, KeyError):
        return None, True
    if any(math.isnan(v) for v in iv):
        return None, True
    lo, hi = iv
    if not math.isfinite(err):
        return None, True

    # Может ли ВЫЧИСЛЕННАЯ разность оказаться по другую сторону от нуля. Нулевая
    # погрешность сравнения означает, что тест точен и сорваться не может — это не
    # редкий случай, а обычный: `x > 1` при переменной x считается без округления
    # вовсе. Без проверки err > 0 такое сравнение объявлялось бы неустойчивым, и в
    # границу уезжал бы скачок между ветками, которого не бывает.
    unstable = err > 0.0 and iv_abs_min(iv) <= err

    if rel in ('==', '!='):
        # Равенство вещественных чисел решается только если ноль вне интервала
        if lo > 0.0 or hi < 0.0:
            return (rel == '!='), unstable
        return None, True
    if rel == '<':
        if hi < 0.0:
            return True, unstable
        if lo >= 0.0:
            return False, unstable
    elif rel == '<=':
        if hi <= 0.0:
            return True, unstable
        if lo > 0.0:
            return False, unstable
    elif rel == '>':
        if lo > 0.0:
            return True, unstable
        if hi <= 0.0:
            return False, unstable
    elif rel == '>=':
        if lo >= 0.0:
            return True, unstable
        if hi < 0.0:
            return False, unstable
    return None, unstable


def split_for_guards(domain, conds, max_boxes=32):
    """Разбить домен так, чтобы условия были решены как можно чаще.

    Делим ту коробку, на которой больше всего нерешённых условий, по самой широкой
    переменной. Смысл ровно тот же, что у ветвления домена в основном анализе:
    интервальная арифметика монотонна по вложению, поэтому разбиение никогда не
    делает оценку ложной — только уточняет её.
    """
    boxes = [dict(domain)]
    if not conds or not domain:
        return boxes
    while len(boxes) < max_boxes:
        scored = []
        for i, b in enumerate(boxes):
            undecided = sum(1 for c in conds if guard_status(c, b)[0] is None)
            scored.append((undecided, i))
        scored.sort(reverse=True)
        if scored[0][0] == 0:
            break                        # всё решено, делить больше нечего
        idx = scored[0][1]
        v = _widest(boxes[idx])
        if v is None:
            break
        halves = _halve(boxes[idx], v)
        if halves is None:
            break
        boxes[idx] = halves[0]
        boxes.append(halves[1])
    return boxes


def reachable(path, boxes):
    """Коробки, на которых этот путь возможен.

    Условие решено против нас — путь на этой коробке невозможен, коробку
    выбрасываем. Не решено — оставляем: так оценка остаётся верхней.
    """
    out = []
    for b in boxes:
        ok = True
        for cond, want in path.guards:
            val, _ = guard_status(cond, b)
            if val is not None and val != want:
                ok = False
                break
        if ok:
            out.append(b)
    return out


def hull(boxes):
    """Объемлющая коробка — по ней ведётся ПОИСК формы, а не считается граница."""
    if not boxes:
        return {}
    out = {}
    for k in boxes[0]:
        lo = min(b[k][0] for b in boxes)
        hi = max(b[k][1] for b in boxes)
        out[k] = (lo, hi)
    return out


# ---------- граница на пути и слагаемое за неустойчивый тест ----------
def path_bound(expr, boxes, refine_boxes=8):
    """Максимум границы ошибки выражения по набору коробок."""
    from pareto import budget
    if not boxes:
        return 0.0
    worst = 0.0
    for n, b in enumerate(boxes):
        # Часы режут только УТОЧНЕНИЕ по коробкам: максимум по уже пройденным
        # коробкам верхней оценкой не станет, поэтому бросить нельзя — дальше
        # считаем по одной коробке на весь остаток, то есть грубее, но верно.
        if n and budget.expired('per-box refinement of the branches'):
            refine_boxes = 1
        try:
            e = tree_cost_refined(expr, b, boxes=refine_boxes)[1]
        except (ValueError, ZeroDivisionError, OverflowError, KeyError):
            return INF
        if not math.isfinite(e):
            return INF
        worst = max(worst, e)
    return worst


def _first_divergence(a, b):
    """Условие, на котором два пути расходятся впервые. None — пути несравнимы.

    Сравнивать по «различию ровно в одном условии» было бы неверно: если ветки
    содержат разные вложенные `if`, у путей разной длины наборы условий, и такая
    пара молча выпала бы из учёта вместе со своим скачком. Правильный признак
    другой — общий префикс решений, а на первом же расхождении одно и то же
    условие с разными исходами. Что дальше внутри ветки, неважно: сорвавшийся тест
    уводит исполнение в чужое поддерево, и там оно идёт как пойдёт.
    """
    n = min(len(a.guards), len(b.guards))
    for i in range(n):
        ca, va = a.guards[i]
        cb, vb = b.guards[i]
        if ca != cb:
            return None
        if va != vb:
            return ca
    return None


def divergence_terms(path_list, boxes, exprs):
    """Цена неустойчивого теста: сколько может стоить уход не в ту ветку.

    Для пары путей, расходящихся по одному условию, берём коробки, на которых это
    условие может сорваться и оба пути возможны, и оцениваем на них МОДУЛЬ РАЗНОСТИ
    идеальных значений двух ветвей. Это и есть величина скачка: если программа
    ушла в чужую ветку, результат отличается от идеального примерно на неё.

    К скачку прибавляется погрешность самой ветки, в которую ушли: обе ошибки
    складываются, гасить их нечем.

    Важная деталь: разность считается по ИДЕАЛЬНЫМ выражениям обеих ветвей, и
    интервал для неё уточняется аффинной арифметикой, которая помнит общие
    подвыражения. У ветвей они почти всегда общие — на границе ветки обычно
    совпадают, и тогда скачок честно выходит близким к нулю, а не шириной домена.
    """
    out = []
    for i, p in enumerate(path_list):
        for q in path_list[i + 1:]:
            cond = _first_divergence(p, q)
            if cond is None:
                continue
            risky = []
            for b in boxes:
                val, unstable = guard_status(cond, b)
                if not unstable:
                    continue
                # обе ветки должны быть возможны на этой коробке, не считая самого
                # спорного условия
                if not _possible_ignoring(p, cond, b) or not _possible_ignoring(q, cond, b):
                    continue
                risky.append(b)
            if not risky:
                continue
            gap = 0.0
            ideal_p = strip_rounding(exprs[id(p)])
            ideal_q = strip_rounding(exprs[id(q)])
            if ideal_p == ideal_q:
                # Ветви считают одно и то же — уходить «не туда» просто некуда, и
                # скачка не существует. Проверка структурная, поэтому это точное
                # утверждение, а не оценка: интервальная оценка разности здесь дала
                # бы маленькое, но ненулевое число из-за остатков линеаризации.
                continue
            jump = ('-', ideal_p, ideal_q)
            for b in risky:
                try:
                    iv = tree_cost(jump, b)[2]
                except (ValueError, ZeroDivisionError, OverflowError, KeyError):
                    gap = INF
                    break
                gap = max(gap, iv_abs_max(iv))
            out.append({'cond': cond, 'gap': gap, 'boxes': len(risky),
                        'paths': (p.label, q.label)})
    return out


def _possible_ignoring(path, skip_cond, box):
    for cond, want in path.guards:
        if cond == skip_cond:
            continue
        val, _ = guard_status(cond, box)
        if val is not None and val != want:
            return False
    return True


# ---------- разворот циклов ----------
def unroll(var, start, stop, step, body):
    """Цикл с известным числом шагов -> прямолинейный код.

    Разворот — единственный полностью честный способ доказать что-то про цикл
    этими средствами: после него код прямолинейный, и работает весь остальной
    аппарат без единой новой аксиомы. Цена — размер, поэтому число шагов
    ограничено, и превышение это громкий отказ, а не молчаливая срезка.
    """
    if step == 0:
        raise ProgramError('a loop with zero step never terminates')
    count = 0
    i = start
    while (i < stop) if step > 0 else (i > stop):
        count += 1
        i += step
        if count > MAX_UNROLL:
            raise ProgramError(
                'the loop runs more than {} times. Unrolling is the only sound way this '
                'method knows to handle a loop; for a longer one use a reduction scheme '
                '(pareto/reductions.py) or bound the trip count.'.format(MAX_UNROLL))
    out = []
    i = start
    for _ in range(count):
        subst = {var: ('num', float(i))}
        for st in body:
            out.append(_subst_stmt(st, subst))
        i += step
    return out


def _subst_stmt(st, env):
    kind = st[0]
    if kind == 'let':
        rest = tuple(st[3:])
        return ('let', st[1], substitute(st[2], env)) + rest
    if kind == 'return':
        return ('return', substitute(st[1], env))
    if kind == 'if':
        cond = (st[1][0], substitute(st[1][1], env), substitute(st[1][2], env))
        return ('if', cond, [_subst_stmt(s, env) for s in st[2]],
                [_subst_stmt(s, env) for s in st[3]])
    raise ProgramError('unsupported statement: {}'.format(kind))


# ---------- исполнение программы: замер против идеала ----------
def run_float(stmts, env):
    """Выполнить программу так, как её выполнит машина.

    Условия решаются по ВЫЧИСЛЕННЫМ значениям, с округлениями. Это ровно то, что
    делает процессор, и ровно то, из-за чего возможен неустойчивый тест: сравнение
    видит не идеальные величины, а те, что лежат в регистрах.
    """
    from pareto.evalfp import eval_float
    env = dict(env)
    for st in stmts:
        kind = st[0]
        if kind == 'let':
            value = eval_float(st[2], env)
            if len(st) > 3 and st[3]:
                value = ROUND_OPS[st[3]].round(value)
            env[st[1]] = value
        elif kind == 'return':
            return eval_float(st[1], env)
        elif kind == 'if':
            rel, lhs, rhs = st[1]
            left, right = eval_float(lhs, env), eval_float(rhs, env)
            taken = _compare(rel, left, right)
            return run_float(list(st[2] if taken else st[3]), env)
        else:
            raise ProgramError('unsupported statement: {}'.format(kind))
    raise ProgramError('a branch of the function ends without returning a value')


def run_exact(stmts, env, prec=60):
    """Идеальное значение программы: вещественная арифметика, без округлений.

    Условия здесь решаются по ИДЕАЛЬНЫМ величинам — это и есть та программа, с
    которой сравнивается настоящая. None означает, что эталон сам себе не доверяет
    на этой точке: такую точку не судят.
    """
    from decimal import Decimal, localcontext
    from pareto.exactref import exact_stable
    env = dict(env)
    with localcontext() as ctx:
        ctx.prec = prec
        for st in stmts:
            kind = st[0]
            if kind == 'let':
                v = exact_stable(strip_rounding(st[2]), env)
                if v is None:
                    return None
                env[st[1]] = v
            elif kind == 'return':
                return exact_stable(strip_rounding(st[1]), env)
            elif kind == 'if':
                rel, lhs, rhs = st[1]
                left = exact_stable(strip_rounding(lhs), env)
                right = exact_stable(strip_rounding(rhs), env)
                if left is None or right is None:
                    return None
                taken = _compare(rel, left, right)
                return run_exact(list(st[2] if taken else st[3]), env, prec=prec)
            else:
                raise ProgramError('unsupported statement: {}'.format(kind))
    raise ProgramError('a branch of the function ends without returning a value')


def _compare(rel, a, b):
    if rel == '<':
        return a < b
    if rel == '<=':
        return a <= b
    if rel == '>':
        return a > b
    if rel == '>=':
        return a >= b
    if rel == '==':
        return a == b
    if rel == '!=':
        return a != b
    raise ProgramError('unsupported comparison: {}'.format(rel))


# ---------- анализ программы целиком ----------
def analyse_program(stmts, domain, max_boxes=32, refine_boxes=8, optimise=None):
    """Граница ошибки программы с ветвлениями и, если дана, переписанная форма.

    optimise — функция (выражение, домен) -> (новое выражение, его граница) либо
    None. Через неё подключается обычный поиск по e-графу, применённый к КАЖДОМУ
    пути отдельно: у разных ветвей разная арифметика и разные области, поэтому и
    лучшая форма у них разная. Компилятор ровно так и поступает.
    """
    path_list = paths(stmts)
    conds = []
    for p in path_list:
        for c, _ in p.guards:
            if c not in conds:
                conds.append(c)
    boxes = split_for_guards(domain, conds, max_boxes=max_boxes)

    exprs = {}
    report = []
    base_worst = 0.0
    best_worst = 0.0
    for p in path_list:
        exprs[id(p)] = p.expr
        rb = reachable(p, boxes)
        if not rb:
            report.append({'label': p.label, 'guards': p.guards, 'reachable': False,
                           'base': 0.0, 'best': 0.0, 'expr': p.expr, 'form': p.expr})
            continue
        base = path_bound(p.expr, rb, refine_boxes=refine_boxes)
        form, best = p.expr, base
        if optimise is not None:
            try:
                form, best = optimise(p.expr, hull(rb))
            except Exception:
                form, best = p.expr, base
            # граница переписанной формы пересчитывается на ТЕХ ЖЕ коробках, а не
            # берётся из поиска: поиск шёл по объемлющей коробке, и его число могло
            # оказаться как хуже, так и лучше
            best = path_bound(form, rb, refine_boxes=refine_boxes)
            if not (math.isfinite(best) and best <= base):
                form, best = p.expr, base
        base_worst = max(base_worst, base)
        best_worst = max(best_worst, best)
        report.append({'label': p.label, 'guards': p.guards, 'reachable': True,
                       'boxes': len(rb), 'base': base, 'best': best,
                       'expr': p.expr, 'form': form})

    div = divergence_terms(path_list, boxes, exprs)
    div_extra = 0.0
    for d in div:
        if not math.isfinite(d['gap']):
            div_extra = INF
            break
        div_extra = max(div_extra, d['gap'])

    # Итог складывается из двух слагаемых, и оба обязательны.
    #
    # Первое — максимум погрешности округления по достижимым путям: там, где тест
    # решается уверенно, ошибка не больше, чем у той ветки, которая исполнится.
    #
    # Второе — цена сорвавшегося теста: скачок между ветками плюс погрешность той
    # ветки, в которую в итоге ушли. Погрешность берётся максимальной по путям —
    # это верхняя оценка для любой конкретной ветки, значит сумма тоже верхняя.
    #
    # Складываем, а не берём максимум: у неустойчивого теста ошибка равна скачку И
    # погрешности ветки одновременно.
    total_base = base_worst + div_extra if math.isfinite(div_extra) else INF
    total_best = best_worst + div_extra if math.isfinite(div_extra) else INF
    return {
        'paths': report,
        'boxes': len(boxes),
        'base_bound': total_base,
        'best_bound': total_best,
        'stable_base': base_worst,
        'stable_best': best_worst,
        'divergence': div,
        'divergence_extra': div_extra,
    }
