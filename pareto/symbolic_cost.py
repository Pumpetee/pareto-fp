# -*- coding: utf-8 -*-
"""Граница ошибки дерева, посчитанная символической формой.

Работает параллельно обычному `tree_cost`: считает ту же величину, но с учётом того,
что одинаковые подвыражения округляются одинаково. Где символический счёт оказался
туже — берём его, где нет — остаётся прежний. Хуже стать не может по построению.

Поддерживаются те же операции, что и в основном анализе. Всё, чего здесь нет,
возвращает None, и вызывающая сторона просто пользуется интервальной оценкой.
"""
from __future__ import annotations

import math

U = 2.0 ** -53

from pareto.analysis import (eval_interval, exact_op, half_ulp, iv_abs_max,
                             iv_abs_min, op_unit, tighten)
from pareto.precision import ROUND_OPS, overflows
from pareto.symbolic_error import ErrForm, node_key

INF = float('inf')


def _safe(iv):
    return iv is not None and all(math.isfinite(v) for v in iv)


def symbolic_error(tree, domain):
    """(форма ошибки, интервал значения) либо (None, None), если операция не покрыта."""
    op = tree[0]
    if op == 'num':
        v = float(tree[1])
        return ErrForm.zero(), (v, v)
    if op == 'var':
        return ErrForm.zero(), domain[tree[1]]
    if op in ('approx', 'eft'):
        return None, None                  # у них своя модель погрешности

    kids = [symbolic_error(k, domain) for k in tree[1:]]
    if any(e is None for e, _ in kids):
        return None, None
    errs = [e for e, _ in kids]
    ivs = [iv for _, iv in kids]
    if not all(_safe(iv) for iv in ivs):
        return None, None

    try:
        out_iv = tighten(tree, domain, eval_interval(op, ivs))
    except (ValueError, ZeroDivisionError, OverflowError, KeyError):
        return None, None
    if not _safe(out_iv):
        return None, None

    key = node_key(tree)
    # Символический путь держит ту же модель округления, что и интервальный:
    # бюджет по операциям (exp/log не корректно округлены), половина улпы вместо
    # U * |значение| и признание точных операций. Иначе символическая оценка
    # оказывалась хуже интервальной там, где должна была выигрывать, и минимум из
    # двух её просто не выбирал — на sine это и стоило нам девятой победы.
    #
    # ErrForm хранит слагаемое как U * magnitude, поэтому величину пересчитываем
    # обратно в эту единицу.
    _kid_errs = []
    for form, _iv in kids:
        try:
            _kid_errs.append(form.bound())
        except Exception:
            _kid_errs.append(float('inf'))
    if op in ROUND_OPS:
        # Округление к узкому формату: та же линейная форма, но улпа берётся в
        # целевом формате и в точке «максимум модуля плюс накопленная ошибка».
        # Символ привязан к записи поддерева, поэтому два одинаковых округления
        # к float сокращаются в разности точно так же, как обычные.
        fmt = ROUND_OPS[op]
        if overflows(fmt, out_iv) or not math.isfinite(_kid_errs[0]):
            return None, None
        top = iv_abs_max(out_iv) + _kid_errs[0]
        if top > fmt.max_finite:
            return None, None
        m = 0.0 if exact_op(tree, ivs, _kid_errs, out_iv) else fmt.half_ulp(top) / U
        return errs[0].with_rounding(key, m), out_iv

    if exact_op(tree, ivs, _kid_errs, out_iv):
        mag = 0.0
    else:
        mag = half_ulp(iv_abs_max(out_iv)) * op_unit(op) / U

    if op == 'neg':
        return errs[0].scaled(-1.0), out_iv
    if op == '+':
        return (errs[0] + errs[1]).with_rounding(key, mag), out_iv
    if op == '-':
        # вот ради чего всё затевалось: одинаковые поддеревья дают одинаковые
        # символы, и при вычитании они сокращаются, а не складываются
        return (errs[0] - errs[1]).with_rounding(key, mag), out_iv
    if op == '*':
        a, b = ivs
        form = (errs[0].scaled(iv_abs_max(b), (key, 'mulL'))
                + errs[1].scaled(iv_abs_max(a), (key, 'mulR')))
        return form.with_rounding(key, mag), out_iv
    if op == 'fma':
        a, b = ivs[0], ivs[1]
        form = (errs[0].scaled(iv_abs_max(b), (key, 'fmaL'))
                + errs[1].scaled(iv_abs_max(a), (key, 'fmaR')) + errs[2])
        return form.with_rounding(key, mag), out_iv
    if op == '/':
        a, b = ivs
        bmin = iv_abs_min(b)
        if bmin == 0.0:
            return None, None
        form = (errs[0].scaled(1.0 / bmin, (key, 'divN'))
                + errs[1].scaled(iv_abs_max(a) / (bmin * bmin), (key, 'divD')))
        return form.with_rounding(key, mag), out_iv
    if op == 'sqrt':
        amin = iv_abs_min(ivs[0])
        if amin <= 0.0:
            return None, None
        return errs[0].scaled(1.0 / (2.0 * math.sqrt(amin)), (key, 'sqrt')).with_rounding(key, mag), out_iv
    if op == 'exp':
        return errs[0].scaled(iv_abs_max(out_iv), (key, 'exp')).with_rounding(key, mag), out_iv
    if op == 'log':
        amin = iv_abs_min(ivs[0])
        if amin <= 0.0:
            return None, None
        return errs[0].scaled(1.0 / amin, (key, 'un')).with_rounding(key, mag), out_iv
    if op == 'expm1':
        top = iv_abs_max(ivs[0])
        if top > 709.78:
            return None, None
        return errs[0].scaled(math.exp(top), (key, 'un')).with_rounding(key, mag), out_iv
    if op == 'log1p':
        a = ivs[0]
        dmin = iv_abs_min((1.0 + a[0], 1.0 + a[1]))
        if dmin == 0.0:
            return None, None
        return errs[0].scaled(1.0 / dmin, (key, 'un')).with_rounding(key, mag), out_iv
    if op == 'hypot':
        return (errs[0] + errs[1]).with_rounding(key, mag), out_iv
    return None, None


def symbolic_bound(tree, domain):
    """Граница по символической форме либо None, если посчитать не вышло."""
    try:
        form, _ = symbolic_error(tree, domain)
    except (RecursionError, ValueError, ZeroDivisionError, OverflowError, KeyError):
        return None
    if form is None:
        return None
    b = form.bound()
    return b if math.isfinite(b) else None
