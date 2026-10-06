# -*- coding: utf-8 -*-
"""Сколько чужого кода мы на самом деле принимаем.

Перед тем как искать красивую находку в чужом проекте, надо честно померить
охват: какая доля настоящих функций проходит через фронтенд и на чём он
спотыкается. Без этого числа любой рассказ про «инструмент можно навести на ваш
код» — это обещание, а не факт.

Запуск:  python tools/scan_repo.py <путь к репозиторию> [--limit N]
Вывод:   принято / отклонено, топ причин отказа, список принятых функций.
"""
from __future__ import annotations

import argparse
import collections
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pareto.cfront import (CParseError, collect_context, functions,
                           constants, globals_of, macro_aliases,
                           make_resolver, parse_function)

SKIP_DIRS = {'.git', 'build', 'cmake', 'tests', 'test', 'examples', 'third_party',
             'external', 'vendor', 'docs', 'doc'}


def reason_key(msg):
    """Сводим сообщение об отказе к короткой причине, чтобы считать их группами."""
    m = msg.lower()
    for probe, key in (
        ('array', 'массивы'),
        ('pointer', 'указатели'),
        ('unknown name', 'неизвестное имя (глобал/поле структуры)'),
        ('call to', 'вызов чужой функции'),
        ('not part of the supported subset', 'конструкция вне подмножества'),
        ('condition must be a comparison', 'условие не сравнение'),
        ('declaration of', 'объявление вне подмножества'),
        ('remainder', 'целочисленный остаток'),
        ('cast to an integer', 'приведение к целому'),
        ('while', 'цикл while'),
        ('statement starting at', 'неизвестный оператор'),
        ('cannot read an expression', 'невычитываемое выражение'),
        ('pow is supported', 'pow с непостоянной степенью'),
    ):
        if probe in m:
            return key
    return msg[:60]


ARITH = {'+', '-', '*', '/', 'fma', 'sqrt', 'exp', 'log', 'expm1', 'log1p',
         'sin', 'cos', 'atan', 'atan2', 'hypot', 'neg'}


def counts_arithmetic(prog):
    """Есть ли в функции хоть одно вычисление.

    Без этого вопроса цифра охвата лжёт в нашу пользу. Половина принятого в
    Chipmunk — это `return body->m`: чтение поля, ни одной операции, ошибка
    тождественно ноль. Такая функция проходит фронтенд и попадает в счёт, но
    доказывать в ней нечего, и покупателю она не говорит ничего. Честная цифра —
    доля функций, в которых есть что считать.
    """
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
            return True                 # ветвление или цикл — уже не чтение поля
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('repo')
    ap.add_argument('--limit', type=int, default=0, help='остановиться после N функций')
    args = ap.parse_args()

    root = Path(args.repo)
    files = [p for p in root.rglob('*')
             if p.suffix in ('.c', '.h')
             and not any(part in SKIP_DIRS for part in p.parts)]

    # Типы и структуры собираются по ВСЕМУ дереву: объявления живут в заголовках,
    # а разбираем мы .c. Без этого почти всё отвергается по причине «тип не
    # объявлен здесь», и цифра охвата говорит о нашей слепоте, а не о коде.
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
    resolve = make_resolver(texts, types, table, macros=macros, consts=consts, globs=globs)
    extra = [t for t in types if t not in ('double', 'float', 'long double')]
    print(f'вещественных типов найдено: {len(types)}'
          + (f' (включая {", ".join(sorted(extra)[:5])})' if extra else ''))
    print(f'структур найдено: {len(table)}')
    print()

    ok, bad, trivial = [], [], []
    reasons = collections.Counter()
    seen = 0

    for f in files:
        try:
            src = f.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        try:
            names = functions(src, types)
        except Exception:
            continue
        for name in names:
            seen += 1
            if args.limit and seen > args.limit:
                break
            try:
                prog = parse_function(src, name, types, table, resolve,
                                      macros, consts, globs)
                ok.append((f.relative_to(root), name))
                if not counts_arithmetic(prog):
                    trivial.append((f.relative_to(root), name))
            except CParseError as e:
                bad.append((f.relative_to(root), name, str(e)))
                reasons[reason_key(str(e))] += 1
            except Exception as e:
                bad.append((f.relative_to(root), name, f'{type(e).__name__}: {e}'))
                reasons[reason_key(str(e))] += 1
        if args.limit and seen > args.limit:
            break

    total = len(ok) + len(bad)
    print(f'файлов просмотрено: {len(files)}')
    print(f'функций найдено:    {total}')
    if total:
        print(f'принято:            {len(ok)}  ({100.0 * len(ok) / total:.1f}%)')
        real = len(ok) - len(trivial)
        print(f'  из них со вычислениями: {real}  '
              f'({100.0 * real / total:.1f}% от всех)')
        print(f'  чтение поля без вычислений: {len(trivial)}  '
              '— проходят фронтенд, но доказывать в них нечего')
        print(f'отклонено:          {len(bad)}')
    print()
    print('почему отказ:')
    for key, n in reasons.most_common(12):
        print(f'  {n:>5}  {key}')
    print()
    print('принятые функции:')
    for p, n in ok[:60]:
        print(f'  {p}  ::  {n}')
    if len(ok) > 60:
        print(f'  ... и ещё {len(ok) - 60}')


if __name__ == '__main__':
    main()
