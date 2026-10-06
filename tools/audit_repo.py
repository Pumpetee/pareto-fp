# -*- coding: utf-8 -*-
"""Прогон всех принятых функций чужого файла: где граница не доказывается.

Сканер отвечает на вопрос «сколько принимаем». Этот — на вопрос «и что из этого
следует». Для каждой принятой функции берётся один и тот же широкий диапазон на
все аргументы, и смотрится, удалось ли доказать конечную границу ошибки. Там, где
не удалось, значение может уйти в бесконечность или в ноль ещё до того, как
посчитается результат, — то есть функция молча вернёт не то.

Диапазон задаётся снаружи и печатается в отчёте: без него никакая граница не
имеет смысла, а подобранный «удобный» диапазон превращает отчёт в рекламу.

Запуск: python tools/audit_repo.py <файл.c|.h> --range -1e20..1e20
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pareto.api import analyse_c_function, safety_envelope, domain_hazards
from pareto.cfront import (CParseError, collect_context, functions,
                           constants, globals_of, macro_aliases,
                           make_resolver, parse_function)


def prog_tree(prog):
    """Выражение единственного пути функции — для поиска границы безопасности.

    Локальные переменные в нашей форме хранятся отдельными операторами `let` и
    подставляются внутрь: разворачиваем их здесь, чтобы получить одно выражение.
    Функция с ветвлениями сюда не годится — у каждой ветки своя огибающая, и
    усреднять их было бы обманом.
    """
    env, result = {}, None
    for st in prog['stmts']:
        if st[0] == 'let':
            env[st[1]] = st[2].tree if hasattr(st[2], 'tree') else st[2]
        elif st[0] == 'return':
            result = st[1]
        else:
            raise ValueError('функция с ветвлениями')
    if result is None:
        raise ValueError('нет возврата')

    def walk(node):
        if node[0] == 'var' and node[1] in env:
            return walk(env[node[1]])
        if node[0] in ('num', 'var'):
            return node
        return (node[0],) + tuple(walk(k) for k in node[1:])

    return walk(result)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('path')
    ap.add_argument('--range', default='-1e20..1e20',
                    help='диапазон по умолчанию для всех аргументов')
    ap.add_argument('--arg', action='append', default=[], metavar='ИМЯ=НИЗ..ВЕРХ',
                    help='диапазон для аргумента с таким именем; можно '
                         'повторять. Сопоставление по имени параметра, '
                         'а не по порядку')

    args = ap.parse_args()

    lo, hi = (float(x) for x in args.range.split('..'))
    # Диапазон по имени параметра. Без этого обход живого проекта упирается в
    # ложные тревоги: даёшь ±1e3 всем аргументам подряд — и получаешь честное
    # «делитель накрывает ноль» на выражении 1 + 2*z*w + w*w, которое при реальных
    # hertz > 0 и timeStep > 0 меньше единицы не бывает. Предупреждение верное,
    # но про код, которого не существует. Диапазоны должен задавать тот, кто знает
    # вызывающую сторону, а инструмент обязан дать такую возможность.
    per_arg = {}
    for item in args.arg:
        if '=' not in item or '..' not in item:
            raise SystemExit(f'не разобрал --arg {item!r}, нужно ИМЯ=НИЗ..ВЕРХ')
        nm, rng = item.split('=', 1)
        a_lo, a_hi = (float(x) for x in rng.split('..'))
        if a_lo > a_hi:
            raise SystemExit(f'у {nm} низ больше верха')
        per_arg[nm.strip()] = (a_lo, a_hi)
    target = Path(args.path)
    src = target.read_text(encoding='utf-8', errors='replace')
    root = target.parent
    for _ in range(3):                 # поднимаемся до корня проекта за заголовками
        if (root.parent / 'include').exists() or (root.parent / 'src').exists():
            root = root.parent
    siblings = [p for p in root.rglob('*') if p.suffix in ('.c', '.h')]
    pool = siblings or [target]
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
    resolve = make_resolver(texts, types, table, macros=macros, consts=consts, globs=globs)
    ctx = {'types': types, 'table': table, 'resolve': resolve, 'macros': macros,
           'consts': consts, 'globs': globs}

    rows, hazards = [], []
    for name in functions(src, types):
        try:
            prog = parse_function(src, name, types, table, resolve, macros, consts, globs)
        except Exception:
            continue
        dom = {a: per_arg.get(a, (lo, hi)) for a in prog['args']}
        try:
            res = analyse_c_function(src, name, dom=dom, ctx=ctx)
        except Exception as e:
            rows.append((name, None, None, f'{type(e).__name__}', None))
            continue
        written = res.get('base_bound')
        best = res.get('best_bound')
        paths = res.get('paths') or []
        form = ''
        for pth in paths:
            f = pth.get('best_form') or pth.get('form') or ''
            if f:
                form = f
                break
        try:
            for h in domain_hazards(prog_tree(prog), dom):
                hazards.append((name, h))
        except Exception:
            pass
        env = None
        if written is None or not math.isfinite(written or math.inf):
            try:
                k, _full = safety_envelope(prog_tree(prog), dom)
                env = k * max(abs(lo), abs(hi))
            except Exception:
                env = None
        rows.append((name, written, best, form, env))

    print(f'файл: {args.path}')
    print(f'диапазон для КАЖДОГО аргумента: [{lo:g}, {hi:g}]')
    for nm in sorted(per_arg):
        print(f'  кроме {nm}: [{per_arg[nm][0]:g}, {per_arg[nm][1]:g}]')
    print()
    bad = [r for r in rows if r[1] is None or not math.isfinite(r[1] or math.inf)]
    print(f'принятых функций: {len(rows)} | граница НЕ доказана как написано: {len(bad)}')
    print()
    if hazards:
        print('МОЖЕТ ВЕРНУТЬ НЕ ЧИСЛО:')
        for name, h in hazards:
            print(f'  {name}: {h["op"]} от {h["argument"]}')
            print(f'    диапазон [{h["range"][0]:.3e}, {h["range"][1]:.3e}] — {h["why"]}')
        print()
    print(f'{"функция":<24}{"как написано":>14}{"переписано":>14}{"безопасно до":>14}')
    for name, w, b, form, env in rows:
        ws = 'не доказана' if (w is None or not math.isfinite(w)) else f'{w:.3e}'
        bs = '—' if (b is None or not math.isfinite(b)) else f'{b:.3e}'
        es = '—' if not env else f'{env:.2e}'
        print(f'{name:<24}{ws:>14}{bs:>14}{es:>14}')


if __name__ == '__main__':
    main()
