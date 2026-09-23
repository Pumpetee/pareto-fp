# -*- coding: utf-8 -*-
"""Редукции: та же пара метрик, но для суммы массива.

Скалярное выражение оптимизируется перестановкой узлов. Для агрегата выбор
другой: это СХЕМА суммирования. Схемы дают один и тот же математический
результат и разную пару «время — ошибка», то есть ровно тот же фронт Парето,
только точки на нём — алгоритмы, а не формы выражения.

Границы ошибки — классика (Higham, Accuracy and Stability of Numerical
Algorithms, гл. 4): при суммировании n чисел в binary64
  наивная       |E| <= (n-1) * u * S,     S = sum |x_i|
  блочная (k)   |E| <= (n/k + k - 1) * u * S
  попарная      |E| <= log2(n) * u * S
  Кэхен         |E| <= (2u + O(n u^2)) * S
u = 2^-53.
"""
from __future__ import annotations

import math

U = 2.0 ** -53


SCHEMES = {
    'naive': {
        'title': 'наивная сумма',
        'err': lambda n: (n - 1) * U,
        # работа: n сложений, критический путь: тоже n — цепочка зависимостей
        'work': lambda n: float(n),
        'lat': lambda n: float(n),
        'js': """
  let s = 0.0;
  for (let i = 0; i < N; i++) s += A[i];
  return s;""",
    },
    'block4': {
        'title': 'блочная, 4 аккумулятора',
        'err': lambda n: (n / 4 + 3) * U,
        'work': lambda n: float(n),
        'lat': lambda n: n / 4.0 + 2,
        'js': """
  let s0 = 0.0, s1 = 0.0, s2 = 0.0, s3 = 0.0;
  let i = 0;
  for (; i + 3 < N; i += 4) {
    s0 += A[i]; s1 += A[i + 1]; s2 += A[i + 2]; s3 += A[i + 3];
  }
  for (; i < N; i++) s0 += A[i];
  return (s0 + s1) + (s2 + s3);""",
    },
    'block8': {
        'title': 'блочная, 8 аккумуляторов',
        'err': lambda n: (n / 8 + 7) * U,
        'work': lambda n: float(n),
        'lat': lambda n: n / 8.0 + 3,
        'js': """
  let s0 = 0.0, s1 = 0.0, s2 = 0.0, s3 = 0.0, s4 = 0.0, s5 = 0.0, s6 = 0.0, s7 = 0.0;
  let i = 0;
  for (; i + 7 < N; i += 8) {
    s0 += A[i]; s1 += A[i + 1]; s2 += A[i + 2]; s3 += A[i + 3];
    s4 += A[i + 4]; s5 += A[i + 5]; s6 += A[i + 6]; s7 += A[i + 7];
  }
  for (; i < N; i++) s0 += A[i];
  return ((s0 + s1) + (s2 + s3)) + ((s4 + s5) + (s6 + s7));""",
    },
    'pairwise': {
        'title': 'попарная (рекурсивная)',
        'err': lambda n: math.log2(max(n, 2)) * U,
        'work': lambda n: float(n),
        'lat': lambda n: math.log2(max(n, 2)) + 1,
        'js': """
  function rec(lo, hi) {
    if (hi - lo <= 8) {
      let s = 0.0;
      for (let i = lo; i < hi; i++) s += A[i];
      return s;
    }
    const mid = (lo + hi) >> 1;
    return rec(lo, mid) + rec(mid, hi);
  }
  return rec(0, N);""",
    },
    'kahan': {
        'title': 'компенсированная (Кэхен)',
        'err': lambda n: 2.0 * U,
        'work': lambda n: 4.0 * n,
        'lat': lambda n: 4.0 * n,
        'js': """
  let s = 0.0, c = 0.0;
  for (let i = 0; i < N; i++) {
    const y = A[i] - c;
    const t = s + y;
    c = (t - s) - y;
    s = t;
  }
  return s;""",
    },
    'neumaier': {
        'title': 'компенсированная (Нейман)',
        'err': lambda n: 2.0 * U,
        'work': lambda n: 5.0 * n,
        'lat': lambda n: 5.0 * n,
        'js': """
  let s = 0.0, c = 0.0;
  for (let i = 0; i < N; i++) {
    const t = s + A[i];
    if (Math.abs(s) >= Math.abs(A[i])) c += (s - t) + A[i];
    else c += (A[i] - t) + s;
    s = t;
  }
  return s + c;""",
    },
}


def bound(scheme, n, sum_abs):
    """Верхняя граница абсолютной ошибки схемы на массиве с суммой модулей sum_abs."""
    return SCHEMES[scheme]['err'](n) * sum_abs


def analytic_cost(scheme, n):
    """Чисто аналитическая стоимость: работа плюс критический путь."""
    s = SCHEMES[scheme]
    return s['work'](n) + s['lat'](n)


# ---------- калибровка модели стоимости по машине ----------
# Аналитическая модель систематически врёт: она не знает ни про стоимость
# рекурсивного вызова, ни про то, как конкретный JIT раскладывает ветвления.
# Проверено на попарной сумме: по теории дешевле наивной, на деле втрое дороже.
# Поэтому стоимость калибруется один раз замером (наносекунды на элемент),
# а доказанная граница ошибки остаётся аналитической — её калибровать нельзя.
import json as _json
import os as _os

_CALIB_PATH = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                            'bench', 'calibration.json')
_CALIB = None


def load_calibration(path=None):
    global _CALIB
    p = path or _CALIB_PATH
    try:
        with open(p, 'r', encoding='utf-8') as f:
            _CALIB = _json.load(f)
    except (OSError, ValueError):
        _CALIB = None
    return _CALIB


def save_calibration(ns_per_elem, path=None):
    global _CALIB
    p = path or _CALIB_PATH
    with open(p, 'w', encoding='utf-8') as f:
        _json.dump(ns_per_elem, f, ensure_ascii=False, indent=2)
    _CALIB = ns_per_elem
    return p


def cost(scheme, n):
    """Стоимость схемы: калиброванная, если замер есть, иначе аналитическая."""
    if _CALIB is None:
        load_calibration()
    if _CALIB and scheme in _CALIB:
        return _CALIB[scheme] * n
    return analytic_cost(scheme, n)


def pareto(n, sum_abs, names=None):
    """Недоминируемые схемы по паре (стоимость, граница ошибки)."""
    names = names or list(SCHEMES)
    pts = [(cost(nm, n), bound(nm, n, sum_abs), nm) for nm in names]
    out = []
    for c, e, nm in sorted(pts):
        if not any(c2 <= c and e2 <= e and (c2 < c or e2 < e) for c2, e2, _ in out):
            out.append((c, e, nm))
    return out


def pick(n, sum_abs, budget=None, cost_budget=None, base='naive'):
    """Выбор точки фронта под политику.

    budget — во сколько раз разрешено ухудшить границу ошибки против base.
    cost_budget — во сколько раз разрешено удорожить против base.
    """
    front = pareto(n, sum_abs)
    b_cost, b_err = cost(base, n), bound(base, n, sum_abs)
    if budget is not None:
        ok = [p for p in front if p[1] <= b_err * budget]
        return min(ok, key=lambda p: p[0]) if ok else min(front, key=lambda p: p[1])
    if cost_budget is not None:
        ok = [p for p in front if p[0] <= b_cost * cost_budget]
        return min(ok, key=lambda p: p[1]) if ok else min(front, key=lambda p: p[0])
    return front[0]
