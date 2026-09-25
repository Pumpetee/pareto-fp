# -*- coding: utf-8 -*-
"""То же самое, но на нативном коде: clang -O2, замер через QueryPerformanceCounter.

Запуск: python pareto/run_native.py [кейс ...]
"""
from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal, getcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pareto.analysis import pareto_extract, tree_cost
from pareto.codegen import op_count, to_c, to_text
from pareto.egraph import EGraph
from pareto.rules import RULES
from pareto.run import CASES, exact, rel_error

from pareto.toolchain import find_clang, link_flags

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / 'bench'
CLANG = find_clang()
getcontext().prec = 60

C_TEMPLATE = r'''// сгенерировано pareto/run_native.py
#include <stdio.h>
#include <math.h>
#include <windows.h>

#define N %(npts)d
#define REPS %(reps)d

static double %(arrays_decl)s;

static double bench_base(void) {
  double acc = 0.0;
  for (int r = 0; r < REPS; r++)
    for (int i = 0; i < N; i++) { %(loads)s acc += %(base_c)s; }
  return acc;
}
static double bench_opt(void) {
  double acc = 0.0;
  for (int r = 0; r < REPS; r++)
    for (int i = 0; i < N; i++) { %(loads)s acc += %(opt_c)s; }
  return acc;
}
static double bench_acc(void) {
  double acc = 0.0;
  for (int r = 0; r < REPS; r++)
    for (int i = 0; i < N; i++) { %(loads)s acc += %(acc_c)s; }
  return acc;
}

static double run_timed(double (*f)(void), double *guard) {
  LARGE_INTEGER fr, t0, t1;
  QueryPerformanceFrequency(&fr);
  QueryPerformanceCounter(&t0);
  *guard += f();
  QueryPerformanceCounter(&t1);
  return (double)(t1.QuadPart - t0.QuadPart) * 1e9 / (double)fr.QuadPart;
}

int main(int argc, char **argv) {
  // jitter зависит от argc, поэтому компилятор не может свернуть данные
  // в константы и выбросить цикл целиком. Фактически всегда 0.
  double jitter = (argc > 1000) ? 1e-300 : 0.0;
  %(fill)s

  double guard = 0.0;
  for (int w = 0; w < 3; w++) { run_timed(bench_base, &guard); run_timed(bench_opt, &guard); run_timed(bench_acc, &guard); }

  double bb = 1e30, oo = 1e30, aa = 1e30;
  for (int k = 0; k < 9; k++) {
    double a = run_timed(bench_base, &guard);
    double b = run_timed(bench_opt, &guard);
    double c = run_timed(bench_acc, &guard);
    if (a < bb) bb = a;
    if (b < oo) oo = b;
    if (c < aa) aa = c;
  }

  printf("{\"base_ns\": %%.1f, \"opt_ns\": %%.1f, \"acc_ns\": %%.1f, \"guard\": %%.17g, \"vals\": [",
         bb, oo, aa, guard);
  for (int i = 0; i < N; i++) {
    %(loads)s
    printf("%%s{\"base\": %%.17g, \"opt\": %%.17g, \"acc\": %%.17g}",
           i ? ", " : "", %(base_c)s, %(opt_c)s, %(acc_c)s);
  }
  printf("]}\n");
  return 0;
}
'''


def build_c(name, case, base_t, opt_t, acc_t, pts, reps):
    vs = case['vars']
    arrays_decl = ', '.join('A_{v}[{n}]'.format(v=v, n=len(pts)) for v in vs)
    loads = ' '.join('const double {v} = A_{v}[i];'.format(v=v) for v in vs)
    fill = '\n  '.join(
        'for (int i = 0; i < N; i++) A_{v}[i] = ({lo}) + (({hi}) - ({lo})) * (double)i / (double)N + jitter;'.format(
            v=v, lo=repr(pts[0][v]), hi=repr(pts[-1][v] + (pts[-1][v] - pts[0][v]) / (len(pts) - 1)))
        for v in vs)
    src = C_TEMPLATE % {
        'npts': len(pts), 'reps': reps, 'arrays_decl': arrays_decl, 'loads': loads,
        'fill': fill, 'base_c': to_c(base_t), 'opt_c': to_c(opt_t), 'acc_c': to_c(acc_t),
    }
    cpath = BENCH / ('native_' + name + '.c')
    epath = BENCH / ('native_' + name + '.exe')
    cpath.write_text(src, encoding='utf-8')
    r = subprocess.run([CLANG, str(cpath), '-O2', '-ffp-contract=off', *link_flags(), '-o', str(epath)],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:3000])
    return epath


def recompute_points(case, n):
    """Точки ровно те же, что генерирует C: линейная сетка по домену."""
    pts = [case['points'](i, n) for i in range(n)]
    out = []
    for i in range(n):
        p = {}
        for v in case['vars']:
            lo = pts[0][v]
            hi = pts[-1][v] + (pts[-1][v] - pts[0][v]) / (n - 1)
            p[v] = lo + (hi - lo) * i / n
        out.append(p)
    return out


def run_case(name, case, n_points=1024, reps=400):
    expr = case['expr']
    domain = case['domain']
    eg = EGraph()
    root = eg.add_expr(expr)
    eg.saturate(RULES, iters=case.get('iters', 10), domain=case['domain'])
    front, _ = pareto_extract(eg, root, domain, keep=10)
    b_cost, b_err, _, b_work, b_lat = tree_cost(expr, domain)

    budget = case.get('budget', 2.0)
    safe = [p for p in front if p[1] <= b_err * budget]
    pick = min(safe, key=lambda p: (p[0], p[1])) if safe else min(front, key=lambda p: p[1])
    cb = case.get('cost_budget', 1.5)
    afford = [p for p in front if p[0] <= b_cost * cb]
    best_acc = (min(afford, key=lambda p: (p[1], p[0])) if afford
                else min(front, key=lambda p: (p[1], p[0])))

    pts = recompute_points(case, n_points)
    exe = build_c(name, case, expr, pick[2], best_acc[2], pts, reps)
    out = subprocess.run([str(exe)], capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[:2000])
    res = json.loads(out.stdout)

    eb = eo = ea = Decimal(0)
    for p, v in zip(pts, res['vals']):
        env = {k: Decimal(float(val)) for k, val in p.items()}
        ref = exact(expr, env)
        eb = max(eb, rel_error(v['base'], ref))
        eo = max(eo, rel_error(v['opt'], ref))
        ea = max(ea, rel_error(v['acc'], ref))

    return {'name': name,
            'base': {'expr': to_text(expr), 'ops': op_count(expr), 'ns': res['base_ns'], 'err': eb},
            'opt': {'expr': to_text(pick[2]), 'ops': op_count(pick[2]), 'ns': res['opt_ns'], 'err': eo},
            'acc': {'expr': to_text(best_acc[2]), 'ops': op_count(best_acc[2]), 'ns': res['acc_ns'], 'err': ea},
            'speedup': res['base_ns'] / res['opt_ns'] if res['opt_ns'] else float('nan')}


def fmt(d):
    s = ['=' * 76, 'КЕЙС: ' + d['name'] + '   [нативный код, clang -O2]']
    for k, t in (('base', 'БЫЛО '), ('opt', 'СТАЛО'), ('acc', 'ТОЧНО')):
        x = d[k]
        s.append('{}: {}'.format(t, x['expr']))
        s.append('       {} · {:.3f} мс · ошибка {:.3e}'.format(
            x['ops'], x['ns'] / 1e6, float(x['err'])))
    s.append('ИТОГО: ускорение x{:.2f}'.format(d['speedup']))
    if d['base']['err'] > 0 and d['acc']['err'] > 0:
        s.append('       режим точности: ошибка меньше в {:.3g} раз, цена {:.2f}x времени'.format(
            float(d['base']['err'] / d['acc']['err']), d['acc']['ns'] / d['base']['ns']))
    return '\n'.join(s)


if __name__ == '__main__':
    names = sys.argv[1:] or list(CASES)
    out = []
    for nm in names:
        r = run_case(nm, CASES[nm])
        out.append(r)
        print(fmt(r), flush=True)
    (BENCH / 'native_results.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
