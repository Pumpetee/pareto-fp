# -*- coding: utf-8 -*-
"""Каждое правило обязано быть тождеством, а не «почти тождеством».

Аудит 25.09.2026 после разбора, в котором внешний рецензент нашёл ошибку в
интервальной арифметике. Правило, верное лишь на части значений, ломает не оценку,
а сам ответ: e-граф объединит классы, которые не равны, и извлечённая форма будет
считать не то выражение.

Проверка численная и грубая, как и положено аудиту: подставляем случайные значения
в обе стороны правила и сравниваем результаты с учётом округления. Значения берутся
и положительные, и отрицательные — большинство опасных случаев живёт именно в знаке
аргумента корня или логарифма.
"""
from __future__ import annotations

import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pareto.fma_compat import fma as exact_fma
from pareto.rules import RULES

SEED = 20260925
TRIALS = 300


def evaluate(pattern, env):
    """Вычисляет шаблон правила при подстановке значений вместо ?переменных."""
    if isinstance(pattern, str):
        return env[pattern]
    op = pattern[0]
    if op == 'num':
        return float(pattern[1])
    if op == 'var':
        return env[pattern[1]]
    kids = [evaluate(k, env) for k in pattern[1:]]
    if op == 'neg':
        return -kids[0]
    if op == 'sqrt':
        return math.sqrt(kids[0])
    if op == 'exp':
        return math.exp(kids[0])
    if op == 'log':
        return math.log(kids[0])
    if op == 'expm1':
        return math.expm1(kids[0])
    if op == 'log1p':
        return math.log1p(kids[0])
    if op == 'hypot':
        return math.hypot(*kids)
    if op == 'fma':
        return exact_fma(kids[0], kids[1], kids[2])
    if op == '+':
        return kids[0] + kids[1]
    if op == '-':
        return kids[0] - kids[1]
    if op == '*':
        return kids[0] * kids[1]
    if op == '/':
        return kids[0] / kids[1]
    raise AssertionError('unknown op in pattern: ' + op)


def pattern_vars(pattern, acc=None):
    acc = set() if acc is None else acc
    if isinstance(pattern, str):
        acc.add(pattern)
    elif pattern[0] not in ('num', 'var'):
        for k in pattern[1:]:
            pattern_vars(k, acc)
    return acc


class RulesAreIdentities(unittest.TestCase):
    def test_every_rewrite_rule_holds_numerically(self):
        rng = random.Random(SEED)
        broken = []

        for rule in RULES:
            lhs, rhs = rule[0], rule[1]
            if callable(rhs):
                continue                     # правила с предусловием проверяются отдельно
            names = sorted(pattern_vars(lhs) | pattern_vars(rhs))
            if not names:
                continue

            mismatches = 0
            checked = 0
            for _ in range(TRIALS):
                env = {n: rng.uniform(-8.0, 8.0) for n in names}
                try:
                    left = evaluate(lhs, env)
                    right = evaluate(rhs, env)
                except (ValueError, ZeroDivisionError, OverflowError):
                    continue                 # обе стороны не определены — не случай правила
                if not (math.isfinite(left) and math.isfinite(right)):
                    continue
                checked += 1
                scale = max(1.0, abs(left), abs(right))
                if abs(left - right) > 1e-9 * scale:
                    mismatches += 1

            if checked and mismatches:
                broken.append((lhs, rhs, mismatches, checked))

        msg = '\n'.join(f'  {l} != {r}  ({m} из {c} проверок)' for l, r, m, c in broken)
        self.assertFalse(broken, 'правила, не являющиеся тождествами:\n' + msg)

    def test_rules_do_not_silently_produce_nan(self):
        """Одна сторона определена, другая нет — тоже дефект: классы объединять нельзя."""
        rng = random.Random(SEED + 1)
        asymmetric = []

        for rule in RULES:
            lhs, rhs = rule[0], rule[1]
            if callable(rhs):
                continue
            names = sorted(pattern_vars(lhs) | pattern_vars(rhs))
            if not names:
                continue
            bad = 0
            for _ in range(TRIALS):
                env = {n: rng.uniform(-8.0, 8.0) for n in names}

                def safe(p):
                    try:
                        v = evaluate(p, env)
                        return v if math.isfinite(v) else None
                    except (ValueError, ZeroDivisionError, OverflowError):
                        return None

                left, right = safe(lhs), safe(rhs)
                if (left is None) != (right is None):
                    bad += 1
            if bad:
                asymmetric.append((lhs, rhs, bad))

        msg = '\n'.join(f'  {l}  <->  {r}  (расходятся по определённости {n} раз)'
                        for l, r, n in asymmetric)
        self.assertFalse(asymmetric, 'правила, где одна сторона определена, а другая нет:\n' + msg)


if __name__ == '__main__':
    unittest.main()
