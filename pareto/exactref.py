# -*- coding: utf-8 -*-
"""Эталон: значение выражения в вещественной арифметике на 60+ значащих цифрах.

Отдельный модуль по той же причине, что и `evalfp`: это не часть бенчмарков, а
часть проверки. Именно от этого значения отмеряется ошибка любой формы, и им же
решается, куда ушла бы программа при идеальном сравнении в ветвлении. Пока функции
жили внутри замерочного скрипта, любой, кому нужен был эталон, тащил за собой
кейсы, пути к bench и запуск node.

Узлы округления к узкому формату здесь ПРОЗРАЧНЫ: идеальное значение не знает, в
каком формате кто-то собирался считать промежуточные величины.
"""
from __future__ import annotations

from decimal import (Decimal, DivisionByZero, InvalidOperation, Overflow,
                     getcontext, localcontext)

getcontext().prec = 60


# ---------- эталон в 60 значащих цифр ----------
_PI_CACHE = {}


def _pi():
    """Пи с точностью текущего контекста. Нужно для приведения аргумента.

    Без приведения ряд для синуса на аргументе порядка 1e18 не сходится ни к чему
    осмысленному: слагаемые растут до 1e18 степени, и шестьдесят цифр съедаются
    сокращением. А эталон, который врёт, объявляет ошибкой правильный ответ —
    ровно на этом 26.09.2026 мы уже обожглись.
    """
    # Ряд считается один раз на точность и запоминается. Без этого фаззинг встал
    # совсем: пи пересчитывалось на КАЖДЫЙ синус, то есть сотни тысяч раз за прогон.
    key = getcontext().prec
    if key in _PI_CACHE:
        return _PI_CACHE[key]
    getcontext().prec += 10
    three = Decimal(3)
    lasts, t, s, n, na, d, da = 0, three, 3, 1, 0, 0, 24
    while s != lasts:
        lasts = s
        n, na = n + na, na + 8
        d, da = d + da, da + 32
        t = (t * n) / d
        s += t
    getcontext().prec -= 10
    _PI_CACHE[key] = +s
    return _PI_CACHE[key]


# Предел, за которым приведение аргумента требует больше цифр, чем мы готовы
# считать: синус при 1e18 нуждается в восемнадцати дополнительных разрядах пи, и
# честный эталон там обходится дороже всей проверки. Политика та же, что и у
# остального эталона: не можем заверить — не судим точку, а не делаем вид.
_TRIG_LIMIT = Decimal(10) ** 8


def _dec_sin(x):
    if abs(x) > _TRIG_LIMIT:
        raise ValueError('trig argument too large to certify')
    getcontext().prec += 12
    pi = _pi()
    x = x % (2 * pi)                      # приведение: иначе ряд бесполезен
    i, lasts, s, fact, num, sign = 1, 0, x, 1, x, 1
    while s != lasts:
        lasts = s
        i += 2
        fact *= i * (i - 1)
        num *= x * x
        sign *= -1
        s += num / fact * sign
    getcontext().prec -= 12
    return +s


def _dec_cos(x):
    return _dec_sin(x + _pi() / 2)


def _dec_atan(x):
    """Арктангенс рядом, с переносом большого аргумента в малый."""
    getcontext().prec += 12
    one = Decimal(1)
    if abs(x) > one:
        r = _pi() / 2 - _dec_atan(one / x)
        if x < 0:
            r = -_pi() / 2 - _dec_atan(one / x)
        getcontext().prec -= 12
        return +r
    i, lasts, s, num, sign = 1, 0, x, x, 1
    while s != lasts:
        lasts = s
        i += 2
        num *= x * x
        sign *= -1
        s += sign * num / i
    getcontext().prec -= 12
    return +s


def exact_tracked(tree, env):
    """Значение и САМАЯ БОЛЬШАЯ промежуточная величина по пути к нему.

    Второе нужно, чтобы понять, сколько цифр требует задача: если по дороге
    встречалось 1e199, а ответ порядка 1e2, то любое вычисление короче двухсот
    цифр даст не ответ, а мусор из сокращения.
    """
    op = tree[0]
    if op in ('f32', 'f16'):
        # Эталон — это ИДЕАЛЬНОЕ вещественное значение, а округление к узкому
        # формату частью математики не является: это выбор реализации. Поэтому в
        # эталоне такие узлы прозрачны. Именно от этого значения и отмеряется
        # граница ошибки во всём проекте.
        return exact_tracked(tree[1], env)
    if op == 'num':
        v = Decimal(float(tree[1]))
        return v, abs(v)
    if op == 'var':
        v = env[tree[1]]
        return v, abs(v)
    if op == 'neg':
        v, m = exact_tracked(tree[1], env)
        return -v, m
    if op == 'fabs':
        v, m = exact_tracked(tree[1], env)
        return abs(v), m
    if op in ('sin', 'cos', 'atan'):
        v, m = exact_tracked(tree[1], env)
        r = {'sin': _dec_sin, 'cos': _dec_cos, 'atan': _dec_atan}[op](v)
        return r, max(m, abs(r))
    if op == 'atan2':
        y, my = exact_tracked(tree[1], env)
        x, mx = exact_tracked(tree[2], env)
        if x == 0 and y == 0:
            raise ValueError('atan2(0,0)')
        if x > 0:
            r = _dec_atan(y / x)
        elif x < 0:
            r = _dec_atan(y / x) + (_pi() if y >= 0 else -_pi())
        else:
            r = _pi() / 2 if y > 0 else -_pi() / 2
        return r, max(my, mx, abs(r))
    if op in ('sqrt', 'exp', 'log'):
        v, m = exact_tracked(tree[1], env)
        r = v.sqrt() if op == 'sqrt' else (v.exp() if op == 'exp' else v.ln())
        return r, max(m, abs(r))
    a, ma = exact_tracked(tree[1], env)
    b, mb = exact_tracked(tree[2], env)
    if op == 'fmin': r = min(a, b)
    elif op == 'fmax': r = max(a, b)
    elif op == '+': r = a + b
    elif op == '-': r = a - b
    elif op == '*': r = a * b
    elif op == '/': r = a / b
    else: raise ValueError(op)
    return r, max(ma, mb, abs(r))


def exact_stable(tree, env, margin=30, cap=600):
    """Эталон, который знает, хватило ли ему точности. None — значит не знает.

    Фиксированные 60 цифр — ловушка, и она сработала 26.09.2026 на семени 808:
    выражение ((x - exp(x)) + exp(x)) при x около 460 даёт exp(x) порядка 1e199,
    x на его фоне исчезает, эталон схлопывается в ноль — и тест объявил нарушение
    границы там, где формула как раз верна. Врал проверяющий, а не анализ.

    Сравнивать два расчёта разной точности тут бесполезно: и 60, и 120 цифр дают
    один и тот же ноль, то есть два вранья совпадают. Поэтому меряем размах —
    отношение наибольшей промежуточной величины к ответу — и пересчитываем ровно
    один раз с нужным числом цифр. Не укладываемся в потолок или ответ оказался
    нулём после больших промежуточных величин — возвращаем None, и точка не судится.
    Пропустить сомнительную точку честнее, чем объявить ложное нарушение.
    """
    def run(prec):
        with localcontext() as ctx:
            ctx.prec = prec
            return exact_tracked(tree, env)

    try:
        val, top = run(60)
    except (InvalidOperation, DivisionByZero, Overflow, ValueError, KeyError):
        return None
    if val == 0:
        return val if top == 0 else None
    try:
        span = (abs(top) / abs(val)).adjusted()
    except (InvalidOperation, Overflow, DivisionByZero):
        return None
    need = max(60, span + margin)
    if need <= 60:
        return val
    if need > cap:
        return None
    try:
        val2, top2 = run(need)
    except (InvalidOperation, DivisionByZero, Overflow, ValueError, KeyError):
        return None
    if val2 == 0:
        return None
    try:
        if (abs(top2) / abs(val2)).adjusted() + margin > need:
            return None          # после пересчёта размах оказался ещё больше
    except (InvalidOperation, Overflow, DivisionByZero):
        return None
    return val2


def exact(tree, env):
    op = tree[0]
    if op == 'num':
        return Decimal(float(tree[1]))
    if op == 'var':
        return env[tree[1]]
    if op == 'neg':
        return -exact(tree[1], env)
    if op == 'sqrt':
        return exact(tree[1], env).sqrt()
    if op == 'exp':
        return exact(tree[1], env).exp()
    if op == 'log':
        return exact(tree[1], env).ln()
    a = exact(tree[1], env)
    b = exact(tree[2], env)
    if op == '+': return a + b
    if op == '-': return a - b
    if op == '*': return a * b
    if op == '/': return a / b
    raise ValueError(op)

