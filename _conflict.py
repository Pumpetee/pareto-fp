# -*- coding: utf-8 -*-
"""Структура, объявленная дважды по-разному, — отказ, а не догадка."""
import io

p = "pareto/cfront.py"
s = io.open(p, encoding="utf-8").read()

# ---------- таблица структур помнит противоречия ----------
helper = '''class StructTable(dict):
    """Структуры проекта плюс список имён, объявленных ПО-РАЗНОМУ дважды.

    Мы не выполняем препроцессор, и это имеет цену. В box2d написано так:

        #if defined( BOX2D_DOUBLE_PRECISION )
        typedef struct b2Pos { double x, y; } b2Pos;
        #else
        typedef struct b2Pos { float x, y; } b2Pos;
        #endif

    Оба объявления лежат в одном файле. Мы читали их подряд и оставляли то, что
    попалось последним, — то есть выбирали точность за компилятора. 07.10.2026
    побитовая сверка показала итог: наш разбор считал разность в двойной
    точности, компилятор в одинарной, и пять функций box2d расходились.

    Молча угадывать здесь нельзя: граница вышла бы верной для программы, которой
    в сборке нет. Поэтому противоречие запоминается, и функция, зависящая от
    такого типа, честно отвергается с указанием имени — человек знает, какой у
    него флаг сборки, и может сказать нам.
    """

    __slots__ = ('conflicts',)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.conflicts = {}


'''
anchor = "def _expand(table, tname, prefix, out, depth=0):"
assert anchor in s
s = s.replace(anchor, helper + anchor, 1)

# ---------- structs() регистрирует противоречия ----------
old = "    out = {}\n    field_re = _field_re(types)"
assert old in s, "начало structs() не найдено"
s = s.replace(old, "    out = StructTable()\n    field_re = _field_re(types)", 1)

old2 = """        if fields:
            out[name] = fields
    for m in _STRUCT_NAMED.finditer(src):"""
assert old2 in s
new2 = """        if fields:
            _record_struct(out, name, fields)
    for m in _STRUCT_NAMED.finditer(src):"""
s = s.replace(old2, new2, 1)

old3 = """        if fields and name not in out:
            out[name] = fields"""
assert old3 in s
new3 = """        if fields:
            _record_struct(out, name, fields)"""
s = s.replace(old3, new3, 1)

record = '''def _record_struct(out, name, fields):
    """Положить структуру, а при РАЗНЫХ объявлениях одного имени — запомнить спор."""
    old = out.get(name)
    if old is not None and old != fields:
        differing = sorted(set(old) ^ set(fields)) or [
            f for f in old if f in fields and old[f] is not fields[f]]
        out.conflicts[name] = differing
    out[name] = fields


'''
s = s.replace("def _record_struct(out", "def _record_struct(out", 1)
anchor2 = "class StructTable(dict):"
s = s.replace(anchor2, record + anchor2, 1)

io.open(p, "w", encoding="utf-8", newline="").write(s)
import ast
ast.parse(s)
print("противоречия структур регистрируются")
