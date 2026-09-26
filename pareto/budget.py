# -*- coding: utf-8 -*-
"""Часы на весь анализ, а не только на насыщение e-графа.

Полный прогон по десяти задачам FPBench занимал 227 секунд, и почти всё это время
уходило не в поиск по графу — у того свои часы стоят давно, — а в необязательные
стадии после извлечения: разложения в ряд, компенсированные схемы, ветвление
домена на десятки коробок. Человек, который первый раз запустил утилиту на своём
файле, не готов ждать четыре минуты и не знает, зачем ждёт.

Свойство, на котором держится вся конструкция: **время режет только КАЧЕСТВО
ответа, но не его состоятельность.** Каждая отсекаемая стадия либо предлагает
нового кандидата, либо уточняет границу вниз. Не успели — останется кандидат
попроще и граница пошире. Граница, которая уже напечатана, от этого не перестаёт
быть верхней, потому что все оценки в проекте корректны сверху независимо друг от
друга.

Поэтому часы здесь мягкие и проверяются между стадиями, а не прерывают вычисление
на середине: половина посчитанной оценки ничем не лучше её отсутствия.
"""
from __future__ import annotations

import os
import time

_deadline = None
_cut = []


def set_budget(seconds):
    """Поставить часы. None или 0 — без ограничения."""
    global _deadline, _cut
    _cut = []
    if seconds is None or seconds <= 0:
        _deadline = None
    else:
        _deadline = time.perf_counter() + float(seconds)
    return _deadline


def clear():
    global _deadline, _cut
    _deadline = None
    _cut = []


def remaining():
    if _deadline is None:
        return float('inf')
    return _deadline - time.perf_counter()


def expired(stage=None):
    """Истекли ли часы. Имя стадии запоминается, чтобы честно сказать, что срезали."""
    if _deadline is None:
        return False
    if time.perf_counter() <= _deadline:
        return False
    if stage and stage not in _cut:
        _cut.append(stage)
    return True


def note(stage):
    """Отметить срезанную стадию вручную."""
    if stage not in _cut:
        _cut.append(stage)


def cut_stages():
    """Что не досчитали — это идёт в отчёт, а не заметается под ковёр."""
    return list(_cut)


def default_seconds():
    """Значение по умолчанию: переменной окружения либо без ограничения."""
    raw = os.environ.get('PARETO_TIME_BUDGET')
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None
