# -*- coding: utf-8 -*-
"""Найти ВХОД, на котором написанная форма врёт, а переписанная нет.

«Доказанная граница стала туже в 8145 раз» — утверждение верное и неубедительное.
Человек слышит в нём оценку сверху, то есть разговор о том, чего может не быть.
Убеждает другое: вот эти числа, подайте их в свою функцию, вот её ответ, вот
правильный, вот сколько цифр потеряно. Это проверяется за минуту и не требует
верить ни одному нашему слову.

Поэтому здесь не сравниваются границы. Берётся написанная форма и предложенная,
обе исполняются как машина, обе сверяются с эталоном на Decimal, и ищется точка,
где написанная теряет больше всего верных цифр.

Запуск:
  python tools/witness.py <файл.c|.h> --function ИМЯ [--range -1e3..1e3]
  python tools/witness.py <корень проекта> --all
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pareto.api import analyse_c_function
from pareto.cfront import (collect_context, constants, functions, globals_of,
                           macro_aliases, make_resolver, parse_function)
from pareto.codegen import to_c
from pareto.evalfp import eval_float
from pareto.exactref import exact_stable
from pareto.parser import parse as parse_expr

SKIP_DIRS = {'.git', 'build', 'cmake', 'tests', 'test', 'examples', 'example',
             'demo', 'demos', 'third_party', 'external', 'extern', 'vendor',
             'docs', 'doc', 'benchmark', 'benchmarks', 'samples'}


# Сколько верных десятичных цифр формат вообще способен держать. Выше этого
# никакая запись не поднимется, и писать «потеряно 7.9 цифр» про ответ, точный
# настолько, насколько float32 позволяет, было бы клеветой на правильную форму.
FORMAT_DIGITS = {4: 7.2, 8: 15.9}


def correct_digits(got, ref):
    """Сколько верных десятичных цифр в ответе.

    Ноль значит ни одной: код вернул NaN, бесконечность или ноль там, где
    настоящее значение не ноль. Больше — лучше.

    Это считается от НАСТОЯЩЕГО значения, а не от идеального ответа в том же
    формате, и потому упирается в точность формата: у float32 примерно 7.2 цифры.
    Сравнивать надо две записи между собой, а не каждую с бесконечной точностью.
    """
    if got != got or not math.isfinite(got):
        return 0.0
    if ref == 0:
        return math.inf if got == 0.0 else 0.0
    if got == 0.0:
        return 0.0
    rel = abs((Decimal(got) - ref) / ref)
    if rel == 0:
        return math.inf
    try:
        return max(0.0, -math.log10(float(rel)))
    except (ValueError, OverflowError):
        return 0.0


def hunt(written, rewritten, dom, cases, rng):
    """Точка, где написанная форма теряет больше всего цифр против переписанной.

    Точки берутся не только случайные: крайние значения диапазона и величины,
    прижатые к его концам, нарочно проверяются отдельно. Болезнь переполнения
    промежуточного квадрата живёт именно там, и случайная выборка по середине
    диапазона прошла бы мимо неё.
    """
    names = sorted(dom)
    best = None
    probes = []
    for v in (1.0, 0.5, 1e-3, 1e-6, 1e-9, 1e-12):
        probes.append({n: dom[n][1] * v for n in names})
        probes.append({n: dom[n][0] * v for n in names})
    probes.append({n: dom[n][1] for n in names})
    probes.append({n: dom[n][0] for n in names})
    for _ in range(cases):
        probes.append({n: rng.uniform(*dom[n]) for n in names})

    for pt in probes:
        try:
            a = eval_float(written, pt)
            b = eval_float(rewritten, pt)
        except Exception:
            continue
        ref = exact_stable(written, {k: Decimal(v) for k, v in pt.items()})
        if ref is None:
            continue                         # эталон сам себе не доверяет
        ca, cb = correct_digits(a, ref), correct_digits(b, ref)
        if ca >= cb:
            continue                         # написанная не хуже — не свидетель
        gain = cb - ca if math.isfinite(cb) else math.inf
        if best is None or gain > best[0]:
            best = (gain, pt, a, b, ref, ca, cb)
    return best


def kind_of(found):
    """Класс свидетеля. Разводить их обязательно, иначе счёт находок — подлог.

    «Катастрофа» — написанная форма не даёт НИ ОДНОЙ верной цифры: вернула
    бесконечность, NaN или ноль там, где настоящее значение далеко от нуля. Это
    дефект: ответа просто нет.

    «Точнее» — обе формы дают числа, одна ближе. На cpvdot это 15.8 цифры против
    17.7, то есть разница в одну младшую единицу. Полезно, но дефектом не
    является, и ставить такое в один список с бесконечностью нельзя.
    """
    ca = found[5]
    return 'катастрофа' if ca == 0.0 else 'точнее'


def report(name, written, rewritten, found, width):
    gain, pt, a, b, ref, ca, cb = found
    limit = FORMAT_DIGITS.get(width, 15.9)
    print('### `{}`'.format(name))
    print()
    print('Вход:', ', '.join('`{} = {!r}`'.format(k, v) for k, v in sorted(pt.items())))
    print()
    fmt = lambda d: 'ни одной' if d == 0 else ('точно' if d == math.inf
                                               else '{:.1f}'.format(d))
    print('| | ответ | верных цифр |')
    print('|---|---|---:|')
    print('| как написано | `{!r}` | {} |'.format(a, fmt(ca)))
    print('| после переписи | `{!r}` | {} |'.format(b, fmt(cb)))
    print('| настоящее значение | `{}` | |'.format(
        ('{:.6e}'.format(float(ref)) if abs(ref) < Decimal('1e300')
         else str(ref)[:24])))
    print()
    print('Предложенная форма: `{}`'.format(to_c(rewritten)))
    print()
    print('Для справки: типичный предел формата здесь около {:.1f} верных цифр. '
          'Отдельная точка может оказаться и точнее — важно не абсолютное '
          'число, а разница между двумя записями.'.format(limit))
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('path')
    ap.add_argument('--function')
    ap.add_argument('--all', action='store_true',
                    help='обойти весь проект и показать всех найденных свидетелей')
    ap.add_argument('--range', default='-1e3..1e3')
    ap.add_argument('--cases', type=int, default=400)
    ap.add_argument('--seed', type=int, default=20261006)
    ap.add_argument('--min-digits', type=float, default=1.0,
                    help='сколько цифр должна терять написанная форма, чтобы счесть это находкой')
    a = ap.parse_args()

    lo, hi = (float(x) for x in a.range.split('..'))
    rng = random.Random(a.seed)
    given = Path(a.path)
    root = given if given.is_dir() else given.parent
    for _ in range(3):
        if not given.is_dir() and (
                (root.parent / 'include').exists() or (root.parent / 'src').exists()):
            root = root.parent
    pool = [p for p in root.rglob('*')
            if p.suffix in ('.c', '.h')
            and not any(part.lower() in SKIP_DIRS for part in p.parts)]
    types, table = collect_context(pool)
    texts = []
    for f in pool:
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

    targets = pool if given.is_dir() else [given]
    print('# Свидетели: входы, на которых написанная форма теряет цифры')
    print()
    print('Диапазон для каждого аргумента: `[{:g}, {:g}]`. Ответы ниже получены '
          'исполнением формы ровно так, как её исполнит машина, и сверены с '
          'эталоном на Decimal.'.format(lo, hi))
    print()

    hard, soft = [], []
    for target in targets:
        try:
            src = target.read_text(encoding='utf-8', errors='replace')
            names = functions(src, types)
        except Exception:
            continue
        if a.function:
            names = [n for n in names if n == a.function]
        for name in names:
            try:
                prog = parse_function(src, name, types, table, resolve, macros,
                                      consts, globs)
            except Exception:
                continue
            if len(prog['stmts']) and any(st[0] not in ('let', 'return')
                                          for st in prog['stmts']):
                continue
            dom = {arg: (lo, hi) for arg in prog['args']}
            try:
                res = analyse_c_function(src, name, dom=dom, ctx=ctx)
            except Exception:
                continue
            paths = res.get('paths') or []
            if len(paths) != 1:
                continue
            w, r = paths[0].get('expr'), paths[0].get('form')
            if w is None or r is None or w == r:
                continue
            got = hunt(w, r, dom, a.cases, rng)
            if got is None or got[0] < a.min_digits:
                continue
            # Разрядность берём из объявленного типа возврата: у raylib и box2d
            # это float32, у Chipmunk — double, и справочный предел цифр у них
            # разный. Ставить четвёрку всем было бы небрежностью ровно того рода,
            # за которую я уже ловил себя в этом же файле.
            width = 8 if getattr(prog.get('result'), 'mant_bits', 24) > 24 else 4
            (hard if kind_of(got) == 'катастрофа' else soft).append(
                (name, w, r, got, width))
    if not hard and not soft:
        print('Свидетелей не найдено: на этих диапазонах написанные формы не '
              'теряют цифр против предложенных. Это тоже результат — переписывать '
              'тут нечего.')
        return 0

    print('Найдено: **{}** функций, где написанная форма не даёт ни одной верной '
          'цифры, и {} — где предложенная просто точнее.'.format(len(hard), len(soft)))
    print()
    if hard:
        print('## Ответа нет вовсе')
        print()
        print('Здесь код возвращает бесконечность, NaN или ноль при том, что '
              'настоящее значение в формат укладывается. Это дефект, а не '
              'потеря точности.')
        print()
        for name, w, r, got, width in hard:
            report(name, w, r, got, width)
    if soft:
        print('## Просто точнее')
        print()
        print('Здесь обе записи дают числа, предложенная ближе к настоящему '
              'значению. Полезно, но дефектом не является — и в один список с '
              'предыдущим разделом не ставится.')
        print()
        for name, w, r, got, width in soft:
            report(name, w, r, got, width)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
