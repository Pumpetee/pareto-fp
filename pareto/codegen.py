# -*- coding: utf-8 -*-
"""Печать дерева выражения: человекочитаемо, как C и как JS-код.

Узлы округления к узкому формату печатаются приведением типа: в C это
`(float)(...)`, в JS — `Math.fround(...)`, в человеческой записи — `f32(...)`.
Приведение обязано остаться в тексте: без него напечатанная формула считалась бы
целиком в double и не отвечала бы той границе ошибки, которую мы обещаем рядом.
"""
from pareto.precision import ROUND_OPS

JS_FUN = {'sqrt': 'Math.sqrt', 'exp': 'Math.exp', 'log': 'Math.log',
          'expm1': 'Math.expm1', 'log1p': 'Math.log1p'}

# В JS узкий формат ровно один — Math.fround, то есть binary32.
JS_ROUND = {'f32': 'Math.fround'}


def to_js(t):
    op = t[0]
    if op in ('approx', 'eft'):   # служебные обёртки в коде не видны
        return to_js(t[1])
    if op in ROUND_OPS:
        fn = JS_ROUND.get(op)
        if fn is None:
            raise ValueError('JavaScript has no rounding to {}'.format(op))
        return fn + '(' + to_js(t[1]) + ')'
    if op == 'num':
        v = t[1]
        return repr(float(v))
    if op == 'var':
        return t[1]
    if op == 'neg':
        return '(-' + to_js(t[1]) + ')'
    if op in JS_FUN:
        return JS_FUN[op] + '(' + to_js(t[1]) + ')'
    if op == 'hypot':
        return 'Math.hypot(' + to_js(t[1]) + ', ' + to_js(t[2]) + ')'
    if op == 'fma':
        return '(' + to_js(t[1]) + ' * ' + to_js(t[2]) + ' + ' + to_js(t[3]) + ')'
    return '(' + to_js(t[1]) + ' ' + op + ' ' + to_js(t[2]) + ')'


C_FUN = {'sqrt': 'sqrt', 'exp': 'exp', 'log': 'log',
         'expm1': 'expm1', 'log1p': 'log1p'}


def to_c(t):
    op = t[0]
    if op in ('approx', 'eft'):   # служебные обёртки в коде не видны
        return to_c(t[1])
    if op in ROUND_OPS:
        return '(' + ROUND_OPS[op].c_type + ')(' + to_c(t[1]) + ')'
    if op == 'num':
        return repr(float(t[1]))
    if op == 'var':
        return t[1]
    if op == 'neg':
        return '(-' + to_c(t[1]) + ')'
    if op in C_FUN:
        return C_FUN[op] + '(' + to_c(t[1]) + ')'
    if op in ('fma', 'hypot'):
        return op + '(' + ', '.join(to_c(k) for k in t[1:]) + ')'
    return '(' + to_c(t[1]) + ' ' + op + ' ' + to_c(t[2]) + ')'


def to_text(t):
    op = t[0]
    if op in ('approx', 'eft'):   # служебные обёртки в коде не видны
        return to_text(t[1])
    if op in ROUND_OPS:
        return op + '(' + to_text(t[1]) + ')'
    if op == 'num':
        v = float(t[1])
        return str(int(v)) if v == int(v) else repr(v)
    if op == 'var':
        return t[1]
    if op == 'neg':
        return '-' + to_text(t[1])
    if op in ('sqrt', 'exp', 'log', 'expm1', 'log1p'):
        return op + '(' + to_text(t[1]) + ')'
    if op in ('fma', 'hypot'):
        return op + '(' + ', '.join(to_text(k) for k in t[1:]) + ')'
    return '(' + to_text(t[1]) + ' ' + op + ' ' + to_text(t[2]) + ')'


def _nodes(t):
    if not isinstance(t, tuple) or t[0] in ('num', 'var'):
        return 1
    if t[0] in ('approx', 'eft'):
        return _nodes(t[1])
    return 1 + sum(_nodes(k) for k in t[1:])


def to_c_block(tree, prefix='t', min_size=3, indent='    '):
    """Тело на C с промежуточными переменными вместо повторов.

    Зачем это нужно. Подстановка локальных переменных, без которой не разобрать
    настоящую функцию, размножает поддеревья: три витка метода Ньютона, записанные
    одним выражением, дают строку в несколько килобайт, которую человек не прочтёт
    и не станет вставлять к себе. Обратная операция — вынести каждое повторяющееся
    поддерево в свою переменную.

    Это не только косметика. Модель стоимости в проекте с самого начала считает
    общие подвыражения ОДИН раз (`cse_work` в analysis.py), то есть обещанная цена
    соответствует именно такой записи, с переменными, а не буквальному повтору.
    Печатать повтор значило бы обещать одну цену, а отдавать другую.

    Одинаковые поддеревья дают и одно округление — а значит одну и ту же
    погрешность, и именно так её считает символическая форма ошибки. Здесь эти три
    места наконец согласованы между собой.
    """
    counts = {}

    def count(node):
        if not isinstance(node, tuple) or node[0] in ('num', 'var'):
            return
        if node[0] in ('approx', 'eft'):
            count(node[1])
            return
        key = repr(node)
        counts[key] = counts.get(key, 0) + 1
        if counts[key] > 1:
            return                        # внутрь повтора второй раз не идём
        for k in node[1:]:
            count(k)

    count(tree)
    shared = {k for k, n in counts.items() if n > 1}

    lines = []
    names = {}
    counter = [0]

    def emit(node):
        if not isinstance(node, tuple):
            return to_c(node)
        op = node[0]
        if op in ('num', 'var'):
            return to_c(node)
        if op in ('approx', 'eft'):
            return emit(node[1])
        key = repr(node)
        if key in names:
            return names[key]
        if op in ROUND_OPS:
            text = '(' + ROUND_OPS[op].c_type + ')(' + emit(node[1]) + ')'
        elif op == 'neg':
            text = '(-' + emit(node[1]) + ')'
        elif op in C_FUN:
            text = C_FUN[op] + '(' + emit(node[1]) + ')'
        elif op in ('fma', 'hypot'):
            text = op + '(' + ', '.join(emit(k) for k in node[1:]) + ')'
        else:
            text = '(' + emit(node[1]) + ' ' + op + ' ' + emit(node[2]) + ')'
        if key in shared and _nodes(node) >= min_size:
            counter[0] += 1
            var = '{}{}'.format(prefix, counter[0])
            ctype = ROUND_OPS[op].c_type if op in ROUND_OPS else 'double'
            lines.append('{}{} {} = {};'.format(indent, ctype, var, text))
            names[key] = var
            return var
        return text

    final = emit(tree)
    lines.append('{}return {};'.format(indent, final))
    return '\n'.join(lines)


def op_count(t):
    """Сколько операций каждого вида в дереве."""
    from collections import Counter
    c = Counter()

    def walk(n):
        if n[0] in ('num', 'var'):
            return
        c[n[0]] += 1
        for k in n[1:]:
            walk(k)

    walk(t)
    return dict(c)
