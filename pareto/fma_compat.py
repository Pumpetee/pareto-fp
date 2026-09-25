# -*- coding: utf-8 -*-
"""Точная fma на любой версии Python.

`math.fma` появилась только в Python 3.13. Привычная замена `a*b + c` — НЕ fma: там
два округления вместо одного, и это ровно то, ради чего fma существует. Подмена
тихо ломала проверки на CI: модель ошибки считала одно округление, а тест выполнял
два, и граница выглядела нарушенной на ровном месте.

Замена честная: произведение и сумма считаются в рациональной арифметике без потерь,
и результат округляется к ближайшему double ровно один раз — это и есть определение
fma. Медленнее, но нужна она только в тестах и замерах.
"""
from __future__ import annotations

import math
from fractions import Fraction

HAS_NATIVE_FMA = hasattr(math, 'fma')


def fma(a, b, c):
    if HAS_NATIVE_FMA:
        return math.fma(a, b, c)
    if not (math.isfinite(a) and math.isfinite(b) and math.isfinite(c)):
        return a * b + c
    return float(Fraction(a) * Fraction(b) + Fraction(c))
