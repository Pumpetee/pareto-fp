# -*- coding: utf-8 -*-
"""Печать выражения в MLIR (диалекты func, arith, math).

Зачем: MLIR — общий промежуточный слой почти всех компиляторов поверх LLVM.
Если наша переписанная форма выражается там, результат достаётся любому языку,
а не только C. Это первый шаг переноса: генерация корректного модуля и прогон
его через штатный конвейер mlir-opt → mlir-translate → llc/clang.

    python -m pareto.to_mlir "x*x - y*y" --name sq_diff
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pareto.parser import parse, variables

BIN_OPS = {'+': 'arith.addf', '-': 'arith.subf', '*': 'arith.mulf', '/': 'arith.divf'}
FUN_OPS = {'sqrt': 'math.sqrt', 'exp': 'math.exp', 'log': 'math.log'}


class _Emitter:
    def __init__(self, ty='f64'):
        self.ty = ty
        self.lines = []
        self.n = 0

    def tmp(self):
        self.n += 1
        return '%v{}'.format(self.n)

    def emit(self, tree, env):
        op = tree[0]
        if op == 'var':
            return env[tree[1]]
        if op == 'num':
            out = self.tmp()
            # константа печатается с плавающей точкой, иначе arith.constant не примет тип
            self.lines.append('    {} = arith.constant {:.17e} : {}'.format(out, float(tree[1]), self.ty))
            return out
        if op == 'neg':
            inner = self.emit(tree[1], env)
            out = self.tmp()
            self.lines.append('    {} = arith.negf {} : {}'.format(out, inner, self.ty))
            return out
        if op in FUN_OPS:
            inner = self.emit(tree[1], env)
            out = self.tmp()
            self.lines.append('    {} = {} {} : {}'.format(out, FUN_OPS[op], inner, self.ty))
            return out
        if op == 'fma':
            a, b, c = (self.emit(k, env) for k in tree[1:])
            out = self.tmp()
            self.lines.append('    {} = math.fma {}, {}, {} : {}'.format(out, a, b, c, self.ty))
            return out
        if op in BIN_OPS:
            a = self.emit(tree[1], env)
            b = self.emit(tree[2], env)
            out = self.tmp()
            self.lines.append('    {} = {} {}, {} : {}'.format(out, BIN_OPS[op], a, b, self.ty))
            return out
        raise ValueError('в MLIR не умею операцию: ' + op)


def to_mlir(tree, name='f', ty='f64', vars_=None):
    """Готовый модуль с одной функцией.

    `vars_` задаёт ПОРЯДОК аргументов явно. Это не косметика: переписанная форма
    часто упоминает переменные в другом порядке (`(b + a) / c` вместо `a/c + b/c`),
    и если сигнатуру выводить из неё самой, вызывающий код подаст аргументы не туда.
    Именно так у нас ошибка на two_div выскочила до 6.99 вместо 1e-16.
    """
    vs = list(vars_) if vars_ else variables(tree)
    missing = [v for v in variables(tree) if v not in vs]
    if missing:
        raise ValueError('в списке аргументов нет переменных: ' + ', '.join(missing))
    env = {v: '%arg{}'.format(i) for i, v in enumerate(vs)}
    em = _Emitter(ty)
    res = em.emit(tree, env)
    args = ', '.join('{}: {}'.format(env[v], ty) for v in vs)
    head = '  func.func @{}({}) -> {} {{'.format(name, args, ty)
    tail = '    return {} : {}\n  }}'.format(res, ty)
    return '\n'.join(['module {', head, *em.lines, tail, '}', ''])


def main(argv=None):
    ap = argparse.ArgumentParser(prog='pareto.to_mlir',
                                 description='Печатает формулу как модуль MLIR (func + arith + math).')
    ap.add_argument('expr')
    ap.add_argument('--name', default='f', help='имя функции в модуле')
    ap.add_argument('--type', default='f64', choices=['f32', 'f64'])
    ap.add_argument('--out', help='файл вместо вывода в консоль')
    a = ap.parse_args(argv)
    text = to_mlir(parse(a.expr), a.name, a.type)
    if a.out:
        Path(a.out).write_text(text, encoding='utf-8')
        print('записано: ' + a.out)
    else:
        print(text, end='')
    return 0


if __name__ == '__main__':
    sys.exit(main())
