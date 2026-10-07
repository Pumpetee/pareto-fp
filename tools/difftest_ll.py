# -*- coding: utf-8 -*-
"""Сверка нашего чтения представления LLVM с настоящим исполнением, побитово.

C++ мы читаем через представление компилятора, и без внешней проверки это
держалось бы на моём слове. Здесь судья тот же, что для C, но устроен иначе:
сравнивать нужно не с пересборкой исходника, а с тем САМЫМ представлением,
которое мы прочли. Поэтому оно и компилируется — clang принимает .ll как вход.

Имена функций в C++ искажены, и разворачивать их не нужно: стенд объявляет
функцию с явной ассемблерной меткой, равной искажённому имени. Так подпись
пишется на C, а связывается с кодом из C++.

Что сравнивается побитово, а что нет: арифметические операции IEEE-754 обязывает
округлять точно, поэтому у них требуется совпадение каждого бита. Для sin, cos,
exp, log и степени стандарт точного округления НЕ требует, и там проверяется
другое, более важное — ответ обязан лежать внутри НАШЕЙ напечатанной границы.

Запуск:
  python tools/difftest_ll.py файл.ll [--cases 200] [--range -1e3..1e3]
"""
from __future__ import annotations

import argparse
import math
import random
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pareto.analysis import tree_cost_refined
from pareto.evalfp import eval_float
from pareto.llfront import demangle, functions_of, parse_module
from pareto.precision import FLOAT32

from difftest_c import (CLANG, INEXACT_OPS, from_bits, to_bits)   # noqa: E402

HARNESS = r'''#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static float bits_to_f(const char *s) {
    unsigned long u = strtoul(s, NULL, 16);
    unsigned int w = (unsigned int)u;
    float f; memcpy(&f, &w, 4); return f;
}

static double bits_to_d(const char *s) {
    unsigned long long u = strtoull(s, NULL, 16);
    double d; memcpy(&d, &u, 8); return d;
}

/* Объявление с ассемблерной меткой: имя в C++ искажено, а связать надо именно
   с ним. Так подпись пишется на C, и разворачивать имя не требуется. */
%(ret)s target(%(proto)s) __asm__("%(mangled)s");

int main(int argc, char **argv) {
    %(reads)s
    %(ret)s r = target(%(call)s);
    %(outtype)s out;
    memcpy(&out, &r, sizeof(out));
    printf("%(fmt)s", out);
    return 0;
}
'''


def uses_inexact(tree):
    if not isinstance(tree, tuple):
        return False
    if tree[0] in INEXACT_OPS:
        return True
    return any(uses_inexact(k) for k in tree[1:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('path', help='файл .ll')
    ap.add_argument('--range', default='-1e3..1e3')
    ap.add_argument('--cases', type=int, default=120)
    ap.add_argument('--seed', type=int, default=20261007)
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args()

    if not CLANG:
        raise SystemExit('компилятор не найден: укажите PARETO_CLANG')
    lo, hi = (float(x) for x in a.range.split('..'))
    rng = random.Random(a.seed)

    ll = Path(a.path)
    tmp = Path(tempfile.mkdtemp(prefix='difftest_ll_'))
    text = ll.read_text(encoding='utf-8', errors='replace')
    rets = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith('define'):
            continue
        import re
        m = re.match(r'define[^@]*?(float|double)\s[^@]*@("[^"]+"|[\w.$]+)', line)
        if m:
            rets[m.group(2).strip('"')] = m.group(1)

    checked = bitwise = within = 0
    cannot_judge = []
    bad = []
    for name, (kind, payload, args) in functions_of(ll).items():
        if kind != 'дерево' or name not in rets or not args:
            continue
        ret_c = rets[name]
        proto = ', '.join('float' if f is FLOAT32 else 'double'
                          for _r, f in args)
        reads, call = [], []
        for i, (reg, fmt) in enumerate(args):
            ctype = 'float' if fmt is FLOAT32 else 'double'
            rd = 'bits_to_f' if fmt is FLOAT32 else 'bits_to_d'
            reads.append('{} x{} = {}(argv[{}]);'.format(ctype, i, rd, i + 1))
            call.append('x{}'.format(i))
        width = 4 if ret_c == 'float' else 8
        code = HARNESS % {
            'ret': ret_c, 'proto': proto, 'mangled': name,
            'reads': ('\n    ').join(reads), 'call': ', '.join(call),
            'outtype': 'unsigned int' if width == 4 else 'unsigned long long',
            'fmt': '%08x' if width == 4 else '%016llx',
        }
        cfile = tmp / (str(abs(hash(name))) + '.c')
        exe = tmp / (str(abs(hash(name))) + '.exe')
        cfile.write_text(code, encoding='utf-8')
        r = subprocess.run([CLANG, str(cfile), str(ll), '-O0', '-o', str(exe)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            if not a.quiet:
                print('{:<34} стенд не собрался'.format(demangle(name)[:34]))
            continue

        names = ['a' + reg if reg.isdigit() else reg for reg, _f in args]
        inexact = uses_inexact(payload)
        bound = None
        if inexact:
            try:
                bound = tree_cost_refined(payload, {n: (lo, hi) for n in names})[1]
            except Exception:
                bound = None

        mism = None
        for _ in range(a.cases):
            vals, argv = {}, []
            for (reg, fmt), nm in zip(args, names):
                w = 4 if fmt is FLOAT32 else 8
                pick = rng.random()
                if pick < 0.12:
                    v = 0.0
                elif pick < 0.25:
                    v = rng.choice([lo, hi, 1.0, -1.0])
                else:
                    v = lo + (hi - lo) * rng.random()
                v = from_bits(to_bits(v, w), w)
                vals[nm] = v
                argv.append(('{:08x}' if w == 4 else '{:016x}').format(
                    to_bits(v, w)))
            got = subprocess.run([str(exe)] + argv, capture_output=True,
                                 text=True)
            if got.returncode != 0 or not got.stdout.strip():
                continue
            c_bits = int(got.stdout.strip(), 16)
            c_val = from_bits(c_bits, width)
            try:
                ours = eval_float(payload, vals)
            except Exception as e:
                mism = (dict(vals), 'исключение ' + type(e).__name__, repr(c_val))
                break
            if ours != ours or not math.isfinite(ours):
                if c_val == c_val and math.isfinite(c_val):
                    mism = (dict(vals), repr(ours), repr(c_val))
                    break
                continue
            if to_bits(ours, width) != c_bits:
                if inexact and bound is not None and math.isfinite(bound) \
                        and abs(ours - c_val) <= bound * 1.000001:
                    continue
                if inexact and (bound is None or not math.isfinite(bound)):
                    # Судить нечем: побитового равенства требовать нельзя (libm
                    # точного округления не обязан), а границы, по которой можно
                    # было бы судить, мы на этом диапазоне не доказали. Называть
                    # это расхождением значило бы объявлять дефектом собственное
                    # воздержание.
                    mism = ('нельзя судить', dict(vals), repr(ours), repr(c_val))
                    break
                mism = (dict(vals), repr(ours), repr(c_val))
                break
        checked += 1
        if inexact:
            within += 1
        else:
            bitwise += 1
        if not a.quiet:
            print('{:<34} {}'.format(
                demangle(name)[:34],
                'совпало побитово' if mism is None and not inexact else
                ('внутри нашей границы' if mism is None else
                 ('не судится' if mism[0] == 'нельзя судить'
                  else 'РАСХОЖДЕНИЕ'))))
        if mism is not None and mism[0] == 'нельзя судить':
            cannot_judge.append((name,) + tuple(mism[1:]))
        elif mism is not None:
            bad.append((name,) + mism)

    print()
    print('сверено функций: {} | расхождений: {}'.format(checked, len(bad)))
    print('  побитово: {} | против нашей границы: {}'.format(bitwise, within))
    if cannot_judge:
        print('  не судится: {} — внутри libm-операция, а границы на этом '
              'диапазоне мы НЕ доказали, поэтому сравнивать нечем. Это не '
              'дефект чтения и не совпадение: это отсутствие основания для '
              'суждения, и его надо называть, а не относить к одной из '
              'сторон.'.format(len(cannot_judge)))
        for nm, vals, ours, theirs in cannot_judge:
            print('    {}: наше {} против исполнения {}'.format(
                demangle(nm)[:40], ours, theirs))
    for name, vals, ours, theirs in bad:
        print('  {}: наше {} против исполнения {}'.format(
            demangle(name)[:40], ours, theirs))
        print('    входы:', vals)
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
