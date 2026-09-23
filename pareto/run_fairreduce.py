# -*- coding: utf-8 -*-
"""Честное сравнение по суммированию массива: схемы против того, что компилятор умеет сам.

Заявленное ускорение x7.9 на суммировании было получено против наивного цикла при
`-O2`. Вопрос, на который отвечает этот скрипт: остаётся ли выигрыш, когда наивному
циклу разрешают векторизоваться (`-O3`) и переставлять сложения (`-ffast-math`).
Ответ важнее самого ускорения: именно его спросят первым.

    python pareto/run_fairreduce.py [набор ...]
"""
from __future__ import annotations

import json
import struct
import subprocess
import sys
from decimal import Decimal, getcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pareto.reductions import SCHEMES, bound
from pareto.run_native_reduce import C_SCHEMES
from pareto.run_reduce import DATASETS, N
from pareto.toolchain import TIMER_C, find_clang, link_flags

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / 'bench'
getcontext().prec = 80
REPS = 300
RUN = dict(capture_output=True, encoding=None)

MODES = [
    ('O2', ['-O2', '-ffp-contract=off']),
    ('O3', ['-O3', '-ffp-contract=off']),
    ('fast', ['-O3', '-ffast-math']),
]

TEMPLATE = r'''// сгенерировано pareto/run_fairreduce.py
#include <stdio.h>
#include <math.h>
%(timer)s
#if defined(_WIN32)
  #include <io.h>
  #include <fcntl.h>
#endif

#define N %(n)d
#define REPS %(reps)d
static double A[N];

static double rec(int lo, int hi) {
  if (hi - lo <= 8) { double s = 0.0; for (int i = lo; i < hi; i++) s += A[i]; return s; }
  int mid = (lo + hi) / 2;
  return rec(lo, mid) + rec(mid, hi);
}

%(funcs)s

static double timed(double (*f)(void), double *guard, double *ns) {
  double t0 = now_ns();
  double v = 0.0;
  for (int r = 0; r < REPS; r++) {
    // без этой добавки компилятор доказывает инвариантность вызова и считает сумму
    // один раз за весь цикл — попарная схема показывала ускорение x432
    A[r & (N - 1)] += 1e-300;
    v += f();
  }
  *ns = now_ns() - t0;
  *guard += v;
  return v;
}

int main(void) {
#if defined(_WIN32)
  _setmode(_fileno(stdin), _O_BINARY);
#endif
  if (fread(A, sizeof(double), N, stdin) != N) { printf("no data\n"); return 1; }

  double guard = 0.0, ns;
  %(warm)s
  printf("{");
  %(body)s
  printf("\"guard\": %%.17g}\n", guard);
  return 0;
}
'''


def build(name, schemes, mode, flags):
    funcs = '\n'.join('static double f_%s(void) {%s\n}' % (nm, C_SCHEMES[nm]) for nm in schemes)
    warm = '\n  '.join('for (int w=0; w<3; w++) timed(f_%s, &guard, &ns);' % nm for nm in schemes)
    body = []
    for nm in schemes:
        body.append('double best_%s = 1e30, v_%s = 0;' % (nm, nm))
        body.append('for (int k=0;k<7;k++){ v_%s = timed(f_%s, &guard, &ns); if (ns < best_%s) best_%s = ns; }'
                    % (nm, nm, nm, nm))
        body.append('printf("\\"%s\\": {\\"ns\\": %%.1f, \\"val\\": %%.17g}, ", best_%s, f_%s());'
                    % (nm, nm, nm))
    src = TEMPLATE % {'n': N, 'reps': REPS, 'funcs': funcs, 'warm': warm,
                      'body': '\n  '.join(body), 'timer': TIMER_C}
    cpath = BENCH / ('fairreduce_{}.c'.format(name))
    cpath.write_text(src, encoding='utf-8')
    exe = BENCH / ('fairreduce_{}_{}{}'.format(name, mode, '.exe' if sys.platform.startswith('win') else ''))
    r = subprocess.run([find_clang(), str(cpath), *flags, *link_flags(), '-o', str(exe)],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode != 0:
        raise RuntimeError('сборка {} {}: {}'.format(name, mode, (r.stderr or '')[:2500]))
    return exe


def run_mode(exe, blob):
    out = subprocess.run([str(exe)], input=blob, capture_output=True, timeout=900)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.decode('utf-8', 'replace')[:2000])
    return json.loads(out.stdout.decode('utf-8', 'replace'))


def run(name):
    data = DATASETS[name](N)
    blob = struct.pack('<%dd' % N, *data)
    exact_sum = sum((Decimal(x) for x in data), Decimal(0))
    sum_abs = float(sum(abs(x) for x in data))
    schemes = list(SCHEMES)

    table = {}
    for mode, flags in MODES:
        res = run_mode(build(name, schemes, mode, flags), blob)
        rows = []
        for nm in schemes:
            got = Decimal(res[nm]['val'])
            rel = abs(got - exact_sum) / abs(exact_sum) if exact_sum != 0 else abs(got - exact_sum)
            rows.append({'scheme': nm, 'title': SCHEMES[nm]['title'], 'ns': res[nm]['ns'],
                         'rel': rel, 'bound': bound(nm, N, sum_abs) / abs(float(exact_sum))})
        table[mode] = rows
    return {'name': name, 'table': table}


def fmt(d):
    s = ['=' * 96, 'НАБОР: {} · {} элементов'.format(d['name'], N), '']
    s.append('{:<26} {:>22} {:>22} {:>22}'.format('схема', '-O2', '-O3', '-O3 -ffast-math'))
    for i, nm in enumerate(SCHEMES):
        cells = []
        for mode, _ in MODES:
            r = next(x for x in d['table'][mode] if x['scheme'] == nm)
            cells.append('{:>8.2f} мс {:>9.2e}'.format(r['ns'] / 1e6, float(r['rel'])))
        title = next(x for x in d['table']['O2'] if x['scheme'] == nm)['title']
        s.append('{:<26} {} {} {}'.format(title, *cells))

    naive_o2 = next(x for x in d['table']['O2'] if x['scheme'] == 'naive')
    naive_best = min(next(x for x in d['table'][m] if x['scheme'] == 'naive')['ns'] for m, _ in MODES)
    ours_best = min(min(x['ns'] for x in d['table'][m] if x['scheme'] != 'naive') for m, _ in MODES)
    naive_fast = next(x for x in d['table']['fast'] if x['scheme'] == 'naive')
    best_row = min((x for m, _ in MODES for x in d['table'][m] if x['scheme'] != 'naive'),
                   key=lambda x: x['ns'])
    s += ['',
          'наивная при -O2           : {:.2f} мс · ошибка {:.3e}'.format(naive_o2['ns'] / 1e6, float(naive_o2['rel'])),
          'наивная, лучший режим     : {:.2f} мс · ошибка {:.3e} (fast-math)'.format(
              naive_fast['ns'] / 1e6, float(naive_fast['rel'])),
          'лучшая наша схема         : {:.2f} мс · ошибка {:.3e} ({})'.format(
              best_row['ns'] / 1e6, float(best_row['rel']), best_row['title']),
          '',
          'против наивной при -O2    : x{:.2f}'.format(naive_o2['ns'] / ours_best),
          'против лучшего у clang    : x{:.2f}   <- главная цифра'.format(naive_best / ours_best),
          'выигрыш по точности       : x{:.3g}'.format(
              float(naive_fast['rel'] / best_row['rel']) if best_row['rel'] else float('inf'))]
    return '\n'.join(s)


if __name__ == '__main__':
    names = sys.argv[1:] or list(DATASETS)
    out = []
    for nm in names:
        r = run(nm)
        out.append(r)
        print(fmt(r), flush=True)
    (BENCH / 'fairreduce_results.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    (BENCH / 'fairreduce_report.txt').write_text('\n'.join(fmt(r) for r in out), encoding='utf-8')
    print('\nотчёт: bench/fairreduce_report.txt · машинно: bench/fairreduce_results.json')
