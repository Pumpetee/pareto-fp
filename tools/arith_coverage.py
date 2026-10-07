# -*- coding: utf-8 -*-
"""Какую долю функций С ВЫЧИСЛЕНИЯМИ мы принимаем.

Знаменатель здесь важнее числителя. «Принято 17.3% функций проекта» ничего не
говорит: в проекте полно функций, где вычислять нечего — `return body->m`,
целочисленные счётчики, `void`-процедуры. Их отсутствие в нашем счёте не наш
недостаток, и ставить их в знаменатель значит назначить себе цель, которой
достичь нельзя.

Поэтому кандидат определяется ПО ТЕКСТУ исходника, независимо от нашего
разборщика: функция считается вещественной, если её возврат или хотя бы один
параметр вещественного типа, и в теле есть арифметика над ними либо вызов
вещественной функции из libm. Решение по тексту, а не по нашему успеху, — иначе
мера превратилась бы в самооценку: отвергли и тем самым объявили «не кандидат».

Мера груба, и это сказано вслух: список кандидатов печатается ключом --list,
чтобы спорное можно было посмотреть глазами, а не поверить на слово.

Запуск:
  python tools/arith_coverage.py <корень проекта> [--list] [--why]
"""
from __future__ import annotations

import argparse
import collections
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pareto.cfront import (CParseError, collect_context, constants, functions,
                           globals_of, macro_aliases, make_resolver,
                           parse_function)

SKIP_DIRS = {'.git', 'build', 'cmake', 'tests', 'test', 'examples', 'example',
             'demo', 'demos', 'third_party', 'external', 'extern', 'vendor',
             'docs', 'doc', 'benchmark', 'benchmarks', 'samples',
             # box2d/shared — не библиотека, а её вспомогательный код: по
             # собственному CMakeLists это отдельная статическая цель, которая
             # собирается только при включённых примерах, тестах или
             # бенчмарках, и её заголовок так и подписан. Исключаю по
             # документу проекта, а не потому, что там неудобные отказы;
             # ключ --with-samples печатает число и вместе с ними.
             'shared'}

LIBM = {'sqrt', 'sqrtf', 'exp', 'expf', 'log', 'logf', 'sin', 'sinf', 'cos',
        'cosf', 'tan', 'tanf', 'atan', 'atanf', 'atan2', 'atan2f', 'pow',
        'powf', 'fabs', 'fabsf', 'hypot', 'hypotf', 'fma', 'fmaf', 'floor',
        'floorf', 'ceil', 'ceilf', 'round', 'roundf', 'expm1', 'log1p',
        'fmin', 'fminf', 'fmax', 'fmaxf', 'fmod', 'fmodf'}

ARITH = {'+', '-', '*', '/'}

_COMMENT = re.compile(r'//[^\n]*|/\*.*?\*/', re.S)
_STRING = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')


def body_of(src, name):
    """Текст тела функции по её имени. None, если не нашли определение."""
    m = re.search(r'\b' + re.escape(name) + r'\s*\(([^)]*)\)\s*\{', src)
    if not m:
        return None, None
    params = m.group(1)
    i = src.index('{', m.end() - 1)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == '{':
            depth += 1
        elif src[j] == '}':
            depth -= 1
            if depth == 0:
                return params, src[i + 1:j]
    return params, src[i + 1:]


def return_type(src, name):
    m = re.search(r'([A-Za-z_][\w\s\*]*?)\s+\b' + re.escape(name) + r'\s*\([^)]*\)\s*\{',
                  src)
    if not m:
        return ''
    return m.group(1)


INT_TYPES = {'int', 'unsigned', 'long', 'short', 'char', 'size_t', 'bool',
             'int8_t', 'int16_t', 'int32_t', 'int64_t', 'uint8_t', 'uint16_t',
             'uint32_t', 'uint64_t', 'void'}


def strip_declarations(text, type_names):
    """Убрать звёздочки объявлений и стрелки, чтобы не счесть их арифметикой.

    Первая версия этой проверки считала `b2World* world` умножением и потому
    объявила кандидатами 132 функции box2d вместо настоящих — цифра охвата
    поехала в нашу пользу на ровном месте. Отличить объявление от умножения по
    одному тексту нельзя, но имена типов проекта у нас есть, и этого достаточно:
    звёздочка после известного типа — объявление, а не операция.
    """
    out = text.replace('->', '.')
    pattern = (r'\b(?:' + '|'.join(sorted(
        (re.escape(t) for t in type_names), key=len, reverse=True))
        + r')\s*\*+')
    out = re.sub(pattern, ' ', out)
    # Приведения вида (float*) и (void *) тоже не арифметика.
    out = re.sub(r'\(\s*[A-Za-z_]\w*\s*\*+\s*\)', ' ', out)
    return out


def is_float_candidate(src, name, float_types, type_names):
    """Есть ли в функции вещественная арифметика. Судим по тексту.

    Нужны оба условия: вещественный тип в подписи или среди локальных, И хоть
    одна арифметическая операция либо вызов libm. Одного типа мало — геттер
    `return body->m` возвращает double и не вычисляет ничего.
    """
    params, body = body_of(src, name)
    if body is None:
        return False, 'определение не найдено'
    clean = _STRING.sub('""', _COMMENT.sub(' ', body))
    ret = return_type(src, name)
    words = set(re.findall(r'[A-Za-z_]\w*', ret + ' ' + (params or '') + ' ' + clean))

    if not (words & float_types):
        return False, 'вещественных типов в подписи и теле нет'

    calls = set(re.findall(r'([A-Za-z_]\w*)\s*\(', clean))
    if calls & LIBM:
        return True, ''

    bare = strip_declarations(clean, type_names)
    # Операнд с обеих сторон: унарный минус и ++ арифметикой не считаем.
    if re.search(r'[\w\)\]]\s*[-+*/]\s*[\w\(\.]', bare):
        return True, ''
    return False, 'вещественный тип есть, но ни одной операции над ним'


TREE_ARITH = {'+', '-', '*', '/', 'fma', 'sqrt', 'exp', 'log', 'expm1',
              'log1p', 'sin', 'cos', 'atan', 'atan2', 'hypot', 'neg'}


def tree_has_arithmetic(prog):
    """Есть ли вычисления в РАЗОБРАННОМ дереве. Нужно для самопроверки меры."""
    def walk(node):
        if not isinstance(node, tuple):
            return False
        if node[0] in TREE_ARITH:
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


def reason_key(msg):
    m = msg.lower()
    for probe, key in (
        ('array', 'массивы'),
        ('pointer', 'указатели'),
        ('unknown name', 'поле структуры или глобал через указатель'),
        ('neither an argument nor a local', 'поле структуры или глобал через указатель'),
        ('call to', 'вызов функции вне подмножества'),
        ('not a single return', 'вызванная функция с ветвлением'),
        ('switch', 'switch'),
        ('while', 'цикл while'),
        ('is declared without a value', 'объявление без значения'),
        ('declaration of', 'объявление вне подмножества'),
        ('trip count', 'цикл с неизвестным числом шагов'),
        ('remainder', 'целочисленный остаток'),
        ('cast to an integer', 'приведение к целому'),
        ("expected ';'", 'конструкция выражения вне подмножества'),
        ("expected ')'", 'конструкция выражения вне подмножества'),
        ('statement starting at', 'неизвестный оператор'),
        ('cannot read an expression', 'невычитываемое выражение'),
        ('pow is supported', 'pow с непостоянной степенью'),
        ('condition must be', 'условие не сравнение'),
    ):
        if probe in m:
            return key
    return msg[:58]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('repo')
    ap.add_argument('--list', action='store_true', help='перечислить отвергнутых кандидатов')
    ap.add_argument('--why', action='store_true', help='печатать полное сообщение отказа')
    ap.add_argument('--with-samples', action='store_true',
                    help='считать и вспомогательный код примеров тоже')
    a = ap.parse_args()

    root = Path(a.repo)
    skip = set() if a.with_samples else SKIP_DIRS
    files = [p for p in root.rglob('*')
             if p.suffix in ('.c', '.h')
             and not any(part.lower() in skip for part in p.parts)]
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
    float_types = set(types) | {'float', 'double'}
    type_names = (set(types) | set(table) | INT_TYPES
                  | {'float', 'double'})

    total = candidates = taken = 0
    disagree = []
    reasons = collections.Counter()
    rejected = []

    for f in files:
        try:
            src = f.read_text(encoding='utf-8', errors='replace')
            names = functions(src, types)
        except Exception:
            continue
        for name in names:
            total += 1
            ok, _ = is_float_candidate(src, name, float_types, type_names)
            if not ok:
                continue
            candidates += 1
            try:
                prog = parse_function(src, name, types, table, resolve, macros,
                                      consts, globs)
                taken += 1
                # Самопроверка меры. Текстовая оценка груба, и единственный
                # способ ей верить — сверять там, где есть с чем сверять: на
                # принятой функции разобранное дерево знает правду точно. Если
                # текст сказал «есть вычисления», а в дереве их нет, значит мера
                # завышает охват в нашу пользу, и это надо видеть, а не замечать
                # случайно. Именно так и поймалось, что `b2World* world`
                # считалось умножением.
                if not tree_has_arithmetic(prog):
                    disagree.append((f.relative_to(root), name))
            except CParseError as e:
                reasons[reason_key(str(e))] += 1
                rejected.append((f.relative_to(root), name, str(e)))
            except Exception as e:
                reasons[reason_key(str(e))] += 1
                rejected.append((f.relative_to(root), name, '{}: {}'.format(
                    type(e).__name__, e)))

    print('проект:', root.name)
    print('функций найдено всего:          {}'.format(total))
    print('из них с вещественной арифметикой (кандидаты): {}'.format(candidates))
    if candidates:
        print('принято из кандидатов:          {}  ({:.1f}%)'.format(
            taken, 100.0 * taken / candidates))
        print('отвергнуто:                     {}'.format(candidates - taken))
    if disagree:
        print()
        print('САМОПРОВЕРКА: текст счёл кандидатом, а в разобранном дереве '
              'вычислений нет — {} шт. Мера завышает охват на столько же.'.format(
                  len(disagree)))
        for rel, name in disagree[:10]:
            print('  {}  ::  {}'.format(rel, name))
    else:
        print()
        print('САМОПРОВЕРКА: на всех принятых функциях текстовая оценка совпала '
              'с разобранным деревом.')
    print()
    if reasons:
        print('почему отвергнуты кандидаты:')
        for reason, n in reasons.most_common(20):
            print('  {:<48} {}'.format(reason, n))
        print()
    if a.list:
        print('отвергнутые кандидаты:')
        for rel, name, msg in rejected:
            print('  {}  ::  {}'.format(rel, name))
            if a.why:
                print('      {}'.format(msg[:150]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
