# -*- coding: utf-8 -*-
"""Дело-папка по чужому проекту: что принято, что доказано, что сломано.

Документ, который можно показать, должен считаться, а не печататься руками.
Руками написанное число живёт своей жизнью: его переписывают в письмо, в письме
оно устаревает, и в разговоре выясняется, что подтвердить его нечем. Поэтому
здесь нет ни одной цифры, взятой из головы — всё, что попадает в вывод,
посчитано этим же запуском, и команда для повтора печатается рядом.

Запуск:
  python tools/case_report.py <корень проекта> --range -1e3..1e3 > case.md
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

sys.path.insert(0, str(Path(__file__).resolve().parent))

from arith_coverage import INT_TYPES, is_float_candidate   # noqa: E402

from pareto import budget as _budget
from pareto.api import analyse_c_function, domain_hazards
from pareto.cfront import (CParseError, collect_context, constants, functions,
                           globals_of, macro_aliases, make_resolver,
                           parse_function, struct_result_fields)

ARITH = {'+', '-', '*', '/', 'fma', 'sqrt', 'exp', 'log', 'expm1', 'log1p',
         'sin', 'cos', 'atan', 'atan2', 'hypot', 'neg'}

SKIP_DIRS = {'.git', 'build', 'cmake', 'tests', 'test', 'examples', 'example',
             'demo', 'demos', 'third_party', 'external', 'extern', 'vendor',
             'docs', 'doc', 'benchmark', 'benchmarks', 'samples'}


def counts_arithmetic(prog):
    """Есть ли в функции хоть одно вычисление, или это чтение поля."""
    def walk(node):
        if not isinstance(node, tuple):
            return False
        if node[0] in ARITH:
            return True
        return any(walk(k) for k in node[1:])

    for st in prog['stmts']:
        if st[0] == 'let':
            t = st[2].tree if hasattr(st[2], 'tree') else st[2]
            if walk(t):
                return True
        elif st[0] == 'return' and walk(st[1]):
            return True
        elif st[0] not in ('let', 'return'):
            return True
    return False


def single_expr(prog):
    """Выражение единственного пути — для поиска опасных мест."""
    env, result = {}, None
    for st in prog['stmts']:
        if st[0] == 'let':
            env[st[1]] = st[2].tree if hasattr(st[2], 'tree') else st[2]
        elif st[0] == 'return':
            result = st[1]
        else:
            return None
    if result is None:
        return None

    def walk(node):
        if node[0] == 'var' and node[1] in env:
            return walk(env[node[1]])
        if node[0] in ('num', 'var'):
            return node
        return (node[0],) + tuple(walk(k) for k in node[1:])

    return walk(result)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('repo')
    ap.add_argument('--range', default='-1e3..1e3')
    ap.add_argument('--budget', type=float, default=5.0, metavar='СЕКУНД',
                    help='предел времени на одну функцию; 0 — без предела. '
                         'Обход проекта должен занимать предсказуемое время: на '
                         'raylib четыре функции из трёхсот съедали 157 секунд из '
                         'трёхсот, и человек, запустивший инструмент на своём '
                         'коде, видел только висящий терминал. Обрезанные этапы '
                         'называются в выводе, а не скрываются.')
    ap.add_argument('--top', type=int, default=12,
                    help='сколько самых крупных улучшений показать')
    a = ap.parse_args()

    lo, hi = (float(x) for x in a.range.split('..'))
    root = Path(a.repo)
    files = [p for p in root.rglob('*')
             if p.suffix in ('.c', '.h')
             and not any(part.lower() in SKIP_DIRS for part in p.parts)]
    types, table = collect_context(files)
    texts = []
    for f in files:
        try:
            texts.append(f.read_text(encoding='utf-8', errors='replace'))
        except OSError:
            pass
    macros = macro_aliases(texts)
    consts = constants(texts, types)
    globs = globals_of(texts, types, table)
    resolve = make_resolver(texts, types, table, macros=macros, consts=consts,
                            globs=globs)
    ctx = {'types': types, 'table': table, 'resolve': resolve, 'macros': macros,
           'consts': consts, 'globs': globs}
    float_types = set(types) | {'float', 'double'}
    type_names = set(types) | set(table) | INT_TYPES | {'float', 'double'}

    seen = accepted = trivial = candidates = taken = 0
    proved = unproved = 0
    rows, hazards, refusals = [], [], {}
    cut_any = set()

    for f in files:
        try:
            src = f.read_text(encoding='utf-8', errors='replace')
            # table обязателен: без него функции со структурным возвратом
            # невидимы, и охват считается по урезанной вселенной.
            names = functions(src, types, table)
        except Exception:
            continue
        for name in names:
            seen += 1
            # Та же мера кандидата, что в arith_coverage: два документа про один
            # проект обязаны называть одно число. Разные знаменатели в разговоре
            # о покупке читаются как путаница в своих же данных.
            is_cand, _ = is_float_candidate(src, name, float_types, type_names)
            if is_cand:
                candidates += 1
            flds = struct_result_fields(src, name, types, table) or [None]
            try:
                prog = None
                for _f in flds:
                    prog = parse_function(src, name, types, table, resolve,
                                          macros, consts, globs, field=_f)
            except CParseError as e:
                key = str(e).split(':')[-1].strip()[:50]
                refusals[key] = refusals.get(key, 0) + 1
                continue
            except Exception as e:
                refusals[type(e).__name__] = refusals.get(type(e).__name__, 0) + 1
                continue
            accepted += 1
            if is_cand:
                taken += 1
            if not counts_arithmetic(prog):
                trivial += 1
                continue
            dom = {arg: (lo, hi) for arg in prog['args']}
            _budget.set_budget(a.budget)
            try:
                res = analyse_c_function(src, name, dom=dom, ctx=ctx,
                                         field=flds[0])
            except Exception:
                continue
            finally:
                cut_any.update(_budget.cut_stages())
                _budget.clear()
            base, best = res.get('base_bound'), res.get('best_bound')
            if base is None or not math.isfinite(base):
                unproved += 1
            else:
                proved += 1
            gain = None
            if (base and best and math.isfinite(base) and math.isfinite(best)
                    and best > 0):
                gain = base / best
            label = name if flds[0] is None else '{}.{}'.format(name, flds[0])
            rows.append((label, f.relative_to(root), base, best, gain))
            tree = single_expr(prog)
            if tree is not None:
                try:
                    for h in domain_hazards(tree, dom):
                        hazards.append((name, h))
                except Exception:
                    pass

    real = accepted - trivial
    pct = lambda n: '{:.1f}%'.format(100.0 * n / seen) if seen else '—'

    print('# Прогон pareto-fp по проекту `{}`'.format(root.name))
    print()
    print('Диапазон для каждого аргумента: `[{:g}, {:g}]`. Все числа ниже '
          'посчитаны этим запуском.'.format(lo, hi))
    print()
    print('Повторить:')
    print()
    print('```')
    print('python tools/scan_repo.py {}'.format(root))
    print('python tools/case_report.py {} --range {}'.format(root, a.range))
    print('python tools/difftest_c.py {}   # сверка с clang побитово'.format(root))
    print('```')
    print()
    print('## Охват')
    print()
    print('| | функций | доля |')
    print('|---|---:|---:|')
    print('| всего найдено | {} | |'.format(seen))
    print('| принято фронтендом | {} | {} |'.format(accepted, pct(accepted)))
    print('| **из них с вычислениями** | **{}** | **{}** |'.format(real, pct(real)))
    print('| чтение поля без вычислений | {} | {} |'.format(trivial, pct(trivial)))
    print()
    print('Вторая строка — та, которую обычно показывают. Третья — честная: '
          'функция вида `return body->m` проходит фронтенд, но доказывать в ней '
          'нечего.')
    print()
    print('Главное число — доля от КАНДИДАТОВ, то есть от функций, где по тексту '
          'исходника есть вещественная арифметика. Остальные в знаменатель '
          'ставить нельзя: в `void`-процедуре или целочисленном счётчике '
          'доказывать нечего, и держать их там значило бы назначить себе цель, '
          'которой достичь невозможно.')
    print()
    print('**Принято из кандидатов: {} из {}'.format(taken, candidates)
          + (' — {:.1f}%**'.format(100.0 * taken / candidates) if candidates
             else '**'))
    print()
    print('## Границы')
    print()
    if cut_any:
        print('Предел времени на функцию: {:g} с. Из-за него на части функций '
              'поиск оборван, и граница ниже могла быть туже: {}. Снимается '
              'ключом `--budget 0`.'.format(a.budget, ', '.join(sorted(cut_any))))
        print()
    print('Граница доказана: **{}**, не доказана: **{}**.'.format(proved, unproved))
    print()
    if rows:
        ranked = sorted((r for r in rows if r[4]), key=lambda r: -r[4])[:a.top]
        if ranked:
            print('Где перепись даёт больше всего:')
            print()
            print('| функция | как написано | после переписи | туже в |')
            print('|---|---:|---:|---:|')
            for name, rel, base, best, gain in ranked:
                print('| `{}` | {:.3e} | {:.3e} | {:.0f}× |'.format(
                    name, base, best, gain))
            print()
    if hazards:
        print('## Может вернуть не число')
        print()
        print('На этих диапазонах код отдаёт NaN или бесконечность. Это не про '
              'точность, а про то, вернётся ли вообще число.')
        print()
        seen_h = set()
        for name, h in hazards:
            key = (name, h['op'], h['argument'])
            if key in seen_h:
                continue
            seen_h.add(key)
            print('- `{}`: `{}` от `{}` на `[{:.2e}, {:.2e}]` — {}'.format(
                name, h['op'], h['argument'][:70], h['range'][0], h['range'][1],
                h['why']))
        print()
    if refusals:
        print('## На чём фронтенд отказывает')
        print()
        print('| причина | функций |')
        print('|---|---:|')
        for reason, n in sorted(refusals.items(), key=lambda kv: -kv[1])[:10]:
            print('| {} | {} |'.format(reason, n))
        print()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
