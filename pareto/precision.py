# -*- coding: utf-8 -*-
"""Форматы с плавающей точкой, не только binary64.

Зачем это нужно. Настоящий численный код почти никогда не однороден: часть
величин лежит в `double`, часть в `float`, а на ускорителях и в embedded
встречается и то, что уже, чем float. Пока анализ умеет ровно один формат,
инструментом нельзя тронуть чужой файл — в нём объявления типов, а не наша
абстрактная формула.

Как это устроено в дереве. Округление к более узкому формату — это ОТДЕЛЬНЫЙ
узел: `('f32', поддерево)`. Такое решение выбрано сознательно, вместо того чтобы
подвешивать тип к каждому узлу:

* для e-графа `('f32', t)` — обычная унарная операция, которой нет ни в одном
  правиле. Значит правила через неё не переписывают ничего, а алгебра ВОКРУГ неё
  работает как обычно. Переписывание остаётся корректным: `f32(x)` для алгебры
  просто какое-то вещественное число, и любое тождество над этими числами верно;
* смешанная точность выражается тем же механизмом без единого нового понятия:
  `f32(x*y + z)` — это произведение и сумма в double с одним округлением в float
  на выходе, а `f32(f32(x*y) + z)` — уже две разные программы, и обе выражаются
  деревом;
* подбор точности (какое поддерево можно посчитать уже) становится обычной
  вставкой узлов, а не отдельным алгоритмом поверх анализа.

Границы. Округление к ближайшему в формате с p битами мантиссы даёт ошибку не
больше половины улпы результата, ровно как в binary64. Разница только в величине
улпы и в том, где начинаются денормали и переполнение. Поэтому весь остальной
анализ не меняется — меняется лишь функция `half_ulp`.
"""
from __future__ import annotations

import math
import struct

INF = float('inf')


class Format:
    """Двоичный формат: мантисса, границы нормальных чисел, шаг денормалей."""

    __slots__ = ('name', 'mant_bits', 'min_normal', 'max_finite', 'subnormal_ulp',
                 'unit_roundoff', 'c_type')

    def __init__(self, name, mant_bits, emin, emax, c_type):
        self.name = name
        self.mant_bits = mant_bits
        # наименьшее нормальное: 2^emin; наибольшее конечное: (2 - 2^-p) * 2^emax
        self.min_normal = math.ldexp(1.0, emin)
        self.max_finite = (2.0 - math.ldexp(1.0, -mant_bits)) * math.ldexp(1.0, emax)
        self.subnormal_ulp = math.ldexp(1.0, emin - mant_bits)
        self.unit_roundoff = math.ldexp(1.0, -mant_bits - 1)
        self.c_type = c_type

    def ulp(self, mag):
        """Шаг сетки формата в точке |mag|. Монотонна по величине."""
        mag = abs(mag)
        if not math.isfinite(mag):
            return INF
        if mag < self.min_normal:
            return self.subnormal_ulp
        _, e = math.frexp(mag)              # mag = m * 2^e, 0.5 <= m < 1
        return math.ldexp(1.0, e - 1 - self.mant_bits)

    def half_ulp(self, mag):
        """Граница ошибки округления к ближайшему: половина улпы.

        Берём улпу по максимуму модуля на домене — улпа монотонна, значит это и
        есть максимум ошибки округления на всём домене.
        """
        if not math.isfinite(mag):
            return INF
        if mag == 0.0:
            return 0.0
        return self.ulp(mag) / 2.0

    def round(self, v):
        """Значение, округлённое к этому формату. Переполнение — бесконечность."""
        raise NotImplementedError

    def __repr__(self):
        return 'Format({})'.format(self.name)


class _Binary64(Format):
    def __init__(self):
        Format.__init__(self, 'float64', 52, -1022, 1023, 'double')

    def ulp(self, mag):
        mag = abs(mag)
        if not math.isfinite(mag):
            return INF
        # math.ulp уже знает про денормали, где шаг фиксирован
        return math.ulp(mag)

    def round(self, v):
        return float(v)


class _Binary32(Format):
    def __init__(self):
        Format.__init__(self, 'float32', 23, -126, 127, 'float')

    def round(self, v):
        v = float(v)
        if not math.isfinite(v):
            return v
        try:
            return struct.unpack('<f', struct.pack('<f', v))[0]
        except OverflowError:
            # за пределами формата округление к ближайшему даёт бесконечность
            return INF if v > 0 else -INF


class _Binary16(Format):
    """binary16 — то, в чём считают нейросети и часть GPU-кода."""

    def __init__(self):
        Format.__init__(self, 'float16', 10, -14, 15, '_Float16')

    def round(self, v):
        v = float(v)
        if not math.isfinite(v):
            return v
        try:
            return struct.unpack('<e', struct.pack('<e', v))[0]
        except OverflowError:
            return INF if v > 0 else -INF


FLOAT64 = _Binary64()
FLOAT32 = _Binary32()
FLOAT16 = _Binary16()

# Имя узла округления в дереве -> формат. binary64 своего узла не имеет: это
# формат по умолчанию, в котором считается всё, что не обёрнуто.
ROUND_OPS = {
    'f32': FLOAT32,
    'f16': FLOAT16,
}

FORMATS = {
    'float64': FLOAT64, 'double': FLOAT64, 'f64': FLOAT64, 'binary64': FLOAT64,
    'float32': FLOAT32, 'float': FLOAT32, 'f32': FLOAT32, 'binary32': FLOAT32,
    'float16': FLOAT16, 'half': FLOAT16, 'f16': FLOAT16, 'binary16': FLOAT16,
}

# Узел округления, которым оформляется каждый формат.
ROUND_OP_OF = {FLOAT32: 'f32', FLOAT16: 'f16'}


def format_by_name(name):
    key = str(name).strip().lower()
    if key not in FORMATS:
        raise ValueError('unknown floating-point format: {}. available: {}'.format(
            name, ', '.join(sorted(set(f.name for f in FORMATS.values())))))
    return FORMATS[key]


def round_op_for(fmt):
    """Как обернуть поддерево, чтобы оно считалось в формате fmt. None — binary64."""
    return ROUND_OP_OF.get(fmt)


def round_interval(fmt, iv):
    """Образ интервала при округлении к формату.

    Округление к ближайшему монотонно неубывающе, поэтому образ отрезка — это
    отрезок между образами концов. Раздвигать наружу не нужно и нельзя: это
    ровно то множество значений, которое реально окажется в регистре.
    """
    lo, hi = iv
    return (fmt.round(lo), fmt.round(hi))


def overflows(fmt, iv):
    """Может ли значение из интервала не поместиться в формат."""
    top = max(abs(iv[0]), abs(iv[1]))
    if not math.isfinite(top):
        return True
    return top > fmt.max_finite


def has_narrow(tree):
    """Есть ли в дереве узлы округления к узкому формату.

    Нужно тем частям анализа, которые написаны в предположении binary64 и
    неверны при смешанной точности: компенсированные схемы и разложения в ряд.
    Там, где эта проверка сработала, такие кандидаты просто не предлагаются.
    """
    if not isinstance(tree, tuple):
        return False
    if tree[0] in ROUND_OPS:
        return True
    if tree[0] in ('num', 'var'):
        return False
    return any(has_narrow(k) for k in tree[1:] if isinstance(k, tuple))
