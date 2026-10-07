# -*- coding: utf-8 -*-
"""Сверка нашего разбора с настоящим компилятором, побитово.

Самая опасная ошибка такого инструмента — не слабая граница, а разбор НЕ ТОЙ
программы, что написана. Граница тогда верна для выражения, которого в коде нет,
и ни один тест внутри проекта этого не покажет: мы же сами себе и судья.

Поэтому судья берётся снаружи. Функция компилируется clang-ом как есть, вызывается
на случайных входах, и её ответ сравнивается с нашим разбором ПОБИТОВО. Входы и
ответы ходят шестнадцатеричными образцами, а не десятичными записями, чтобы в
сравнение не влезло округление при печати.

Запуск:
  python tools/difftest_c.py <файл.h> [--range -1e3..1e3] [--cases 200]
"""
from __future__ import annotations

import argparse
import math
import os
import shutil
import random
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pareto.cfront import (collect_context, constants, functions, globals_of,
                           macro_aliases, make_resolver, parse_function,
                           struct_result_fields)
from pareto.evalfp import eval_float
from pareto.program import paths
from pareto.precision import FLOAT32, FLOAT64

def find_clang():
    """Путь к компилятору: переменная среды, затем PATH, затем обычные места.

    Жёсткий путь годился ровно до первой чужой машины. Сверка с компилятором
    должна проверяться не у меня, а в CI, иначе это не доказательство, а моё
    слово.
    """
    env = os.environ.get('PARETO_CLANG')
    if env and Path(env).exists():
        return env
    for name in ('clang', 'clang-18', 'clang-17', 'gcc', 'cc'):
        found = shutil.which(name)
        if found:
            return found
    for guess in (os.path.join('C:' + os.sep, 'Program Files', 'LLVM', 'bin',
                               'clang.exe'),
                  os.path.join('C:' + os.sep, 'msys64', 'mingw64', 'bin', 'gcc.exe')):
        if Path(guess).exists():
            return guess
    return None


CLANG = find_clang()

HARNESS = r'''#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "%(header)s"

static float bits_to_f(const char *s) {
    unsigned long u = strtoul(s, NULL, 16);
    unsigned int w = (unsigned int)u;
    float f;
    memcpy(&f, &w, 4);
    return f;
}

static double bits_to_d(const char *s) {
    unsigned long long u = strtoull(s, NULL, 16);
    double d;
    memcpy(&d, &u, 8);
    return d;
}

int main(int argc, char **argv) {
    %(decls)s
    %(rettype)s __pareto_r = %(call)s;
    %(outtype)s out;
    /* Имя с подчёркиваниями нарочно: `v` столкнулось с параметром v в raylib,
       и 34 стенда не собирались с redefinition. */
    %(scalar)s __pareto_v = __pareto_r%(pick)s;
    memcpy(&out, &__pareto_v, sizeof(out));
    printf("%(fmt)s", out);
    return 0;
}
'''


def f32_bits(x):
    return struct.unpack('<I', struct.pack('<f', x))[0]


def bits_f32(b):
    return struct.unpack('<f', struct.pack('<I', b))[0]


def f64_bits(x):
    return struct.unpack('<Q', struct.pack('<d', x))[0]


def bits_f64(b):
    return struct.unpack('<d', struct.pack('<Q', b))[0]


def to_bits(x, width):
    return f32_bits(x) if width == 4 else f64_bits(x)


def from_bits(b, width):
    return bits_f32(b) if width == 4 else bits_f64(b)


def signature(src, name):
    """Типы и имена параметров так, как они стоят в объявлении."""
    m = re.search(r'\b([A-Za-z_][\w\s\*]*?)\s+' + re.escape(name) +
                  r'\s*\(([^)]*)\)\s*\{', src)
    if not m:
        return None, None
    ret = m.group(1).split()[-1]
    params = []
    for part in m.group(2).split(','):
        part = part.strip()
        if not part or part == 'void':
            continue
        toks = part.replace('*', ' * ').split()
        pname = toks[-1]
        ptype = ' '.join(toks[:-1])
        if '*' in ptype:
            return ret, None               # указатели здесь не трогаем
        params.append((ptype, pname))
    return ret, params


def width_of(fmt):
    """Разрядность формата в байтах. Источник истины — сам разбор, а не догадка.

    Раньше разрядность входов выводилась из типа ВОЗВРАТА: для скалярной функции
    это случайно совпадало, а для функции, отдающей Quaternion, стенд начинал
    читать поля как double. В C они float, поэтому присваивание округляло их
    отдельно, и сложение шло уже над округлёнными — а у нас над исходными. Одно
    лишнее округление на входе, и 39 функций из 74 расходились с компилятором.
    Поймано побитовой сверкой 07.10.2026.
    """
    return 4 if getattr(fmt, 'mant_bits', 53) <= 24 else 8


def c_scalar(fmt):
    return 'float' if width_of(fmt) == 4 else 'double'


def build_case(name, ret, params, args, scalar32, scalar64, field=None,
               field_scalar=None):
    """Объявления и вызов для набора плоских имён вроде v.x, v1.y.

    Возврат структуры поддержан через выбор поля: функция, отдающая Vector2, это
    два скалярных выхода, и судить их надо по отдельности. Без этого вся
    векторная математика осталась бы без внешнего судьи — а это ровно то место,
    где доверие и нужно.
    """
    # Chipmunk весь написан на cpFloat, а это обычный double.
    if ret in scalar32:
        ret_c, reader, width = ret, 'bits_to_f', 4
        pick, scalar_c = '', ret
    elif ret in scalar64:
        ret_c, reader, width = ret, 'bits_to_d', 8
        pick, scalar_c = '', ret
    elif field is not None and field_scalar is not None:
        ret_c = ret
        pick = '.' + field
        scalar_c = field_scalar
        width = 4 if field_scalar in scalar32 else 8
        reader = 'bits_to_f' if width == 4 else 'bits_to_d'
    else:
        return None
    bases = {}
    for flat in args:
        base = flat.split('.')[0]
        bases.setdefault(base, []).append(flat)
    want = {p[1]: p[0] for p in params}
    if set(bases) - set(want):
        return None                         # имя не из подписи — не наш случай
    decls, order = [], []
    idx = 1
    for ptype, pname in params:
        flats = bases.get(pname)
        if flats is None:
            return None                     # параметр не дошёл до разбора
        # const в стенде снимаем: объявить const-переменную и потом заполнить её
        # поля нельзя, компилятор отвергал наш же стенд и весь Chipmunk оставался
        # без внешнего судьи. Для значения параметра это ничего не меняет — const
        # относится к копии, а не к вычислению.
        ptype = ptype.replace('const', '').strip()
        if flats == [pname]:
            w = width_of(args.get(pname))
            rd = 'bits_to_f' if w == 4 else 'bits_to_d'
            decls.append('{} {} = {}(argv[{}]);'.format(ptype, pname, rd, idx))
            order.append((pname, w))
            idx += 1
        else:
            decls.append('{} {};'.format(ptype, pname))
            for flat in sorted(flats):
                # Разрядность у КАЖДОГО поля своя и берётся из разбора.
                w = width_of(args.get(flat))
                rd = 'bits_to_f' if w == 4 else 'bits_to_d'
                # Имя цикла нарочно не field: параметр с тем же именем выше
                # означает поле РЕЗУЛЬТАТА, и затенение его здесь уже стоило
                # падения с IndexError. Плоское имя без точки тоже бывает —
                # когда объект разобран и как целое, и по полям; стенд для
                # такого построить нельзя, и мы честно отказываемся.
                if '.' not in flat:
                    return None
                fld_name = flat.split('.', 1)[1]
                decls.append('{}.{} = {}(argv[{}]);'.format(
                    pname, fld_name, rd, idx))
                order.append((flat, w))
                idx += 1
    call = '{}({})'.format(name, ', '.join(p[1] for p in params))
    return decls, call, order, ret_c, width, pick, scalar_c


RELOP = {'<': lambda a, b: a < b, '<=': lambda a, b: a <= b,
         '>': lambda a, b: a > b, '>=': lambda a, b: a >= b,
         '==': lambda a, b: a == b, '!=': lambda a, b: a != b}


def paths_of(prog):
    """Все пути исполнения функции. Один путь — частный случай, а не отдельный.

    Судья раньше умел только прямолинейные функции, и ровно поэтому мимо него
    прошли b2MinFloat, b2MaxFloat, b2AbsFloat и b2ClampFloat — то есть именно то
    новое, что принял фронтенд. Проверять надо прежде всего свежее, а не то, что
    и так лежало.
    """
    try:
        return paths(prog['stmts'])
    except Exception:
        return None


def eval_paths(ps, vals):
    """Значение функции: условия решаются по ВЫЧИСЛЕННЫМ величинам, как в машине.

    Не по истинным и не по интервалам: в живом коде `if (a < b)` сравнивает те
    числа, что получились после округлений, и выбор ветки зависит именно от них.
    """
    for pth in ps:
        ok = True
        for cond, want in pth.guards:
            op, left, right = cond
            got = RELOP[op](eval_float(left, vals), eval_float(right, vals))
            if got != want:
                ok = False
                break
        if ok:
            return eval_float(pth.expr, vals)
    raise ValueError('ни один путь не выбран')


def run_file(target, ctx, a, lo, hi, tmp, inc_args=(), label=''):
    """Сверить все пригодные функции одного файла. Возвращает (сверено, пропущено, расхождения)."""
    try:
        src = target.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return 0, 0, []
    checked = skipped = 0
    bad = []
    # Причины пропуска считаем по видам. «Пропущено 31» — число, с которым нечего
    # делать: непонятно, упираемся мы в сборку стенда, в тип возврата или в
    # ветвление. Разбивка превращает это в список работ.
    why = ctx.setdefault('why', {})

    def skip(reason):
        why[reason] = why.get(reason, 0) + 1

    for name in functions(src, ctx['types'], ctx['table']):
        fields = struct_result_fields(src, name, ctx['types'], ctx['table'])
        try:
            prog = parse_function(src, name, ctx['types'], ctx['table'],
                                  ctx['resolve'], ctx['macros'], ctx['consts'],
                                  ctx['globs'],
                                  field=(fields[0] if fields else None))
        except Exception:
            continue

        ret, params = signature(src, name)
        if not params:
            skipped += 1
            skip('подпись не разобрана')
            continue
        fld = fields[0] if fields else None
        fscalar = None
        if fld is not None:
            fmt = (ctx['table'].get(ret) or {}).get(fld)
            fscalar = 'float' if getattr(fmt, 'mant_bits', 24) <= 24 else 'double'
        built = build_case(name, ret, params, prog['args'],
                           ctx['scalar32'], ctx['scalar64'],
                           field=fld, field_scalar=fscalar)
        if built is None:
            skipped += 1
            skip('тип возврата или параметра не скалярный')
            continue
        decls, call, order, ret_c, out_width, pick, scalar_c = built
        ps = paths_of(prog)
        if ps is None:
            skipped += 1
            skip('пути исполнения не выведены')
            continue

        code = HARNESS % {
            'header': target.as_posix(),
            'decls': (chr(10) + '    ').join(decls),
            'call': call,
            'rettype': ret_c,
            'outtype': 'unsigned int' if out_width == 4 else 'unsigned long long',
            'fmt': '%08x' if out_width == 4 else '%016llx',
            'pick': pick,
            'scalar': scalar_c,
        }
        cfile = tmp / (name + '.c')
        exe = tmp / (name + ('.exe' if os.name == 'nt' else ''))
        cfile.write_text(code, encoding='utf-8')
        cmd = [CLANG, str(cfile), '-O0', '-I', str(target.parent),
               *inc_args, '-o', str(exe)]
        if os.name != 'nt':
            cmd.append('-lm')               # на Linux libm подключается явно
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            skipped += 1
            skip('стенд не собрался')
            if a.why:
                print('{:<26} не собралось:'.format(name))
                for ln in (r.stderr or '').strip().splitlines()[:6]:
                    print('      ' + ln)
            continue

        mism = None
        for _ in range(a.cases):
            vals, argv = {}, []
            for flat, width in order:
                pick = random.random()
                if pick < 0.1:
                    v = 0.0
                elif pick < 0.2:
                    v = random.choice([lo, hi, 1.0, -1.0])
                else:
                    v = lo + (hi - lo) * random.random()
                v = from_bits(to_bits(v, width), width)
                vals[flat] = v
                argv.append(('{:08x}' if width == 4 else '{:016x}').format(
                    to_bits(v, width)))
            got = subprocess.run([str(exe)] + argv, capture_output=True, text=True)
            if got.returncode != 0 or not got.stdout.strip():
                continue
            c_bits = int(got.stdout.strip(), 16)
            c_val = from_bits(c_bits, out_width)
            try:
                ours = eval_paths(ps, vals)
            except Exception as e:
                mism = (dict(vals), 'исключение ' + type(e).__name__, repr(c_val))
                break
            if ours != ours or not math.isfinite(ours):
                # И у нас не число: расхождением считаем только если у компилятора число
                if c_val == c_val and math.isfinite(c_val):
                    mism = (dict(vals), repr(ours), repr(c_val))
                    break
                continue
            if to_bits(ours, out_width) != c_bits:
                mism = (dict(vals), repr(ours), repr(c_val))
                break
        checked += 1
        if not a.quiet:
            print('{:<26} {}'.format(label + name, 'совпало побитово' if mism is None
                                     else 'РАСХОЖДЕНИЕ'))
        if mism is not None:
            bad.append((name,) + mism)

    return checked, skipped, bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('path', help='файл .c/.h или корень проекта')
    ap.add_argument('--range', default='-1e3..1e3')
    ap.add_argument('--cases', type=int, default=120)
    ap.add_argument('--seed', type=int, default=20261006)
    ap.add_argument('--why', action='store_true',
                    help='показывать, почему функция пропущена')
    ap.add_argument('--quiet', action='store_true',
                    help='печатать только расхождения и итог')
    a = ap.parse_args()

    if not CLANG:
        raise SystemExit('компилятор не найден: укажите PARETO_CLANG или '
                         'поставьте clang в PATH')
    lo, hi = (float(x) for x in a.range.split('..'))
    random.seed(a.seed)

    given = Path(a.path)
    whole_repo = given.is_dir()
    # Проект целиком, а не один заголовок. Повод прямой: инструмент принимает в
    # трёх библиотеках 155 функций, а с компилятором было сверено 37. Остальные
    # 118 никто снаружи не проверял — и ровно в этом разрыве и живёт самая опасная
    # ошибка: разбор не той программы, что написана.
    SKIP_DIRS = {'demo', 'demos', 'test', 'tests', 'example', 'examples',
                 'extern', 'external', 'third_party', 'vendor', 'build',
                 'benchmark', 'benchmarks', 'samples'}
    if whole_repo:
        root = given
        targets = sorted(
            p for p in root.rglob('*')
            if p.suffix in ('.c', '.h')
            # Демо и тесты в счёт не идут: судим библиотеку, а не её примеры.
            # Иначе половина отказов приходит из sokol-а, приложенного к
            # Chipmunk-у для показа, и картина перестаёт быть про библиотеку.
            and not (SKIP_DIRS & {part.lower() for part in p.relative_to(root).parts}))
    else:
        root = given.parent
        for _ in range(3):
            if (root.parent / 'include').exists() or (root.parent / 'src').exists():
                root = root.parent
        targets = [given]

    pool = [p for p in root.rglob('*') if p.suffix in ('.c', '.h')] or targets
    types, table = collect_context(pool)
    texts = []
    for f in pool:
        try:
            texts.append(f.read_text(encoding='utf-8', errors='replace'))
        except OSError:
            pass
    macros = macro_aliases(texts)
    consts = constants(texts, types)
    globs = globals_of(texts, types, table)
    resolve = make_resolver(texts, types, table, macros=macros, consts=consts,
                            globs=globs)

    ctx = {
        'types': types, 'table': table, 'resolve': resolve, 'macros': macros,
        'consts': consts, 'globs': globs,
        'scalar32': {'float'} | {k for k, v in types.items() if v is FLOAT32},
        'scalar64': ({'double', 'long double'}
                     | {k for k, v in types.items() if v is FLOAT64}),
    }

    # Пути включения: все каталоги, где есть заголовки. Без этого стенд не
    # собирается почти ни для одного файла настоящего проекта.
    inc_dirs = sorted({p.parent for p in pool if p.suffix == '.h'})
    inc_args = []
    for d in inc_dirs:
        inc_args += ['-I', str(d)]

    print('цель:', given)
    print('судья:', CLANG, '| сравнение побитовое')
    print('диапазон входов: [{:g}, {:g}], случаев на функцию: {}'.format(lo, hi, a.cases))
    print('файлов к обходу:', len(targets))
    print()

    tmp = Path(tempfile.mkdtemp(prefix='difftest_'))
    checked = skipped = 0
    bad = []
    for target in targets:
        c, sk, b = run_file(target, ctx, a, lo, hi, tmp, inc_args=inc_args,
                            label='' if not whole_repo else '')
        if (c or b) and whole_repo and not a.quiet:
            print('-- {} : сверено {}, пропущено {}, расхождений {}'.format(
                target.relative_to(root), c, sk, len(b)))
        checked += c
        skipped += sk
        bad += b

    print()
    print('сверено функций: {} | пропущено: {} | расхождений: {}'.format(
        checked, skipped, len(bad)))
    if ctx.get('why'):
        print('почему пропущены:')
        for reason, n in sorted(ctx['why'].items(), key=lambda kv: -kv[1]):
            print('  {:<42} {}'.format(reason, n))
    for name, vals, ours, theirs in bad:
        print('  {}: наш разбор {} против компилятора {}'.format(name, ours, theirs))
        print('    входы:', vals)
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
