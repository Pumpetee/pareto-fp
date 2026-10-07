# -*- coding: utf-8 -*-
"""Разбор C++ (и вообще любого языка LLVM) через представление компилятора.

Свой разборщик C доведён до половины функций в чистом C, но на C++ он беспомощен
принципиально: шаблоны, классы, перегрузка операторов, наследование. Полётная
математика PX4 написана ровно так. Поэтому C++ читается иначе: компилятор сам
разворачивает шаблоны и выводит типы, а мы берём то, что он выдал, и считаем
границу по арифметике.

Что это даёт сразу, без отдельной работы: C++, Rust, Swift — всё, что
компилирует LLVM.

Запуск:
  python tools/analyse_cpp.py файл.cpp --range=-1e3..1e3 -I путь/к/заголовкам
  python tools/analyse_cpp.py файл.ll  --function norm
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pareto import budget as _budget
from pareto.api import analyse_expression, domain_hazards
from pareto.program import ProgramError, analyse_program
from pareto.codegen import to_c
from pareto.llfront import (LLError, compile_to_ll, demangle, functions_of)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from difftest_c import CLANG   # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('path', help='файл .cpp/.c/.ll')
    ap.add_argument('--function', help='подстрока имени; без неё — все читаемые')
    ap.add_argument('--range', default='-1e3..1e3')
    ap.add_argument('--budget', type=float, default=10.0, metavar='СЕКУНД')
    ap.add_argument('-I', dest='include', action='append', default=[],
                    help='каталог заголовков, можно повторять')
    ap.add_argument('-D', dest='define', action='append', default=[])
    ap.add_argument('--opt', default='-O2',
                    help='уровень оптимизации при сборке представления. Нужен не '
                         'для скорости: на -O0 тело состоит из обращений к памяти, '
                         'и читать там нечего')
    ap.add_argument('--why', action='store_true',
                    help='печатать причины отказа по функциям')
    a = ap.parse_args()

    if not CLANG:
        raise SystemExit('компилятор не найден: укажите PARETO_CLANG')
    lo, hi = (float(x) for x in a.range.split('..'))

    src = Path(a.path)
    if src.suffix == '.ll':
        ll = src
    else:
        extra = ['-I' + d for d in a.include] + ['-D' + d for d in a.define]
        clangpp = CLANG.replace('clang.exe', 'clang++.exe') \
            if src.suffix in ('.cpp', '.cc', '.cxx') else CLANG
        try:
            ll = compile_to_ll(src, clangpp, extra=extra, opt=a.opt)
        except LLError as e:
            raise SystemExit(str(e))

    print('файл:', src)
    print('представление:', ll)
    print('диапазон каждого входа: [{:g}, {:g}]'.format(lo, hi))
    print('слияние умножения со сложением при сборке ЗАПРЕЩЕНО: иначе компилятор '
          'сольёт их в одну операцию, и граница будет посчитана не для той '
          'программы')
    print()

    read = refused = 0
    for name, (kind, payload, args) in functions_of(ll).items():
        human = demangle(name, Path(CLANG).parent)
        if a.function and a.function not in human and a.function not in name:
            continue
        if kind == 'отказ':
            refused += 1
            if a.why:
                print('отказ  {}'.format(human[:70]))
                print('       {}'.format(payload))
            continue
        read += 1
        if kind == 'программа':
            # Функция с ветвлениями: у каждого пути своя арифметика, своя
            # область достижимости и своя лучшая запись. Разбирается по путям.
            names = ['a' + r if r.isdigit() else r for r, _f in args]
            dom = {n: (lo, hi) for n in names}
            print('=== {}'.format(human[:90]))
            _budget.set_budget(a.budget)
            try:
                res = analyse_program(payload, dom)
            except (ProgramError, Exception) as e:
                print('   разбор по путям не удался: {}'.format(e))
                _budget.clear()
                print()
                continue
            finally:
                _budget.clear()
            print('путей исполнения: {}'.format(len(res.get('paths', []))))
            base = res.get('base_bound')
            best = res.get('best_bound')
            print('граница как есть : {}'.format(
                '{:.3e}'.format(base) if base is not None
                and math.isfinite(base) else 'не доказана'))
            if best is not None and math.isfinite(best):
                print('лучшая найденная: {:.3e}'.format(best))
                if base is not None and math.isfinite(base) and best > 0:
                    print('                  туже в {:.0f} раз'.format(base / best))
            extra = res.get('divergence_extra') or 0.0
            if extra > 0:
                # Отдельная строка нарочно: у ветвления есть своя беда, не
                # связанная с округлением. Условие решается по ВЫЧИСЛЕННЫМ
                # величинам, поэтому у границы условия программа может уйти в
                # другую ветку, и ошибка включает весь скачок между ветками.
                print('из них прыжок между ветками: {:.3e} — условие решается '
                      'по вычисленным величинам, и у самой границы условия '
                      'выбирается другая ветка'.format(extra))
            for pth in res.get('paths', [])[:6]:
                cond = ' и '.join(pth.get('guards_text') or []) or '(всегда)'
                print('   путь {}: {}'.format(pth.get('label', '?'), cond[:70]))
            print()
            continue
        tree = payload
        names = ['a' + r if r.isdigit() else r for r, _f in args]
        dom = {n: (lo, hi) for n in names}
        print('=== {}'.format(human[:90]))
        print('как компилятор её выдал:')
        print('   {}'.format(to_c(tree)[:150]))
        _budget.set_budget(a.budget)
        try:
            res = analyse_expression(tree, dom, keep=5)
        except Exception as e:
            print('   разбор не удался: {}'.format(e))
            _budget.clear()
            continue
        finally:
            cut = _budget.cut_stages()
            _budget.clear()
        base = res['base']['err']
        best = min(res['front'], key=lambda p: p['err'])
        print('граница как есть : {}'.format(
            '{:.3e}'.format(base) if math.isfinite(base) else 'не доказана'))
        print('лучшая найденная: {:.3e}'.format(best['err']))
        if math.isfinite(base) and best['err'] > 0:
            print('                  туже в {:.0f} раз'.format(base / best['err']))
        print('предложить вместо: {}'.format(best['c'][:150]))
        try:
            hz = domain_hazards(tree, dom)
        except Exception:
            hz = []
        for h in hz:
            print('ВНИМАНИЕ: {} от {} на [{:.2e}, {:.2e}] — {}'.format(
                h['op'], h['argument'][:60], h['range'][0], h['range'][1],
                h['why']))
        if cut:
            print('(предел времени оборвал этапы: {})'.format(', '.join(cut)))
        print()

    print('прочитано функций: {} | отвергнуто: {}'.format(read, refused))
    if refused and not a.why:
        print('Причины отказов печатает ключ --why. Отвергаются функции, в теле '
              'которых после оптимизации остались обращения к памяти, циклы или '
              'ветвления: значение за ними зависит от того, чего мы не моделируем.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
