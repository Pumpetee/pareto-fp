# -*- coding: utf-8 -*-
"""Точка входа для человека со своей формулой.

    python -m pareto.cli "sqrt(x+1) - sqrt(x)" --domain x=1e6..1e9
    python -m pareto.cli "x*x - y*y" --domain x=1..2 --domain y=1..2 --json

Ничего, кроме питона, не нужно: ни node, ни clang. Компилятор подключается
только в замерах (`pareto/run_fairbench.py`), здесь считается модель.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pareto.analysis import pareto_extract, tree_cost
from pareto.codegen import op_count, to_c, to_text
from pareto.egraph import EGraph
from pareto.parser import ParseError, parse, variables
from pareto.rules import RULES


def parse_domain(items, vars_):
    """`x=1..2` или `x=1e6..1e9`. Без диапазонов границу ошибки посчитать нельзя."""
    dom = {}
    for it in items or []:
        if '=' not in it or '..' not in it:
            raise SystemExit('диапазон пишется как x=1..2, встречено: ' + it)
        name, rng = it.split('=', 1)
        lo, hi = rng.split('..', 1)
        try:
            dom[name.strip()] = (float(lo), float(hi))
        except ValueError:
            raise SystemExit('границы диапазона должны быть числами: ' + it)
    missing = [v for v in vars_ if v not in dom]
    if missing:
        raise SystemExit('не задан диапазон для: {}. Пример: --domain {}=1..2'.format(
            ', '.join(missing), missing[0]))
    return dom


def analyse(text, dom, keep=10, iters=10, budget=2.0, cost_budget=1.5):
    tree = parse(text)
    eg = EGraph()
    root = eg.add_expr(tree)
    eg.saturate(RULES, iters=iters)
    front, _ = pareto_extract(eg, root, dom, keep=keep)
    base_cost, base_err, _, _, _ = tree_cost(tree, dom)

    safe = [p for p in front if p[1] <= base_err * budget] if base_err > 0 else list(front)
    fastest = min(safe, key=lambda p: (p[0], p[1])) if safe else min(front, key=lambda p: p[1])
    afford = [p for p in front if p[0] <= base_cost * cost_budget] if base_cost > 0 else list(front)
    most_exact = (min(afford, key=lambda p: (p[1], p[0])) if afford
                  else min(front, key=lambda p: (p[1], p[0])))

    return {
        'input': to_text(tree),
        'base': {'cost': base_cost, 'err': base_err, 'ops': op_count(tree), 'c': to_c(tree)},
        'front': [{'cost': p[0], 'err': p[1], 'form': to_text(p[2]), 'c': to_c(p[2]),
                   'ops': op_count(p[2])} for p in front],
        'fastest': {'form': to_text(fastest[2]), 'c': to_c(fastest[2]),
                    'cost': fastest[0], 'err': fastest[1]},
        'most_exact': {'form': to_text(most_exact[2]), 'c': to_c(most_exact[2]),
                       'cost': most_exact[0], 'err': most_exact[1]},
    }


def render(r):
    b = r['base']
    out = ['входная формула : ' + r['input'],
           'стоимость модели: {:.1f} · граница ошибки: {:.3e}'.format(b['cost'], b['err']), '',
           'фронт Парето (все недоминируемые формы):',
           '{:>9} {:>12}  {}'.format('стоимость', 'ошибка', 'форма')]
    for p in r['front']:
        out.append('{:>9.1f} {:>12.3e}  {}'.format(p['cost'], p['err'], p['form']))
    out += ['']
    fa, ex = r['fastest'], r['most_exact']
    speed = (b['cost'] / fa['cost']) if fa['cost'] else float('inf')
    out.append('быстрее всего   : {}'.format(fa['form']))
    out.append('                  дешевле в {:.2f}x, граница ошибки {:.3e}'.format(speed, fa['err']))
    out.append('точнее всего    : {}'.format(ex['form']))
    if b['err'] > 0 and ex['err'] > 0:
        out.append('                  ошибка меньше в {:.3g}x, цена {:.2f}x'.format(
            b['err'] / ex['err'], (ex['cost'] / b['cost']) if b['cost'] else 1.0))
    elif ex['err'] == 0:
        out.append('                  ошибка модели ноль, цена {:.2f}x'.format(
            (ex['cost'] / b['cost']) if b['cost'] else 1.0))
    out += ['', 'вставить в код  : ' + fa['c']]
    return '\n'.join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='pareto',
        description='Переписывает числовую формулу в эквивалентную: быстрее или точнее, с границей ошибки.')
    ap.add_argument('expr', help='формула, например "sqrt(x+1) - sqrt(x)"')
    ap.add_argument('--domain', action='append', metavar='x=1..2',
                    help='диапазон переменной, можно повторять для каждой')
    ap.add_argument('--budget', type=float, default=2.0,
                    help='во сколько раз допустимо ухудшить точность ради скорости (по умолчанию 2)')
    ap.add_argument('--cost-budget', type=float, default=1.5,
                    help='во сколько раз допустимо удорожание ради точности (по умолчанию 1.5)')
    ap.add_argument('--keep', type=int, default=10, help='сколько точек фронта показывать')
    ap.add_argument('--iters', type=int, default=10, help='итераций насыщения e-графа')
    ap.add_argument('--json', action='store_true', help='выдать результат машинно')
    a = ap.parse_args(argv)

    try:
        tree = parse(a.expr)
    except ParseError as e:
        raise SystemExit('формулу не разобрал: {}'.format(e))
    dom = parse_domain(a.domain, variables(tree))

    r = analyse(a.expr, dom, keep=a.keep, iters=a.iters,
                budget=a.budget, cost_budget=a.cost_budget)
    print(json.dumps(r, ensure_ascii=False, indent=2) if a.json else render(r))
    return 0


if __name__ == '__main__':
    sys.exit(main())
