# -*- coding: utf-8 -*-
"""Агрегаты: выбор схемы суммирования как оптимизация по фронту Парето.

Запуск: python pareto/run_reduce.py [имя_набора ...]
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from decimal import Decimal, getcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pareto.reductions import (SCHEMES, bound, cost, load_calibration, pareto,
                               pick, save_calibration)

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / 'bench'
getcontext().prec = 80

N = 1 << 16          # 65536 элементов
REPS = 300


# ---------- наборы данных ----------
def ds_uniform(n):
    """Однородные числа около единицы — мягкий случай."""
    return [1.0 + 0.5 * math.sin(i * 0.37) for i in range(n)]


def ds_mixed(n):
    """Крупные слагаемые вперемешку с мелкими — мелкие тонут в наивной сумме."""
    return [1e8 if i % 997 == 0 else 1.0 + 1e-3 * math.sin(i) for i in range(n)]


def ds_alternating(n):
    """Знакопеременный ряд с большим разбросом масштабов."""
    return [((-1.0) ** i) * (1e6 if i % 2 == 0 else 1e-6) + 1e-9 * i for i in range(n)]


DATASETS = {
    'uniform': ds_uniform,
    'mixed': ds_mixed,
    'alternating': ds_alternating,
}


JS = """// сгенерировано pareto/run_reduce.py
const A = Float64Array.from({data});
const N = A.length;

{funcs}

const NAMES = {names};
const FN = {{ {fnmap} }};

function timeIt(f, reps) {{
  let guard = 0.0;
  const t0 = process.hrtime.bigint();
  for (let r = 0; r < reps; r++) guard += f();
  const t1 = process.hrtime.bigint();
  return {{ ns: Number(t1 - t0), guard }};
}}

for (let w = 0; w < 4; w++) for (const nm of NAMES) timeIt(FN[nm], 20);

const best = {{}}, vals = {{}};
for (const nm of NAMES) {{ best[nm] = Infinity; vals[nm] = FN[nm](); }}
for (let k = 0; k < 7; k++) {{
  for (const nm of NAMES) {{
    const r = timeIt(FN[nm], {reps});
    if (r.ns < best[nm]) best[nm] = r.ns;
  }}
}}
console.log(JSON.stringify({{ ns: best, vals }}));
"""


def build_js(name, data, names):
    funcs = '\n'.join(
        'function f_{nm}() {{{body}\n}}'.format(nm=nm, body=SCHEMES[nm]['js'])
        for nm in names)
    fnmap = ', '.join('{nm}: f_{nm}'.format(nm=nm) for nm in names)
    src = JS.format(
        data=json.dumps(data),
        funcs=funcs,
        names=json.dumps(names),
        fnmap=fnmap,
        reps=REPS,
    )
    path = BENCH / ('reduce_' + name + '.mjs')
    path.write_text(src, encoding='utf-8')
    return path


def run(name):
    data = DATASETS[name](N)
    names = list(SCHEMES)
    js = build_js(name, data, names)
    out = subprocess.run(['node', str(js)], capture_output=True, text=True, timeout=900)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[:2000])
    res = json.loads(out.stdout)

    exact_sum = sum((Decimal(x) for x in data), Decimal(0))
    sum_abs = float(sum(abs(x) for x in data))

    rows = []
    for nm in names:
        got = Decimal(res['vals'][nm])
        abs_err = abs(got - exact_sum)
        rel = abs_err / abs(exact_sum) if exact_sum != 0 else abs_err
        rows.append({
            'scheme': nm,
            'title': SCHEMES[nm]['title'],
            'cost': cost(nm, N),
            'bound': bound(nm, N, sum_abs),
            'ns': res['ns'][nm],
            'abs_err': abs_err,
            'rel_err': rel,
        })

    front = pareto(N, sum_abs)
    fast = pick(N, sum_abs, budget=2.0)
    accurate = pick(N, sum_abs, cost_budget=1.5)
    accurate2 = pick(N, sum_abs, cost_budget=2.0)
    return {'name': name, 'rows': rows, 'front': front,
            'fast': fast, 'accurate': accurate, 'accurate2': accurate2,
            'exact': exact_sum, 'sum_abs': sum_abs}


def fmt(d):
    base = next(r for r in d['rows'] if r['scheme'] == 'naive')
    s = ['=' * 78, 'НАБОР: {} · {} элементов · сумма модулей {:.3e}'.format(
        d['name'], N, d['sum_abs']), '']
    s.append('схема                      время     ускорение   реальная ошибка   граница')
    for r in sorted(d['rows'], key=lambda r: r['ns']):
        s.append('{:<26} {:>7.2f} мс   x{:>5.2f}   {:>13.3e}   {:>9.3e}'.format(
            r['title'], r['ns'] / 1e6, base['ns'] / r['ns'],
            float(r['rel_err']), r['bound'] / abs(float(d['exact']))))
    s.append('')
    s.append('фронт Парето: ' + ', '.join(p[2] for p in d['front']))
    s.append('режим скорости (бюджет ошибки x2):   ' + d['fast'][2])
    s.append('режим точности (бюджет цены x1.5):   ' + d['accurate'][2])
    fast_row = next(r for r in d['rows'] if r['scheme'] == d['fast'][2])
    acc_row = next(r for r in d['rows'] if r['scheme'] == d['accurate'][2])
    s.append('')
    s.append('БЫЛО  (наивная сумма):   {:.2f} мс · ошибка {:.3e}'.format(
        base['ns'] / 1e6, float(base['rel_err'])))
    s.append('СТАЛО (режим скорости):  {:.2f} мс · ошибка {:.3e} · ускорение x{:.2f}'.format(
        fast_row['ns'] / 1e6, float(fast_row['rel_err']), base['ns'] / fast_row['ns']))
    s.append('СТАЛО (точность, цена x1.5):  {:.2f} мс · ошибка {:.3e} · точнее в {:.3g} раз'.format(
        acc_row['ns'] / 1e6, float(acc_row['rel_err']),
        float(base['rel_err'] / acc_row['rel_err']) if acc_row['rel_err'] > 0 else float('inf')))
    a2 = next(r for r in d['rows'] if r['scheme'] == d['accurate2'][2])
    s.append('СТАЛО (точность, цена x2.0):  {:.2f} мс · ошибка {:.3e} · точнее в {:.3g} раз'.format(
        a2['ns'] / 1e6, float(a2['rel_err']),
        float(base['rel_err'] / a2['rel_err']) if a2['rel_err'] > 0 else float('inf')))
    return '\n'.join(s)


def calibrate():
    """Один замер на эталонном наборе → стоимость схем в нс на элемент."""
    d = run('uniform')
    ns_per_elem = {r['scheme']: r['ns'] / (N * REPS) for r in d['rows']}
    path = save_calibration(ns_per_elem)
    print('калибровка записана: ' + str(path))
    for k, v in sorted(ns_per_elem.items(), key=lambda kv: kv[1]):
        print('  {:<12} {:.4f} нс/элемент'.format(k, v))
    load_calibration()
    return ns_per_elem


if __name__ == '__main__':
    args = sys.argv[1:]
    if args and args[0] == '--calibrate':
        calibrate()
        args = args[1:]
    load_calibration()
    names = args or list(DATASETS)
    out = []
    for nm in names:
        d = run(nm)
        out.append(d)
        print(fmt(d), flush=True)
    (BENCH / 'reduce_results.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
