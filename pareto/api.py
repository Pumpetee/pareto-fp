# -*- coding: utf-8 -*-
"""Единая точка входа: выражение или функция из файла — внутрь, отчёт — наружу.

Всё, что делает командная строка, делается через эти две функции. Тесты зовут их
же, а не CLI: иначе проверялось бы форматирование, а не анализ.
"""
from __future__ import annotations

import math

from pareto import budget as _budget
from pareto.analysis import (combined_bound, pareto_extract, refine_front,
                             tree_cost, tree_cost_refined)
from pareto.codegen import op_count, to_c, to_c_block, to_text
from pareto.egraph import EGraph
from pareto.mixed import (count_rounding, inner_rounding_free, output_round_op,
                          strip_rounding, tune, uniform_candidates, wrap)
from pareto.parser import parse, variables
from pareto.precision import has_narrow
from pareto.program import ProgramError, analyse_program
from pareto.rules import RULES

INF = float('inf')


# ---------- одно выражение ----------
def search_front(tree, dom, keep=10, iters=10, refine=True, out_round=None):
    """Фронт Парето для выражения. Возвращает список (стоимость, граница, дерево).

    Поиск идёт по ИДЕАЛЬНОМУ выражению — без узлов округления. Это не упрощение,
    а единственная корректная постановка: правила переписывания записаны для
    математики, а округление к узкому формату математикой не является, оно часть
    реализации. Формат результата навешивается обратно уже на найденные формы,
    потому что он контракт: функция объявлена возвращающей float, значит ответ
    обязан возвращать float.
    """
    ideal = strip_rounding(tree)
    if out_round is None:
        out_round = output_round_op(tree)

    eg = EGraph()
    root = eg.add_expr(ideal)
    # Поиску отдаём не весь остаток, а половину: извлечение фронта и пересчёт границ
    # тоже стоят времени, и если насыщение съест весь бюджет, отдавать будет нечего.
    left = _budget.remaining()
    eg.saturate(RULES, iters=iters, domain=dom,
                time_budget=max(0.1, min(20.0, left * 0.5))
                if math.isfinite(left) else None)
    front, _ = pareto_extract(eg, root, dom, keep=keep)
    if refine:
        front = refine_front(front, dom)

    if out_round is None:
        return [(p[0], p[1], p[2]) for p in front]

    # Округление результата меняет границу, поэтому её надо пересчитать, а не
    # донести прежнюю: у формы, которая была точнее всех в double, после
    # округления в float граница может сравняться с любой другой.
    out = []
    for p in front:
        cand = wrap(p[2], out_round)
        try:
            err = tree_cost_refined(cand, dom)[1] if refine else combined_bound(cand, dom)
            cost = tree_cost(cand, dom)[0]
        except (ValueError, ZeroDivisionError, OverflowError, KeyError):
            continue
        out.append((cost, err, cand))
    return out


def _non_dominated(points):
    out = []
    for cost, err, tree in sorted(points, key=lambda p: (p[0], p[1])):
        if any(o[0] <= cost and o[1] <= err and (o[0] < cost or o[1] < err) for o in out):
            continue
        out = [o for o in out if not (cost <= o[0] and err <= o[1])]
        out.append((cost, err, tree))
    return out


def analyse_expression(text_or_tree, dom, keep=10, iters=10, budget=2.0,
                       cost_budget=1.5, refine=True):
    """Разбор одного выражения: фронт, самая дешёвая и самая точная форма."""
    tree = parse(text_or_tree) if isinstance(text_or_tree, str) else text_or_tree
    front = search_front(tree, dom, keep=keep, iters=iters, refine=refine)

    # Исходная запись — тоже точка фронта: бывает, что лучше её ничего нет.
    try:
        base_cost = tree_cost(tree, dom)[0]
        base_err = tree_cost_refined(tree, dom)[1] if refine else combined_bound(tree, dom)
    except (ValueError, ZeroDivisionError, OverflowError, KeyError):
        base_cost, base_err = INF, INF
    front = _non_dominated(front + [(base_cost, base_err, tree)])
    if not front:
        front = [(base_cost, base_err, tree)]

    safe = [p for p in front if p[1] <= base_err * budget] if base_err > 0 else list(front)
    fastest = min(safe, key=lambda p: (p[0], p[1])) if safe else min(front, key=lambda p: p[1])
    afford = [p for p in front if p[0] <= base_cost * cost_budget] if base_cost > 0 else list(front)
    most_exact = (min(afford, key=lambda p: (p[1], p[0])) if afford
                  else min(front, key=lambda p: (p[1], p[0])))

    return {
        'input': to_text(tree),
        'base': {'cost': base_cost, 'err': base_err, 'ops': op_count(tree),
                 'c': to_c(tree)},
        'front': [{'cost': p[0], 'err': p[1], 'form': to_text(p[2]), 'c': to_c(p[2]),
                   'ops': op_count(p[2])} for p in front],
        'fastest': {'form': to_text(fastest[2]), 'c': to_c(fastest[2]),
                    'cost': fastest[0], 'err': fastest[1]},
        'most_exact': {'form': to_text(most_exact[2]), 'c': to_c(most_exact[2]),
                       'cost': most_exact[0], 'err': most_exact[1]},
        'cut': _budget.cut_stages(),
    }


# ---------- проверка требования ----------
# Коды возврата. Значения выбраны так, чтобы скрипт сборки мог различить три
# РАЗНЫХ исхода, а не только «получилось / не получилось»: между «код уже
# удовлетворяет требованию» и «требование недостижимо ни в какой записи» лежит
# третий случай, в котором есть готовое исправление, и он самый частый.
MET = 0            # как написано, граница уже не превышает требование
NEEDS_REWRITE = 1  # как написано — превышает, но найденная форма требование держит
UNPROVABLE = 2     # ни одна найденная форма требования не держит


def check_requirement(required, as_written, best, best_form=None):
    """Держит ли код требование по ошибке. Ответ — вердикт и код возврата.

    Требование проверяется по ДОКАЗАННОЙ верхней границе, а не по замеру на
    выборке входов: замер отвечает «на этих входах обошлось», а в сборке нужен
    ответ про все входы сразу.

    Бесконечная граница — это «доказать не удалось», а не «ошибка огромна», и
    она попадает в тот же исход, что и превышение: утверждать про такой код
    нечего.
    """
    verdict = UNPROVABLE
    if math.isfinite(as_written) and as_written <= required:
        verdict = MET
    elif math.isfinite(best) and best <= required:
        verdict = NEEDS_REWRITE
    return {
        'required': required,
        'as_written': as_written,
        'rewritten': best,
        'verdict': {MET: 'met', NEEDS_REWRITE: 'needs_rewrite',
                    UNPROVABLE: 'unprovable'}[verdict],
        'exit_code': verdict,
        'form': best_form,
    }


# ---------- точность ----------
def precision_report(tree, dom, target=None, formats=('float32', 'float16')):
    """Что известно про форматы: однородные варианты и подбор под цель.

    Однородные варианты отвечают на вопрос «влезет ли расчёт в float целиком».
    Подбор отвечает на более полезный: «какие подвыражения можно опустить до
    float так, чтобы граница осталась не хуже заданной». Второе и есть смешанная
    точность, и здесь она решается ДОКАЗАННОЙ границей, а не прогоном на выборке
    входов, как это делают инструменты подбора точности обычно.
    """
    out = {'uniform': [], 'tuned': None}
    for name, cand, err in uniform_candidates(tree, dom, formats=formats):
        out['uniform'].append({'format': name, 'err': err, 'form': to_text(cand),
                               'c': to_c(cand)})
    if target is not None:
        best, err, narrowed = tune(tree, dom, target, formats=[f for f in formats])
        out['tuned'] = {'target': target, 'err': err, 'narrowed': narrowed,
                        'rounding_nodes': count_rounding(best),
                        'form': to_text(best), 'c': to_c(best),
                        'meets': math.isfinite(err) and err <= target}
    return out


# ---------- функция из файла ----------
def analyse_c_function(src, name=None, dom=None, keep=8, iters=8, refine=True,
                       optimise=True, max_boxes=32):
    """Разбор функции на C: пути, границы, переписанные ветки.

    Каждый путь исполнения оптимизируется САМ: у ветвей разная арифметика и разные
    области достижимости, поэтому и лучшая запись у них разная. Так же поступает
    компилятор, когда специализирует код внутри ветки.
    """
    from pareto.cfront import parse_function

    fn = parse_function(src, name)
    domain = dict(fn['domains'])
    if dom:
        domain.update(dom)
    missing = [a for a in fn['order'] if a not in domain]
    if missing:
        raise ProgramError(
            'no range given for: {}. Add a comment next to the function, for example '
            '// @domain {}: 0 .. 1, or pass --domain {}=0..1'.format(
                ', '.join(missing), missing[0], missing[0]))
    domain = {k: v for k, v in domain.items() if k in set(fn['order'])}

    def optimiser(expr, box):
        front = search_front(expr, box, keep=keep, iters=iters, refine=refine)
        if not front:
            return expr, INF
        best = min(front, key=lambda p: (p[1], p[0]))
        return best[2], best[1]

    result = analyse_program(fn['stmts'], domain, max_boxes=max_boxes,
                             optimise=optimiser if optimise else None)
    result['function'] = fn['name']
    result['result_format'] = fn['result'].name
    result['args'] = {k: v.name for k, v in fn['args'].items()}
    result['order'] = fn['order']
    result['domain'] = domain
    result['cut'] = _budget.cut_stages()
    # Где эта функция лежит в тексте файла. Нужно тому, кто просит записать
    # переписанное тело обратно в исходник, а не прочитать его в консоли.
    result['body_span'] = fn['body_span']
    result['line'] = fn['line']
    for p in result['paths']:
        p['expr_text'] = to_text(p['expr'])
        p['form_text'] = to_text(p['form'])
        p['form_c'] = to_c(p['form'])
        p['guards_text'] = ['{} {} {}'.format(to_text(c[1]), c[0], to_text(c[2]))
                            + ('' if want else '   (false)')
                            for c, want in p['guards']]
    return result


def rewritten_c(result, indent='    '):
    """Переписанное тело функции на C — то, что человек вставит к себе.

    Печатается цепочкой `if`, по одной ветке на путь исполнения. Условия взяты из
    исходной программы дословно, поэтому поведение сохраняется; меняется только
    запись арифметики внутри ветки.

    Пути, недостижимые на объявленных диапазонах, печатаются ТОЖЕ, со своей
    исходной арифметикой. Иначе функция, вызванная за пределами объявленных
    диапазонов, тихо поменяла бы поведение — а диапазоны это обещание вызывающего,
    не свойство кода.
    """
    lines = []
    order = list(result['paths'])
    for n, p in enumerate(order):
        conds = []
        for c, want in p['guards']:
            text = '({}) {} ({})'.format(to_c(c[1]), c[0], to_c(c[2]))
            conds.append(text if want else '!({})'.format(text))
        last = (n == len(order) - 1)
        # Последний путь печатается без условия. Пути перечислены в том же порядке,
        # в каком стоят ветвления в исходнике, и покрывают всё, поэтому последний —
        # это ровно та ветка, куда управление доходит, если не сработало ничего выше.
        # Отрицание его условия было бы и лишним, и опасным: функция без возврата в
        # конце не соберётся.
        if conds and not last:
            lines.append('{}if ({}) {{'.format(indent, ' && '.join(conds)))
            lines.append(to_c_block(p['form'], indent=indent + indent))
            lines.append('{}}}'.format(indent))
        else:
            lines.append(to_c_block(p['form'], indent=indent))
    return '\n'.join(lines)
