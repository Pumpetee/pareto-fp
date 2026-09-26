# -*- coding: utf-8 -*-
"""Обратная запись: переписанная функция возвращается в файл, а не в консоль.

Зачем это отдельный шаг. Пока инструмент печатал тело функции и предлагал
скопировать его руками, он оставался докладчиком: человек читает отчёт, а потом
сам переносит арифметику в свой файл и сам же в этом переносе ошибается. Ровно на
этой границе инструмент перестаёт быть применимым в работе — в сборку его не
поставить, в проверку перед коммитом тоже.

Что здесь происходит. Заменяется РОВНО тело выбранной функции, от `{` до парной
`}`. Всё остальное в файле — комментарии, includes, соседние функции, порядок
объявлений, переводы строк — остаётся дословно на месте, байт в байт. Подпись
функции не трогается вообще, поэтому вызывающий код не меняется и перекомпиляция
не расползается по проекту.

Единственное, что добавляется само, — `#include <math.h>`, и только когда
переписанное тело зовёт функцию оттуда, а прямого include в файле нет. Условие
именно такое, а не «зовёт то, чего в теле не было раньше»: отданный файл обязан
собираться сам, а не при условии, что в исходном уже был нужный заголовок. Если
math.h приходит через чужой include, лишняя строка ничего не сломает — у него есть
защита от повторного включения, — а её отсутствие сломает сборку. Добавленная
строка всегда называется в выводе, а не появляется молча.
"""
from __future__ import annotations

import difflib
import re

# Всё, что умеет печатать codegen и что живёт в math.h.
MATH_CALLS = ('sqrt', 'exp', 'log', 'expm1', 'log1p', 'hypot', 'fma', 'pow')

_MATH_H = re.compile(r'^\s*#\s*include\s*[<"]math\.h[>"]', re.M)
_INCLUDE = re.compile(r'^\s*#\s*include\b.*$', re.M)


def _calls_used(text):
    return sorted({name for name in MATH_CALLS
                   if re.search(r'\b' + name + r'\s*\(', text)})


def _add_math_h(src):
    """Дописать `#include <math.h>` так, чтобы файл остался валидным C.

    Ставим строку после последнего из идущих подряд includes в начале файла — там,
    где её поставил бы человек. Нет ни одного include — сразу за комментарием шапки:
    вставленная перед шапкой строка выглядит как чужая, влезшая поперёд заголовка.
    """
    last = None
    for m in _INCLUDE.finditer(src):
        last = m
        # Идём только по головной группе includes: как только между ними появился
        # код, дальше лезть незачем.
        tail = src[m.end():m.end() + 200]
        if tail.strip() and not tail.lstrip().startswith('#'):
            break
    line = '#include <math.h>'
    if last is not None:
        return src[:last.end()] + '\n' + line + src[last.end():]
    at = _end_of_header_comments(src)
    head = src[:at].rstrip('\n')
    return head + ('\n\n' if head else '') + line + '\n' + src[at:].lstrip('\n')


def _end_of_header_comments(src):
    """Позиция за последним комментарием шапки файла.

    Файлы, которые дают на вход, почти всегда начинаются с комментария о том, что
    внутри. Include нужно ставить за ним, а не перед ним.
    """
    i, n = 0, len(src)
    while i < n:
        j = i
        while j < n and src[j] in ' \t\r\n':
            j += 1
        if src.startswith('/*', j):
            end = src.find('*/', j + 2)
            if end < 0:
                return i
            i = end + 2
        elif src.startswith('//', j):
            end = src.find('\n', j)
            i = n if end < 0 else end + 1
        else:
            return i
    return i


def rewrite_source(src, result, indent='    '):
    """Исходник с переписанным телом функции.

    Возвращает `(новый_текст, заметки)`. Заметки — то, что пришлось сделать помимо
    замены тела, чтобы файл собрался; их печатают человеку, а не прячут.
    """
    from pareto.api import rewritten_c

    span = result.get('body_span')
    if not span:
        raise ValueError('this result carries no position in the source: rewriting back '
                         'needs analyse_c_function on the file text')
    lo, hi = span
    body = rewritten_c(result, indent=indent)
    new = src[:lo] + '{\n' + body + '\n}' + src[hi:]

    notes = []
    used = _calls_used(body)
    if used and not _MATH_H.search(src):
        new = _add_math_h(new)
        notes.append('added #include <math.h>, the file had none and the body calls {}. '
                     'Harmless if the header already arrives through another include'
                     .format(', '.join(used)))
    return new, notes


def unified_diff(old, new, path='file.c'):
    """Обычный unified diff, какой понимают `patch` и любой ревьюер."""
    return ''.join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=str(path), tofile=str(path) + ' (rewritten)', n=3))
