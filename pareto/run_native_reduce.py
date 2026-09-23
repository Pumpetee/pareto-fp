# -*- coding: utf-8 -*-
"""Редукции на нативном коде: clang -O2.

Запуск: python pareto/run_native_reduce.py [набор ...]
"""
from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal, getcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pareto.reductions import SCHEMES, bound, pareto
from pareto.run_reduce import DATASETS, N

from pareto.toolchain import TIMER_C, find_clang, link_flags

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / 'bench'
CLANG = find_clang()
getcontext().prec = 80
REPS = 300

C_SCHEMES = {
    'naive': '''
  double s = 0.0;
  for (int i = 0; i < N; i++) s += A[i];
  return s;''',
    'block4': '''
  double s0=0,s1=0,s2=0,s3=0; int i=0;
  for (; i+3 < N; i+=4) { s0+=A[i]; s1+=A[i+1]; s2+=A[i+2]; s3+=A[i+3]; }
  for (; i < N; i++) s0+=A[i];
  return (s0+s1)+(s2+s3);''',
    'block8': '''
  double s0=0,s1=0,s2=0,s3=0,s4=0,s5=0,s6=0,s7=0; int i=0;
  for (; i+7 < N; i+=8) { s0+=A[i]; s1+=A[i+1]; s2+=A[i+2]; s3+=A[i+3];
                          s4+=A[i+4]; s5+=A[i+5]; s6+=A[i+6]; s7+=A[i+7]; }
  for (; i < N; i++) s0+=A[i];
  return ((s0+s1)+(s2+s3))+((s4+s5)+(s6+s7));''',
    'pairwise': '''
  return rec(0, N);''',
    'kahan': '''
  double s = 0.0, c = 0.0;
  for (int i = 0; i < N; i++) { double y = A[i]-c; double t = s+y; c = (t-s)-y; s = t; }
  return s;''',
    'neumaier': '''
  double s = 0.0, c = 0.0;
  for (int i = 0; i < N; i++) {
    double t = s + A[i];
    if (fabs(s) >= fabs(A[i])) c += (s-t)+A[i]; else c += (A[i]-t)+s;
    s = t;
  }
  return s + c;''',
}

TEMPLATE = r'''// сгенерировано pareto/run_native_reduce.py
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
    // Без этой строки clang доказывает, что f() чистая и данные не меняются,
    // выносит вызов из цикла и считает сумму ОДИН раз: попарная схема
    // показывала ускорение x432, чего физически быть не может.
    // Добавка в 1e-300 на сумму не влияет, но инвариантность ломает.
    A[r & (N - 1)] += 1e-300;
    v += f();
  }
  *ns = now_ns() - t0;
  *guard += v;
  return v;
}

int main(void) {
  // данные приходят бинарно в stdin: путь к файлу передать нельзя —
  // в нём может быть кириллица, а fopen на Windows ждёт ANSI-кодировку
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


def build(name, schemes, data_path):
    funcs = '\n'.join(
        'static double f_%s(void) {%s\n}' % (nm, C_SCHEMES[nm]) for nm in schemes)
    warm = '\n  '.join('for (int w=0; w<3; w++) timed(f_%s, &guard, &ns);' % nm for nm in schemes)
    body = []
    for nm in schemes:
        body.append('double best_%s = 1e30, v_%s = 0;' % (nm, nm))
        body.append('for (int k=0;k<7;k++){ v_%s = timed(f_%s, &guard, &ns); if (ns < best_%s) best_%s = ns; }'
                    % (nm, nm, nm, nm))
        # значение схемы берём ОТДЕЛЬНЫМ вызовом: сумма по повторам с делением
        # смазывает разницу в точности между схемами
        body.append('printf("\\"%s\\": {\\"ns\\": %%.1f, \\"val\\": %%.17g}, ", best_%s, f_%s());'
                    % (nm, nm, nm))
    src = TEMPLATE % {'n': N, 'reps': REPS, 'funcs': funcs, 'warm': warm, 'timer': TIMER_C,
                      'body': '\n  '.join(body)}
    cpath = BENCH / ('native_reduce_' + name + '.c')
    epath = BENCH / ('native_reduce_' + name + '.exe')
    cpath.write_text(src, encoding='utf-8')
    r = subprocess.run([CLANG, str(cpath), '-O2', '-ffp-contract=off', *link_flags(), '-o', str(epath)],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:3000])
    return epath


def run(name):
    import struct
    data = DATASETS[name](N)
    blob = struct.pack('<%dd' % N, *data)

    schemes = list(SCHEMES)
    exe = build(name, schemes, None)
    out = subprocess.run([str(exe)], input=blob, capture_output=True, timeout=900)
    out = subprocess.CompletedProcess(out.args, out.returncode,
                                      out.stdout.decode('utf-8', 'replace'),
                                      out.stderr.decode('utf-8', 'replace'))
    if out.returncode != 0:
        raise RuntimeError(out.stderr[:2000])
    res = json.loads(out.stdout)

    exact_sum = sum((Decimal(x) for x in data), Decimal(0))
    sum_abs = float(sum(abs(x) for x in data))
    rows = []
    for nm in schemes:
        got = Decimal(res[nm]['val'])
        rel = abs(got - exact_sum) / abs(exact_sum) if exact_sum != 0 else abs(got - exact_sum)
        rows.append({'scheme': nm, 'title': SCHEMES[nm]['title'], 'ns': res[nm]['ns'],
                     'rel': rel, 'bound': bound(nm, N, sum_abs) / abs(float(exact_sum))})
    return {'name': name, 'rows': rows, 'front': pareto(N, sum_abs)}


def fmt(d):
    base = next(r for r in d['rows'] if r['scheme'] == 'naive')
    s = ['=' * 78, 'НАБОР: {} · {} элементов · нативный код clang -O2'.format(d['name'], N), '']
    s.append('схема                      время      ускорение   реальная ошибка   граница')
    for r in sorted(d['rows'], key=lambda r: r['ns']):
        s.append('{:<26} {:>7.2f} мс   x{:>5.2f}   {:>13.3e}   {:>9.3e}'.format(
            r['title'], r['ns'] / 1e6, base['ns'] / r['ns'], float(r['rel']), r['bound']))
    fast = min(d['rows'], key=lambda r: r['ns'])
    acc = min(d['rows'], key=lambda r: r['rel'])
    s.append('')
    s.append('БЫЛО  наивная сумма:     {:.2f} мс · ошибка {:.3e}'.format(
        base['ns'] / 1e6, float(base['rel'])))
    s.append('СТАЛО самая быстрая:     {} — {:.2f} мс · ускорение x{:.2f}'.format(
        fast['title'], fast['ns'] / 1e6, base['ns'] / fast['ns']))
    s.append('СТАЛО самая точная:      {} — ошибка {:.3e} · точнее в {:.3g} раз'.format(
        acc['title'], float(acc['rel']),
        float(base['rel'] / acc['rel']) if acc['rel'] > 0 else float('inf')))
    return '\n'.join(s)


if __name__ == '__main__':
    names = sys.argv[1:] or list(DATASETS)
    out = []
    for nm in names:
        d = run(nm)
        out.append(d)
        print(fmt(d), flush=True)
    (BENCH / 'native_reduce_results.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
