# -*- coding: utf-8 -*-
"""Тот же результат, но через настоящий конвейер MLIR.

Обе формы (исходная и выбранная нами) печатаются в диалектах func/arith/math,
прогоняются штатным `mlir-opt` → `mlir-translate` до LLVM IR, компилируются и
замеряются. Смысл: показать, что переписывание живёт не в питоновском стенде,
а в том слое, через который проходят почти все языки поверх LLVM.

    MLIR_BIN=C:\\llvm-src\\build\\bin python pareto/run_mlir.py sq_diff

Оговорка, которую важно не потерять: сгенерированный LLVM IR идёт без
fast-math флагов на инструкциях, то есть это честный строгий режим.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from decimal import Decimal, getcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pareto.analysis import pareto_extract, tree_cost
from pareto.codegen import op_count, to_text
from pareto.egraph import EGraph
from pareto.parser import variables
from pareto.rules import RULES
from pareto.run import CASES, exact, rel_error
from pareto.run_native import recompute_points
from pareto.to_mlir import to_mlir
from pareto.toolchain import TIMER_C, find_clang, find_mlir_tool, link_flags

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / 'bench'
# mlir-opt на Windows не открывает пути с не-ASCII символами: ругается «No mapping
# for the Unicode character exists in the target multi-byte code page». Поэтому вся
# кухня идёт во временной папке с латинским путём, а в bench кладутся копии артефактов.
WORK = Path(tempfile.gettempdir()) / 'pareto_mlir'
WORK.mkdir(parents=True, exist_ok=True)
getcontext().prec = 60

LOWER_PASSES = ['--convert-math-to-llvm', '--convert-arith-to-llvm',
                '--convert-func-to-llvm', '--reconcile-unrealized-casts']

RUN = dict(capture_output=True, text=True, encoding='utf-8', errors='replace')

DRIVER_C = r'''// сгенерировано pareto/run_mlir.py — драйвер замера для функций из MLIR
#include <stdio.h>
%(timer)s

#define N %(npts)d
#define REPS %(reps)d

extern double %(fn_base)s(%(proto)s);
extern double %(fn_opt)s(%(proto)s);

static double %(arrays_decl)s;

static double bench(double (*f)(%(proto)s)) {
  double acc = 0.0;
  for (int r = 0; r < REPS; r++)
    for (int i = 0; i < N; i++) acc += f(%(call_args)s);
  return acc;
}

static double run_timed(double (*f)(%(proto)s), double *guard) {
  double t0 = now_ns();
  *guard += bench(f);
  return now_ns() - t0;
}

int main(int argc, char **argv) {
  double jitter = (argc > 1000) ? 1e-300 : 0.0;
  %(fill)s

  double guard = 0.0;
  for (int w = 0; w < 3; w++) { run_timed(%(fn_base)s, &guard); run_timed(%(fn_opt)s, &guard); }

  double bb = 1e30, oo = 1e30;
  for (int k = 0; k < 9; k++) {
    double a = run_timed(%(fn_base)s, &guard);
    double b = run_timed(%(fn_opt)s, &guard);
    if (a < bb) bb = a;
    if (b < oo) oo = b;
  }

  printf("{\"base_ns\": %%.1f, \"opt_ns\": %%.1f, \"guard\": %%.17g, \"vals\": [", bb, oo, guard);
  for (int i = 0; i < N; i++)
    printf("%%s{\"base\": %%.17g, \"opt\": %%.17g}", i ? ", " : "",
           %(fn_base)s(%(call_args)s), %(fn_opt)s(%(call_args)s));
  printf("]}\n");
  return 0;
}
'''


def mlir_to_ll(tree, fname, tag, vs):
    """Модуль MLIR → LLVM IR штатным конвейером, без наших вмешательств."""
    mlir_path = WORK / ('mlir_{}.mlir'.format(tag))
    low_path = WORK / ('mlir_{}.llvm.mlir'.format(tag))
    ll_path = WORK / ('mlir_{}.ll'.format(tag))
    text = to_mlir(tree, fname, vars_=vs)
    mlir_path.write_text(text, encoding='utf-8')
    (BENCH / ('mlir_{}.mlir'.format(tag))).write_text(text, encoding='utf-8')   # копия для репозитория

    opt, tr = find_mlir_tool('mlir-opt'), find_mlir_tool('mlir-translate')
    r = subprocess.run([opt, str(mlir_path), *LOWER_PASSES, '-o', str(low_path)], **RUN)
    if r.returncode != 0:
        raise RuntimeError('mlir-opt: ' + (r.stderr or '')[:2000])
    r = subprocess.run([tr, str(low_path), '--mlir-to-llvmir', '-o', str(ll_path)], **RUN)
    if r.returncode != 0:
        raise RuntimeError('mlir-translate: ' + (r.stderr or '')[:2000])
    return ll_path


def build_and_run(name, case, base_t, opt_t, pts, reps, opt_level='-O2'):
    vs = variables(base_t) or case['vars']
    fn_base, fn_opt = name + '_base', name + '_opt'
    # порядок аргументов один на обе формы — иначе вызов подаст значения не в те параметры
    ll_base = mlir_to_ll(base_t, fn_base, name + '_base', vs)
    ll_opt = mlir_to_ll(opt_t, fn_opt, name + '_opt', vs)

    proto = ', '.join(['double'] * len(vs))
    call_args = ', '.join('A_{}[i]'.format(v) for v in vs)
    arrays_decl = ', '.join('A_{v}[{n}]'.format(v=v, n=len(pts)) for v in vs)
    fill = '\n  '.join(
        'for (int i = 0; i < N; i++) A_{v}[i] = ({lo}) + (({hi}) - ({lo})) * (double)i / (double)N + jitter;'.format(
            v=v, lo=repr(pts[0][v]), hi=repr(pts[-1][v] + (pts[-1][v] - pts[0][v]) / (len(pts) - 1)))
        for v in vs)

    drv = WORK / ('mlir_driver_{}.c'.format(name))
    drv.write_text(DRIVER_C % {
        'timer': TIMER_C, 'npts': len(pts), 'reps': reps, 'proto': proto,
        'fn_base': fn_base, 'fn_opt': fn_opt, 'arrays_decl': arrays_decl,
        'fill': fill, 'call_args': call_args,
    }, encoding='utf-8')

    exe = WORK / ('mlir_{}{}'.format(name, '.exe' if sys.platform.startswith('win') else ''))
    r = subprocess.run([find_clang(), str(drv), str(ll_base), str(ll_opt), opt_level,
                        *link_flags(), '-o', str(exe)], **RUN)
    if r.returncode != 0:
        raise RuntimeError('сборка: ' + (r.stderr or '')[:2500])
    out = subprocess.run([str(exe)], timeout=900, **RUN)
    if out.returncode != 0:
        raise RuntimeError('прогон: ' + (out.stderr or '')[:2000])
    return json.loads(out.stdout)


def run_case(name, case, n_points=1024, reps=400):
    expr, domain = case['expr'], case['domain']
    eg = EGraph()
    root = eg.add_expr(expr)
    eg.saturate(RULES, iters=case.get('iters', 10), domain=case['domain'])
    front, _ = pareto_extract(eg, root, domain, keep=10)
    _, b_err, _, _, _ = tree_cost(expr, domain)
    budget = case.get('budget', 2.0)
    safe = [p for p in front if p[1] <= b_err * budget]
    pick = min(safe, key=lambda p: (p[0], p[1])) if safe else min(front, key=lambda p: p[1])

    pts = recompute_points(case, n_points)
    res = build_and_run(name, case, expr, pick[2], pts, reps)

    eb = eo = Decimal(0)
    for p, v in zip(pts, res['vals']):
        env = {k: Decimal(float(val)) for k, val in p.items()}
        ref = exact(expr, env)
        eb = max(eb, rel_error(v['base'], ref))
        eo = max(eo, rel_error(v['opt'], ref))

    return {'name': name,
            'base': {'form': to_text(expr), 'ops': op_count(expr), 'ns': res['base_ns'], 'err': eb},
            'opt': {'form': to_text(pick[2]), 'ops': op_count(pick[2]), 'ns': res['opt_ns'], 'err': eo},
            'speedup': res['base_ns'] / res['opt_ns'] if res['opt_ns'] else float('nan')}


def fmt(d):
    s = ['=' * 80, 'КЕЙС: {}   [через MLIR: func/arith/math -> LLVM IR -> clang -O2]'.format(d['name'])]
    for k, t in (('base', 'ИСХОДНАЯ'), ('opt', 'НАША    ')):
        x = d[k]
        s.append('{}: {}'.format(t, x['form']))
        s.append('          {:.3f} мс · ошибка {:.3e}'.format(x['ns'] / 1e6, float(x['err'])))
    s.append('ИТОГО: ускорение x{:.2f}'.format(d['speedup']))
    if d['base']['err'] > 0 and d['opt']['err'] > 0:
        s.append('       точность лучше в {:.3g} раз'.format(float(d['base']['err'] / d['opt']['err'])))
    return '\n'.join(s)


if __name__ == '__main__':
    names = sys.argv[1:] or list(CASES)
    out = []
    for nm in names:
        r = run_case(nm, CASES[nm])
        out.append(r)
        print(fmt(r), flush=True)
    (BENCH / 'mlir_results.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    (BENCH / 'mlir_report.txt').write_text('\n'.join(fmt(r) for r in out), encoding='utf-8')
    print('\nотчёт: bench/mlir_report.txt · машинно: bench/mlir_results.json')
