# -*- coding: utf-8 -*-
"""Ошибка как линейная форма по округлениям — метод, которым силён FPTaylor.

Наша прежняя модель несла ошибку одним числом: верхняя оценка, дальше складываем.
Для `x*x − x*x` это даёт сумму двух погрешностей, хотя они одинаковые и при
вычитании сокращаются полностью. Так теряется точность оценки везде, где
подвыражения повторяются, — а повторяются они постоянно.

Здесь ошибка хранится как

    e = Σ cᵢ·δᵢ,   |δᵢ| ≤ 1

где δᵢ — символ ОДНОГО конкретного округления. Символ привязан к структуре
поддерева, поэтому два одинаковых поддерева получают один и тот же символ: при
вычитании их вклады сокращаются, как и должно быть. Итоговая граница — сумма
модулей коэффициентов, то есть максимум формы по всем допустимым δ.

Это тот же приём, что у FPTaylor (символические формы Тейлора первого порядка),
только скромнее: там максимум ищут глобальной оптимизацией по домену, здесь берётся
покоэффициентная оценка сверху. Зато цена — доли миллисекунды, а не запуск решателя.
"""
from __future__ import annotations

import math

U = 2.0 ** -53
INF = float('inf')


class ErrForm:
    """Линейная форма ошибки плюс отдельный неотрицательный остаток."""

    __slots__ = ('terms', 'rest')

    def __init__(self, terms=None, rest=0.0):
        self.terms = dict(terms or {})
        self.rest = float(rest)

    @staticmethod
    def zero():
        return ErrForm()

    def bound(self):
        """Максимум формы: все δ выставлены в худшую сторону."""
        if not math.isfinite(self.rest):
            return INF
        total = self.rest
        for v in self.terms.values():
            if not math.isfinite(v):
                return INF
            total += abs(v)
        return total

    def scaled(self, k):
        k = float(k)
        if not math.isfinite(k):
            return ErrForm(rest=INF)
        return ErrForm({t: v * k for t, v in self.terms.items()}, self.rest * abs(k))

    def __add__(self, other):
        out = ErrForm(self.terms, self.rest + other.rest)
        for t, v in other.terms.items():
            out.terms[t] = out.terms.get(t, 0.0) + v
            if out.terms[t] == 0.0:
                del out.terms[t]
        return out

    def __sub__(self, other):
        return self + other.scaled(-1.0)

    def with_rounding(self, key, magnitude):
        """Добавляет округление этой операции как новый именованный символ."""
        if not math.isfinite(magnitude):
            return ErrForm(rest=INF)
        out = ErrForm(self.terms, self.rest)
        out.terms[key] = out.terms.get(key, 0.0) + U * magnitude
        return out


def node_key(tree):
    """Устойчивый ключ поддерева: одинаковая запись — один и тот же символ округления.

    Ровно это и даёт сокращение: округление в левом `x*x` и в правом `x*x` — одно и
    то же событие, а не два независимых.
    """
    return repr(tree)
