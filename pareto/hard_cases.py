# -*- coding: utf-8 -*-
"""A harder benchmark, because the first one was too easy for everybody.

On the original seven cases both this project and Herbie bottom out at one or two
ulps: after rewriting there is simply nothing left to win in double precision, and
any comparison there measures noise. So this file adds problems where the error has
room to be large — classic numerical-analysis traps with known stable forms, longer
expressions where rounding accumulates, and expressions whose trouble lives in a
narrow part of a wide domain.

Each case carries the domain and a point generator, the same contract as `run.CASES`,
so every existing script can consume both sets.
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


# Taylor series for exp(x), eight terms, written out the naive way.
# Rounding accumulates term by term; Horner is the textbook answer and the question
# is whether either tool finds it.
_FACT = [1.0, 1.0, 2.0, 6.0, 24.0, 120.0, 720.0, 5040.0]
_exp_series = _add(*[('/', _pow(V('x'), k), N(_FACT[k])) if k else N(1.0)
                     for k in range(8)])

HARD_CASES = {
    # The quadratic formula. With b*b >> 4ac the subtraction in -b + sqrt(b*b - 4ac)
    # cancels catastrophically; the stable form is 2c / (-b - sqrt(b*b - 4ac)).
    'quadratic_root': {
        'expr': ('/', ('+', ('neg', V('b')),
                       ('sqrt', ('-', _mul(V('b'), V('b')), _mul(N(4.0), V('a'), V('c'))))),
                 _mul(N(2.0), V('a'))),
        'vars': ['a', 'b', 'c'],
        'domain': {'a': (1.0, 1.0000001), 'b': (1e7, 1e8), 'c': (1.0, 2.0)},
        'points': lambda i, n: {'a': 1.0, 'b': 1e7 + (1e8 - 1e7) * i / n, 'c': 1.0 + i / n},
    },
    # Variance the wrong way: E[x^2] - E[x]^2. With a large mean and small spread the
    # two terms are nearly equal and the difference loses every significant digit.
    'variance': {
        'expr': ('-', ('/', _add(_mul(V('a'), V('a')), _mul(V('b'), V('b')),
                                 _mul(V('c'), V('c'))), N(3.0)),
                 _mul(('/', _add(V('a'), V('b'), V('c')), N(3.0)),
                      ('/', _add(V('a'), V('b'), V('c')), N(3.0)))),
        'vars': ['a', 'b', 'c'],
        'domain': {'a': (1e6, 1e6 + 1.0), 'b': (1e6, 1e6 + 1.0), 'c': (1e6, 1e6 + 1.0)},
        'points': lambda i, n: {'a': 1e6 + i / n, 'b': 1e6 + 0.5 * i / n, 'c': 1e6 + 0.25 * i / n},
    },
    # (a+b)^2 - (a-b)^2 is exactly 4ab. With b tiny next to a the written form throws
    # away the answer entirely.
    'sq_expand': {
        'expr': ('-', _mul(('+', V('a'), V('b')), ('+', V('a'), V('b'))),
                 _mul(('-', V('a'), V('b')), ('-', V('a'), V('b')))),
        'vars': ['a', 'b'],
        'domain': {'a': (1e5, 1e6), 'b': (1e-6, 1e-5)},
        'points': lambda i, n: {'a': 1e5 + 9e5 * i / n, 'b': 1e-6 + 9e-6 * i / n},
    },
    # Difference of cubes, close arguments: same trap as the difference of squares
    # but one degree deeper, so the stable factorisation is less obvious.
    'cube_diff': {
        'expr': ('-', _pow(V('x'), 3), _pow(V('y'), 3)),
        'vars': ['x', 'y'],
        'domain': {'x': (1000.0, 1000.001), 'y': (999.999, 1000.0)},
        'points': lambda i, n: {'x': 1000.0 + 0.001 * i / n, 'y': 1000.0 - 0.001 * i / n},
    },
    # exp(x) - 1 for small x. The correct answer is expm1; neither our rules nor a
    # pure algebraic rewrite can produce it, which is exactly the point of including it.
    'exp_minus_one': {
        'expr': ('-', ('exp', V('x')), N(1.0)),
        'vars': ['x'],
        'domain': {'x': (1e-12, 1e-6)},
        'points': lambda i, n: {'x': 1e-12 + (1e-6 - 1e-12) * (i + 0.37) / n},
    },
    # log(1 + x) for small x — the log1p trap, same idea from the other side.
    'log_one_plus': {
        'expr': ('log', ('+', N(1.0), V('x'))),
        'vars': ['x'],
        'domain': {'x': (1e-12, 1e-6)},
        'points': lambda i, n: {'x': 1e-12 + (1e-6 - 1e-12) * (i + 0.37) / n},
    },
    # Eight-term Taylor series for exp, expanded. Long expression, error accumulates,
    # and the cost difference between the naive form and Horner is real.
    'exp_series': {
        'expr': _exp_series,
        'vars': ['x'],
        'domain': {'x': (0.1, 3.0)},
        'points': lambda i, n: {'x': 0.1 + 2.9 * (i + 0.31) / n},
        'iters': 6,
        'node_limit': 40000,
    },
    # Hypotenuse. sqrt(x*x + y*y) overflows long before the result does; the stable
    # form scales by the larger operand first.
    'hypot': {
        'expr': ('sqrt', ('+', _mul(V('x'), V('x')), _mul(V('y'), V('y')))),
        'vars': ['x', 'y'],
        'domain': {'x': (1e150, 1e155), 'y': (1e150, 1e155)},
        'points': lambda i, n: {'x': 1e150 * (1 + i / n), 'y': 1e150 * (1 + 0.5 * i / n)},
    },
    # A ratio whose numerator cancels: (x*x - 1) / (x - 1) is x + 1, but written this
    # way it loses digits as x approaches one.
    'ratio_cancel': {
        'expr': ('/', ('-', _mul(V('x'), V('x')), N(1.0)), ('-', V('x'), N(1.0))),
        'vars': ['x'],
        'domain': {'x': (1.0000001, 1.001)},
        'points': lambda i, n: {'x': 1.0000001 + (1.001 - 1.0000001) * (i + 0.19) / n},
    },
}
