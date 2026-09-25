# -*- coding: utf-8 -*-
"""Честное сравнение: наш выбор против того, что компилятор умеет сам.

Вопрос, на который отвечает скрипт: даёт ли наш проход что-то сверх `-O3` и
`-ffast-math`, и какой ценой по точности. Без этой таблицы разговор в
компиляторном сообществе не начинается — там первым делом спрашивают именно её.

Одна и та же программа компилируется тремя наборами флагов, поэтому времена
сравнимы между собой, а ошибка считается против точного значения в 60 цифр.

Запуск: python pareto/run_fairbench.py [кейс ...]
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
from pareto.run_native import recompute_points
from pareto.toolchain import TIMER_C, find_clang, link_flags

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / 'bench'
CLANG = find_clang()
getcontext().prec = 60

# Режимы сборки. `-ffp-contract=off` в первых двух — чтобы clang не подменял
# a*b+c на fma втихую: это меняет и скорость, и ошибку, и сравнение поедет.
MODES = [
    ('O2', ['-O2', '-ffp-contract=off']),
    ('O3', ['-O3', '-ffp-contract=off']),
    # Без разрешения на аппаратную FMA (её нет в базовом x86-64) вызов fma() уходит
    # в libm — корректно округляемая, но дорогая функция. Формы с fma в этом режиме
    # проигрывают в разы, и без отдельной строки в таблице это выглядит как дефект.
    ('fma', ['-O3', '-ffp-contract=off', '-mfma']),
    ('fast', ['-O3', '-ffast-math']),
]

C_TEMPLATE = r'''// сгенерировано pareto/run_fairbench.py
#include <stdio.h>
#include <math.h>
%(timer)s

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

static double run_timed(double (*f)(void), double *guard) {
  double t0 = now_ns();
  *guard += f();
  return now_ns() - t0;
}

int main(int argc, char **argv) {
  double jitter = (argc > 1000) ? 1e-300 : 0.0;
  %(fill)s

  double guard = 0.0;
  for (int w = 0; w < 3; w++) { run_timed(bench_base, &guard); run_timed(bench_opt, &guard); }

  double bb = 1e30, oo = 1e30;
  for (int k = 0; k < 9; k++) {
    double a = run_timed(bench_base, &guard);
    double b = run_timed(bench_opt, &guard);
    if (a < bb) bb = a;
    if (b < oo) oo = b;
  }

  printf("{\"base_ns\": %%.1f, \"opt_ns\": %%.1f, \"guard\": %%.17g, \"vals\": [", bb, oo, guard);
  for (int i = 0; i < N; i++) {
    %(loads)s
    printf("%%s{\"base\": %%.17g, \"opt\": %%.17g}", i ? ", " : "", %(base_c)s, %(opt_c)s);
  }
  printf("]}\n");
  return 0;
}
'''


def gen_source(name, case, base_t, opt_t, pts, reps):
    vs = case['vars']
    arrays_decl = ', '.join('A_{v}[{n}]'.format(v=v, n=len(pts)) for v in vs)
    loads = ' '.join('const double {v} = A_{v}[i];'.format(v=v) for v in vs)
    fill = '\n  '.join(
        'for (int i = 0; i < N; i++) A_{v}[i] = ({lo}) + (({hi}) - ({lo})) * (double)i / (double)N + jitter;'.format(
            v=v, lo=repr(pts[0][v]), hi=repr(pts[-1][v] + (pts[-1][v] - pts[0][v]) / (len(pts) - 1)))
        for v in vs)
    src = C_TEMPLATE % {
        'npts': len(pts), 'reps': reps, 'arrays_decl': arrays_decl, 'loads': loads,
        'fill': fill, 'base_c': to_c(base_t), 'opt_c': to_c(opt_t), 'timer': TIMER_C,
    }
    cpath = BENCH / ('fair_' + name + '.c')
    cpath.write_text(src, encoding='utf-8')
    return cpath


def run_mode(cpath, name, mode, flags):
    exe = BENCH / ('fair_{}_{}{}'.format(name, mode, '.exe' if sys.platform.startswith('win') else ''))
    r = subprocess.run([CLANG, str(cpath), *flags, *link_flags(), '-o', str(exe)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError('сборка {} {}: {}'.format(name, mode, r.stderr[:2000]))
    out = subprocess.run([str(exe)], capture_output=True, text=True, timeout=900)
    if out.returncode != 0:
        raise RuntimeError('прогон {} {}: {}'.format(name, mode, out.stderr[:2000]))
    return json.loads(out.stdout)


def max_err(expr, pts, vals, key):
    worst = Decimal(0)
    for p, v in zip(pts, vals):
        env = {k: Decimal(float(val)) for k, val in p.items()}
        worst = max(worst, rel_error(v[key], exact(expr, env)))
    return worst


def run_case(name, case, n_points=1024, reps=400):
    expr, domain = case['expr'], case['domain']
    eg = EGraph()
    root = eg.add_expr(expr)
    eg.saturate(RULES, iters=case.get('iters', 10), domain=domain)
    front, _ = pareto_extract(eg, root, domain, keep=10)
    b_cost, b_err, _, _, _ = tree_cost(expr, domain)
    budget = case.get('budget', 2.0)
    safe = [p for p in front if p[1] <= b_err * budget]
    pick = min(safe, key=lambda p: (p[0], p[1])) if safe else min(front, key=lambda p: p[1])

    pts = recompute_points(case, n_points)
    cpath = gen_source(name, case, expr, pick[2], pts, reps)

    rows = []
    for mode, flags in MODES:
        res = run_mode(cpath, name, mode, flags)
        rows.append({'mode': mode, 'flags': ' '.join(flags),
                     'base_ns': res['base_ns'], 'opt_ns': res['opt_ns'],
                     'base_err': max_err(expr, pts, res['vals'], 'base'),
                     'opt_err': max_err(expr, pts, res['vals'], 'opt')})

    o2 = rows[0]
    best_compiler = min(r['base_ns'] for r in rows)        # лучшее, что компилятор выжал сам
    best_ours = min(r['opt_ns'] for r in rows)
    fast = [r for r in rows if r['mode'] == 'fast'][0]
    return {
        'name': name,
        'base_expr': to_text(expr), 'base_ops': op_count(expr),
        'opt_expr': to_text(pick[2]), 'opt_ops': op_count(pick[2]),
        'rows': rows,
        'vs_o2': o2['base_ns'] / o2['opt_ns'] if o2['opt_ns'] else float('nan'),
        'vs_best_compiler': best_compiler / best_ours if best_ours else float('nan'),
        'fast_err': fast['base_err'], 'our_err': o2['opt_err'], 'exact_err': o2['base_err'],
    }


def fmt(d):
    s = ['=' * 92, 'КЕЙС: ' + d['name'],
         'исходная форма : {}   [{}]'.format(d['base_expr'], d['base_ops']),
         'наш выбор      : {}   [{}]'.format(d['opt_expr'], d['opt_ops']), '',
         '{:<22} {:>12} {:>12} {:>14} {:>14}'.format('режим', 'исходная мс', 'наша мс', 'ошибка исх.', 'ошибка наша')]
    for r in d['rows']:
        s.append('{:<22} {:>12.3f} {:>12.3f} {:>14.3e} {:>14.3e}'.format(
            r['flags'], r['base_ns'] / 1e6, r['opt_ns'] / 1e6,
            float(r['base_err']), float(r['opt_err'])))
    s.append('')
    s.append('против -O2            : x{:.2f}'.format(d['vs_o2']))
    s.append('против лучшего у clang: x{:.2f}   <- главная цифра, её и спросят'.format(d['vs_best_compiler']))
    s.append('ошибка: точная {:.3e} · fast-math {:.3e} · наша {:.3e}'.format(
        float(d['exact_err']), float(d['fast_err']), float(d['our_err'])))
    return '\n'.join(s)


if __name__ == '__main__':
    names = sys.argv[1:] or list(CASES)
    out = []
    for nm in names:
        r = run_case(nm, CASES[nm])
        out.append(r)
        print(fmt(r), flush=True)
    (BENCH / 'fair_results.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    txt = '\n'.join(fmt(r) for r in out)
    (BENCH / 'fair_report.txt').write_text(txt, encoding='utf-8')
    print('\nотчёт: bench/fair_report.txt · машинно: bench/fair_results.json')
