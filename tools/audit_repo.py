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

from pareto.api import analyse_c_function
from pareto.cfront import CParseError, functions, parse_function


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('path')
    ap.add_argument('--range', default='-1e20..1e20')

    args = ap.parse_args()

    lo, hi = (float(x) for x in args.range.split('..'))
    src = Path(args.path).read_text(encoding='utf-8', errors='replace')

    rows = []
    for name in functions(src):
        try:
            prog = parse_function(src, name)
        except Exception:
            continue
        dom = {a: (lo, hi) for a in prog['args']}
        try:
            res = analyse_c_function(src, name, dom=dom)
        except Exception as e:
            rows.append((name, None, None, f'{type(e).__name__}'))
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
        rows.append((name, written, best, form))

    print(f'файл: {args.path}')
    print(f'диапазон для КАЖДОГО аргумента: [{lo:g}, {hi:g}]')
    print()
    bad = [r for r in rows if r[1] is None or not math.isfinite(r[1] or math.inf)]
    print(f'принятых функций: {len(rows)} | граница НЕ доказана как написано: {len(bad)}')
    print()
    hdr = f'{"функция":<24}{"как написано":>14}{"после переписи":>16}  форма'
    print(hdr)
    for name, w, b, form in rows:
        ws = 'не доказана' if (w is None or not math.isfinite(w)) else f'{w:.3e}'
        bs = '—' if (b is None or not math.isfinite(b)) else f'{b:.3e}'
        print(f'{name:<24}{ws:>14}{bs:>16}  {str(form)[:46]}')


if __name__ == '__main__':
    main()
