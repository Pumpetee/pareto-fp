# -*- coding: utf-8 -*-
"""Стандартный набор FPBench — общий язык этой области.

Herbie не единственный сосед и даже не самый близкий. Границы ошибки для
плавающей точки уже полтора десятка лет считают FPTaylor (символические формы
Тейлора плюс глобальная оптимизация), Daisy (анализ, переписывание и выбор
точности в одном флаконе), Gappa (границы с сертификатом для Coq) и Salsa. Все они
публикуют результаты на одних и тех же задачах из набора FPBench, поэтому честное
сравнение начинается с того, чтобы прогнать себя ровно на них.

Задачи взяты из `benchmarks/rosa.fpcore` (набор rosa/Daisy): классическая физика,
управление, химия и полиномиальные приближения функций. Домены — из поля `:pre`
исходных FPCore, без изменений.

Домены с нулём внутри оставлены как есть. Там, где деление может обратиться в ноль,
наша интервальная арифметика честно вернёт бесконечную границу — это тоже результат,
и прятать его подгонкой домена нельзя.
"""
from __future__ import annotations


def V(name):
    return ('var', name)


def N(x):
    return ('num', float(x))


def _mul(*xs):
    out = xs[0]
    for x in xs[1:]:
        out = ('*', out, x)
    return out


def _sub(a, b):
    return ('-', a, b)


def _add(*xs):
    out = xs[0]
    for x in xs[1:]:
        out = ('+', out, x)
    return out


def _pow(v, k):
    out = v
    for _ in range(k - 1):
        out = ('*', out, v)
    return out


# doppler: t1 = 331.4 + 0.6·T,  (-t1·v) / ((t1+u)·(t1+u))
def _doppler(u_rng, v_rng, t_rng, points):
    t1 = _add(N(331.4), _mul(N(0.6), V('T')))
    body = ('/', _mul(('neg', t1), V('v')),
            _mul(_add(t1, V('u')), _add(t1, V('u'))))
    return {'expr': body, 'vars': ['u', 'v', 'T'],
            'domain': {'u': u_rng, 'v': v_rng, 'T': t_rng},
            'points': points, 'iters': 6, 'node_limit': 20000}


def _turbine_points(dom):
    def gen(i, n):
        f = (i + 0.31) / n
        return {k: dom[k][0] + (dom[k][1] - dom[k][0]) * f for k in dom}
    return gen


_TURB = {'v': (-4.5, -0.3), 'w': (0.4, 0.9), 'r': (3.8, 7.8)}
_RIGID = {'x1': (-15.0, 15.0), 'x2': (-15.0, 15.0), 'x3': (-15.0, 15.0)}


def _grid(dom):
    def gen(i, n):
        f = (i + 0.17) / n
        return {k: dom[k][0] + (dom[k][1] - dom[k][0]) * f for k in dom}
    return gen


FPBENCH_CASES = {
    # Эффект Доплера: три варианта отличаются только диапазонами входов.
    'doppler1': _doppler((-100.0, 100.0), (20.0, 20000.0), (-30.0, 50.0),
                         _grid({'u': (-100.0, 100.0), 'v': (20.0, 20000.0), 'T': (-30.0, 50.0)})),
    'doppler2': _doppler((-125.0, 125.0), (15.0, 25000.0), (-40.0, 60.0),
                         _grid({'u': (-125.0, 125.0), 'v': (15.0, 25000.0), 'T': (-40.0, 60.0)})),
    'doppler3': _doppler((-30.0, 120.0), (320.0, 20300.0), (-50.0, 30.0),
                         _grid({'u': (-30.0, 120.0), 'v': (320.0, 20300.0), 'T': (-50.0, 30.0)})),

    # Уравнения движения твёрдого тела.
    'rigidBody1': {
        'expr': _sub(_sub(_sub(('neg', _mul(V('x1'), V('x2'))),
                               _mul(_mul(N(2.0), V('x2')), V('x3'))),
                          V('x1')),
                     V('x3')),
        'vars': ['x1', 'x2', 'x3'], 'domain': dict(_RIGID), 'points': _grid(_RIGID),
        'iters': 6, 'node_limit': 20000,
    },
    'rigidBody2': {
        'expr': _add(_sub(_sub(_add(_mul(_mul(N(2.0), V('x1')), _mul(V('x2'), V('x3'))),
                                    _mul(_mul(N(3.0), V('x3')), V('x3'))),
                               _mul(_mul(V('x2'), V('x1')), _mul(V('x2'), V('x3')))),
                          _mul(_mul(N(3.0), V('x3')), V('x3'))),
                     _sub(_mul(V('x2'), V('x1')), V('x2'))),
        'vars': ['x1', 'x2', 'x3'], 'domain': dict(_RIGID), 'points': _grid(_RIGID),
        'iters': 5, 'node_limit': 20000,
    },

    # Турбина: три выходных величины одной модели.
    'turbine1': {
        'expr': _sub(_sub(_add(N(3.0), ('/', N(2.0), _mul(V('r'), V('r')))),
                          ('/', _mul(_mul(N(0.125), _sub(N(3.0), _mul(N(2.0), V('v')))),
                                     _mul(_mul(_mul(V('w'), V('w')), V('r')), V('r'))),
                           _sub(N(1.0), V('v')))),
                     N(4.5)),
        'vars': ['v', 'w', 'r'], 'domain': dict(_TURB), 'points': _turbine_points(_TURB),
        'iters': 5, 'node_limit': 20000,
    },
    'turbine2': {
        'expr': _sub(_sub(_mul(N(6.0), V('v')),
                          ('/', _mul(_mul(N(0.5), V('v')),
                                     _mul(_mul(_mul(V('w'), V('w')), V('r')), V('r'))),
                           _sub(N(1.0), V('v')))),
                     N(2.5)),
        'vars': ['v', 'w', 'r'], 'domain': dict(_TURB), 'points': _turbine_points(_TURB),
        'iters': 5, 'node_limit': 20000,
    },
    'turbine3': {
        'expr': _sub(_sub(_sub(N(3.0), ('/', N(2.0), _mul(V('r'), V('r')))),
                          ('/', _mul(_mul(N(0.125), _add(N(1.0), _mul(N(2.0), V('v')))),
                                     _mul(_mul(_mul(V('w'), V('w')), V('r')), V('r'))),
                           _sub(N(1.0), V('v')))),
                     N(0.5)),
        'vars': ['v', 'w', 'r'], 'domain': dict(_TURB), 'points': _turbine_points(_TURB),
        'iters': 5, 'node_limit': 20000,
    },

    # Логистическая модель роста и модель хищник-жертва.
    'verhulst': {
        'expr': ('/', _mul(N(4.0), V('x')), _add(N(1.0), ('/', V('x'), N(1.11)))),
        'vars': ['x'], 'domain': {'x': (0.1, 0.3)},
        'points': lambda i, n: {'x': 0.1 + 0.2 * (i + 0.23) / n},
        'iters': 8, 'node_limit': 20000,
    },
    'predatorPrey': {
        'expr': ('/', _mul(_mul(N(4.0), V('x')), V('x')),
                 _add(N(1.0), _mul(('/', V('x'), N(1.11)), ('/', V('x'), N(1.11))))),
        'vars': ['x'], 'domain': {'x': (0.1, 0.3)},
        'points': lambda i, n: {'x': 0.1 + 0.2 * (i + 0.23) / n},
        'iters': 8, 'node_limit': 20000,
    },

    # Уравнение Ван-дер-Ваальса для углекислого газа.
    'carbonGas': {
        'expr': _mul(_sub(_add(N(3.5e7), ('/', _mul(N(0.401), _mul(N(1000.0), N(1000.0))),
                                          _mul(V('v'), V('v')))),
                          N(0.0)),
                     _sub(V('v'), _mul(N(1000.0), N(4.27e-5)))),
        'vars': ['v'], 'domain': {'v': (0.1, 0.5)},
        'points': lambda i, n: {'v': 0.1 + 0.4 * (i + 0.19) / n},
        'iters': 8, 'node_limit': 20000,
    },

    # Полиномиальные приближения: синус и квадратный корень.
    'sine': {
        'expr': _add(_sub(_sub(V('x'), ('/', _pow(V('x'), 3), N(6.0))),
                          ('neg', ('/', _pow(V('x'), 5), N(120.0)))),
                     ('neg', ('/', _pow(V('x'), 7), N(5040.0)))),
        'vars': ['x'], 'domain': {'x': (-1.57079632679, 1.57079632679)},
        'points': lambda i, n: {'x': -1.57079632679 + 3.14159265358 * (i + 0.13) / n},
        'iters': 5, 'node_limit': 20000,
    },
    'sqroot': {
        'expr': _sub(_add(_sub(_add(N(1.0), _mul(N(0.5), V('x'))),
                               _mul(_mul(N(0.125), V('x')), V('x'))),
                          _mul(_mul(_mul(N(0.0625), V('x')), V('x')), V('x'))),
                     _mul(_mul(_mul(_mul(N(0.0390625), V('x')), V('x')), V('x')), V('x'))),
        'vars': ['x'], 'domain': {'x': (0.0, 1.0)},
        'points': lambda i, n: {'x': (i + 0.11) / n},
        'iters': 5, 'node_limit': 20000,
    },
}
