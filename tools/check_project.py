# -*- coding: utf-8 -*-
"""Проверить ВЕСЬ проект против требования к точности. Для сборки, не для чтения.

Одна функция в терминале — это демонстрация. В работе нужен другой вопрос: «во
всём моём коде ошибка не выходит за такую-то величину — да или нет?» И ответ
нужен кодом возврата, чтобы сборка падала сама, без человека, читающего вывод.

Коды возврата:
  0  — требование держится у всех проверенных функций
  1  — у части не держится как написано, но держится после переписи
  2  — у части не держится ни в одной найденной форме
  64 — ошибка в аргументах

Разница между 1 и 2 важна: в первом случае инструмент уже знает, что написать
вместо, и это задача на день; во втором ограничение недостижимо на этих
диапазонах, и разговаривать надо про диапазоны или про формат.

Что НЕ проверено, печатается тоже. Молчание про непроверенное — худший вид
отчёта: читается как «всё чисто».

Запуск:
  python tools/check_project.py <корень> --require 1e-6 --range=-1e3..1e3
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from arith_coverage import SKIP_DIRS, candidate_set   # noqa: E402

from pareto import budget as _budget
from pareto.api import analyse_c_function
from pareto.cfront import (collect_context, constants, functions, globals_of,
                           macro_aliases, make_resolver, parse_function,
                           struct_result_fields)
from pareto.codegen import to_c

# Список исключаемых каталогов один на все инструменты — см. arith_coverage.


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('repo')
    ap.add_argument('--require', type=float, required=True, metavar='BOUND',
                    help='предел абсолютной ошибки, который обязан держаться')
    ap.add_argument('--range', default='-1e3..1e3')
    ap.add_argument('--arg', action='append', default=[], metavar='ИМЯ=НИЗ..ВЕРХ',
                    help='диапазон для аргумента с таким именем; можно повторять')
    ap.add_argument('--budget', type=float, default=5.0, metavar='СЕКУНД',
                    help='предел времени на функцию; 0 — без предела')
    ap.add_argument('--quiet', action='store_true',
                    help='печатать только нарушителей и итог')
    a = ap.parse_args()

    try:
        lo, hi = (float(x) for x in a.range.split('..'))
    except ValueError:
        raise SystemExit(64)
    per_arg = {}
    for item in a.arg:
        if '=' not in item or '..' not in item:
            print('не разобрал --arg {!r}, нужно ИМЯ=НИЗ..ВЕРХ'.format(item))
            raise SystemExit(64)
        nm, rng = item.split('=', 1)
        a_lo, a_hi = (float(x) for x in rng.split('..'))
        per_arg[nm.strip()] = (a_lo, a_hi)

    root = Path(a.repo)
    files = [p for p in root.rglob('*')
             if p.suffix in ('.c', '.h')
             and not any(part.lower() in SKIP_DIRS for part in p.parts)]
    # Сколько файлов выброшено по ИМЕНИ каталога — обязательно вслух. Каталог с
    # именем test или demo исключается как чужой вспомогательный код, но если
    # чей-то проект целиком лежит в папке с таким именем, он получит пустой
    # отчёт. Молчаливый ноль читается как «всё чисто», а это худший вид ответа.
    _all = [p for p in root.rglob('*') if p.suffix in ('.c', '.h')]
    _skipped = len(_all) - len(files)
    if _skipped:
        print('пропущено файлов по имени каталога ({}): {} из {}'.format(
            ', '.join(sorted(SKIP_DIRS))[:60] + '...', _skipped, len(_all)))
        if not files:
            print('ПРОВЕРЯТЬ НЕЧЕГО: все файлы отброшены по имени каталога. '
                  'Если ваш код лежит в папке с таким именем, укажите на '
                  'подкаталог с исходниками напрямую.')

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
    arith_names = candidate_set(files, types, table, macros)

    checked = 0
    candidates = 0
    unreadable = 0
    holds_as_written = []
    needs_rewrite = []
    impossible = []

    for f in files:
        try:
            src = f.read_text(encoding='utf-8', errors='replace')
            names = functions(src, types, table)
        except Exception:
            continue
        for name in names:
            is_cand = name in arith_names
            if not is_cand:
                continue
            candidates += 1
            for fld in (struct_result_fields(src, name, types, table) or [None]):
                label = name if fld is None else '{}.{}'.format(name, fld)
                try:
                    prog = parse_function(src, name, types, table, resolve,
                                          macros, consts, globs, field=fld)
                except Exception:
                    unreadable += 1
                    break
                dom = {arg: per_arg.get(arg, (lo, hi)) for arg in prog['args']}
                # Сначала ДЕШЁВАЯ проверка: граница для кода как он написан, без
                # поиска переписи. Если требование уже держится, искать лучшую
                # форму не нужно вовсе — а именно поиск и стоит почти всё время.
                # Пока этого не было, мягкая проверка raylib не укладывалась и в
                # десять минут, хотя ответ на неё «всё держится».
                _budget.set_budget(a.budget)
                try:
                    res = analyse_c_function(src, name, dom=dom, ctx=ctx,
                                             field=fld, optimise=False)
                except Exception:
                    unreadable += 1
                    _budget.clear()
                    continue
                finally:
                    _budget.clear()
                base_quick = res.get('base_bound')
                if not (base_quick is not None and math.isfinite(base_quick)
                        and base_quick <= a.require):
                    # Не прошло как написано — вот теперь ищем форму.
                    _budget.set_budget(a.budget)
                    try:
                        res = analyse_c_function(src, name, dom=dom, ctx=ctx,
                                                 field=fld)
                    except Exception:
                        unreadable += 1
                        continue
                    finally:
                        _budget.clear()
                checked += 1
                base = res.get('base_bound')
                best = res.get('best_bound')
                rel = f.relative_to(root)
                if base is not None and math.isfinite(base) and base <= a.require:
                    holds_as_written.append((label, rel, base))
                elif best is not None and math.isfinite(best) and best <= a.require:
                    form = ''
                    try:
                        form = to_c(res['paths'][0]['form'])
                    except Exception:
                        form = ''
                    needs_rewrite.append((label, rel, base, best, form))
                else:
                    impossible.append((label, rel, base, best))

    print('проект: {}'.format(root))
    print('требование: ошибка не больше {:.3e}'.format(a.require))
    print('диапазон по умолчанию: [{:g}, {:g}]'.format(lo, hi))
    for nm in sorted(per_arg):
        print('  кроме {}: [{:g}, {:g}]'.format(nm, *per_arg[nm]))
    print()
    print('проверено выходов: {}'.format(checked))
    print('  держится как написано:        {}'.format(len(holds_as_written)))
    print('  держится после переписи:      {}'.format(len(needs_rewrite)))
    print('  не держится ни в одной форме: {}'.format(len(impossible)))
    print('прочитать не удалось: {} из {} кандидатов'.format(unreadable, candidates))
    print()

    if needs_rewrite and not a.quiet:
        print('ТРЕБУЕТ ПЕРЕПИСИ (инструмент знает, что написать вместо):')
        for label, rel, base, best, form in needs_rewrite:
            print('  {} ({})'.format(label, rel))
            print('    как написано {:.3e}, после переписи {:.3e}'.format(
                base if base is not None and math.isfinite(base) else float('inf'),
                best))
            if form:
                print('    вместо: {}'.format(form[:150]))
        print()
    if impossible and not a.quiet:
        print('НЕ ДОСТИГАЕТСЯ НИ ОДНОЙ ФОРМОЙ (вопрос к диапазонам или к формату):')
        for label, rel, base, best in impossible:
            b = '{:.3e}'.format(best) if best is not None and math.isfinite(best) \
                else 'граница не доказана'
            print('  {} ({}): лучшее найденное {}'.format(label, rel, b))
        print()
    if unreadable and not a.quiet:
        print('Непрочитанные функции в счёт НЕ идут и зелёным не считаются: '
              'молчание про них читалось бы как «всё чисто». Чтобы увидеть '
              'причины по видам, запустите tools/arith_coverage.py.')
        print()

    if impossible:
        return 2
    if needs_rewrite:
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
