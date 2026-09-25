# -*- coding: utf-8 -*-
"""Прогон: насыщение → фронт Парето → кодогенерация → замер в node.

Запуск:  python pareto/run.py            (все кейсы)
         python pareto/run.py poly       (один кейс)
"""
from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal, getcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pareto.analysis import pareto_extract, tree_cost
from pareto.codegen import op_count, to_js, to_text
from pareto.egraph import EGraph
from pareto.rules import RULES

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / 'bench'
getcontext().prec = 60


# ---------- эталон в 60 значащих цифр ----------
def exact(tree, env):
    op = tree[0]
    if op == 'num':
        return Decimal(float(tree[1]))
    if op == 'var':
        return env[tree[1]]
    if op == 'neg':
        return -exact(tree[1], env)
    if op == 'sqrt':
        return exact(tree[1], env).sqrt()
    if op == 'exp':
        return exact(tree[1], env).exp()
    if op == 'log':
        return exact(tree[1], env).ln()
    a = exact(tree[1], env)
    b = exact(tree[2], env)
    if op == '+': return a + b
    if op == '-': return a - b
    if op == '*': return a * b
    if op == '/': return a / b
    raise ValueError(op)


# ---------- кейсы ----------
def V(n): return ('var', n)
def N(x): return ('num', float(x))


CASES = {
    # разность корней при большом аргументе — классическое сокращение значащих цифр
    'diff_sqrt': {
        'expr': ('-', ('sqrt', ('+', V('x'), N(1))), ('sqrt', V('x'))),
        'vars': ['x'],
        'domain': {'x': (1e6, 1e9)},
        'points': lambda i, n: {'x': 1e6 + (1e9 - 1e6) * i / n},
    },
    # многочлен третьей степени в развёрнутой форме
    'poly': {
        'expr': ('+', ('+', ('+', N(1.5), ('*', N(2.5), V('x'))),
                       ('*', N(3.5), ('*', V('x'), V('x')))),
                 ('*', N(4.5), ('*', V('x'), ('*', V('x'), V('x'))))),
        'vars': ['x'],
        'domain': {'x': (0.5, 2.0)},
        # шаг намеренно не двоичная дробь: иначе точки попадают в числа,
        # представимые точно, и обе формы дают нулевую ошибку — мерить нечего
        'points': lambda i, n: {'x': 0.5 + 1.4999 * (i + 0.31) / n},
    },
    # два деления на общий знаменатель
    'two_div': {
        'expr': ('+', ('/', V('a'), V('c')), ('/', V('b'), V('c'))),
        'vars': ['a', 'b', 'c'],
        'domain': {'a': (1.0, 2.0), 'b': (1.0, 2.0), 'c': (3.0, 4.0)},
        'points': lambda i, n: {'a': 1 + i / n, 'b': 2 - i / n, 'c': 3 + i / n},
    },
    # цепочка делений: (a/b)/c = a/(b*c) — деление дороже умножения
    'div_chain': {
        'expr': ('/', ('/', V('a'), V('b')), V('c')),
        'vars': ['a', 'b', 'c'],
        'domain': {'a': (1.0, 2.0), 'b': (3.0, 4.0), 'c': (5.0, 6.0)},
        'points': lambda i, n: {'a': 1 + i / n, 'b': 3 + i / n, 'c': 5 + i / n},
    },
    # log(a) - log(b) = log(a/b): два дорогих логарифма превращаются в один
    'log_ratio': {
        'expr': ('-', ('log', V('a')), ('log', V('b'))),
        'vars': ['a', 'b'],
        # домены разнесены: при a ≈ b замена log(a)-log(b) на log(a/b) опасна,
        # и оптимизатор её сам отвергает по границе ошибки (проверено отдельно)
        'domain': {'a': (100.0, 200.0), 'b': (2.0, 3.0)},
        'points': lambda i, n: {'a': 100 + 100 * i / n, 'b': 2 + i / n},
    },
    # exp(a) * exp(b) = exp(a + b)
    'exp_sum': {
        'expr': ('*', ('exp', V('a')), ('exp', V('b'))),
        'vars': ['a', 'b'],
        'domain': {'a': (0.1, 2.0), 'b': (0.1, 2.0)},
        'points': lambda i, n: {'a': 0.1 + 1.9 * i / n, 'b': 2.0 - 1.9 * i / n},
    },
    # разность квадратов близких чисел
    'sq_diff': {
        'expr': ('-', ('*', V('x'), V('x')), ('*', V('y'), V('y'))),
        'vars': ['x', 'y'],
        'domain': {'x': (1000.0, 1000.001), 'y': (999.999, 1000.0)},
        'points': lambda i, n: {'x': 1000.0 + 0.001 * i / n, 'y': 1000.0 - 0.001 * i / n},
    },
}


JS_TEMPLATE = """// сгенерировано pareto/run.py — не править руками
// Данные в Float64Array, выражение развёрнуто прямо в теле цикла:
// меряем арифметику, а не накладные расходы на вызов функции.
{arrays}
const N = {npts};

function benchBase(reps) {{
  let acc = 0.0;
  for (let r = 0; r < reps; r++) {{
    for (let i = 0; i < N; i++) {{
      {loads}
      acc += {base_js};
    }}
  }}
  return acc;
}}

function benchOpt(reps) {{
  let acc = 0.0;
  for (let r = 0; r < reps; r++) {{
    for (let i = 0; i < N; i++) {{
      {loads}
      acc += {opt_js};
    }}
  }}
  return acc;
}}

function benchAcc(reps) {{
  let acc = 0.0;
  for (let r = 0; r < reps; r++) {{
    for (let i = 0; i < N; i++) {{
      {loads}
      acc += {acc_js};
    }}
  }}
  return acc;
}}

function timeIt(f, reps) {{
  const t0 = process.hrtime.bigint();
  const s = f(reps);
  const t1 = process.hrtime.bigint();
  return {{ ns: Number(t1 - t0), s }};
}}

const REPS = {reps};
// прогрев: TurboFan должен успеть скомпилировать оба цикла
for (let w = 0; w < 6; w++) {{ timeIt(benchBase, 50); timeIt(benchOpt, 50); }}

let bb = Infinity, oo = Infinity, aa = Infinity, guard = 0;
for (let k = 0; k < 11; k++) {{
  // чередуем порядок, чтобы не ловить систематический перекос
  const a = timeIt(benchBase, REPS); const b = timeIt(benchOpt, REPS);
  const c = timeIt(benchAcc, REPS);
  const b2 = timeIt(benchOpt, REPS); const a2 = timeIt(benchBase, REPS);
  bb = Math.min(bb, a.ns, a2.ns); oo = Math.min(oo, b.ns, b2.ns); aa = Math.min(aa, c.ns);
  guard += a.s + b.s + c.s + a2.s + b2.s;
}}

const vals = [];
for (let i = 0; i < N; i++) {{
  {loads}
  vals.push({{ base: {base_js}, opt: {opt_js}, acc: {acc_js} }});
}}
console.log(JSON.stringify({{ base_ns: bb, opt_ns: oo, acc_ns: aa, vals, guard }}));
"""


def build_js(name, case, base_tree, opt_tree, pts, reps, acc_tree=None):
    arrays = '\n'.join(
        'const A_{v} = Float64Array.from({data});'.format(
            v=v, data=json.dumps([p[v] for p in pts]))
        for v in case['vars'])
    loads = '\n      '.join(
        'const {v} = A_{v}[i];'.format(v=v) for v in case['vars'])
    src = JS_TEMPLATE.format(
        arrays=arrays,
        loads=loads,
        npts=len(pts),
        base_js=to_js(base_tree),
        opt_js=to_js(opt_tree),
        acc_js=to_js(acc_tree if acc_tree is not None else opt_tree),
        reps=reps,
    )
    path = BENCH / ('case_' + name + '.mjs')
    path.write_text(src, encoding='utf-8')
    return path


def rel_error(got, ref):
    # Decimal(float) — ТОЧНОЕ двоичное значение, а не кратчайшая запись.
    # С Decimal(repr(x)) измерение врёт ровно там, где интересно: на разностях
    # близких чисел артефакт записи больше самой ошибки вычисления.
    if ref == 0:
        return abs(Decimal(got))
    return abs((Decimal(got) - ref) / ref)


def run_case(name, case, n_points=1024, reps=400):
    expr = case['expr']
    domain = case['domain']

    eg = EGraph()
    root = eg.add_expr(expr)
    eg.saturate(RULES, iters=case.get('iters', 10), node_limit=case.get('node_limit', 60000), domain=domain)
    nodes, classes = eg.size()

    front, _ = pareto_extract(eg, root, domain, keep=10)
    b_cost, b_err, _, b_work, b_lat = tree_cost(expr, domain)

    # Политика выбора. Берём самую дешёвую форму, у которой доказанная граница
    # ошибки не более чем в BUDGET раз хуже исходной. BUDGET=2 означает
    # «разрешаем потерять не больше одного бита мантиссы».
    # Это и есть содержательная разница с fast-math: там гарантий нет вообще,
    # здесь потеря ограничена числом и доказана статически.
    budget = case.get('budget', 2.0)
    safe = [p for p in front if p[1] <= b_err * budget]
    pick = min(safe, key=lambda p: (p[0], p[1])) if safe else min(front, key=lambda p: p[1])

    # Второй режим: максимум точности при ограничении на стоимость.
    # Готов заплатить не больше cost_budget от исходной цены — что получу по точности.
    cost_budget = case.get('cost_budget', 1.5)
    affordable = [p for p in front if p[0] <= b_cost * cost_budget]
    best_acc = (min(affordable, key=lambda p: (p[1], p[0])) if affordable
                else min(front, key=lambda p: (p[1], p[0])))

    pts = [case['points'](i, n_points) for i in range(n_points)]
    js = build_js(name, case, expr, pick[2], pts, reps, best_acc[2])
    out = subprocess.run(['node', str(js)], capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[:2000])
    res = json.loads(out.stdout)

    err_base = err_opt = err_acc = Decimal(0)   # относительная
    abs_base = abs_opt = abs_acc = Decimal(0)   # абсолютная — сравнима с границей
    for p, v in zip(pts, res['vals']):
        env = {k: Decimal(float(val)) for k, val in p.items()}
        ref = exact(expr, env)
        err_base = max(err_base, rel_error(v['base'], ref))
        err_opt = max(err_opt, rel_error(v['opt'], ref))
        abs_base = max(abs_base, abs(Decimal(v['base']) - ref))
        abs_opt = max(abs_opt, abs(Decimal(v['opt']) - ref))
        err_acc = max(err_acc, rel_error(v['acc'], ref))
        abs_acc = max(abs_acc, abs(Decimal(v['acc']) - ref))

    return {
        'name': name,
        'egraph': {'nodes': nodes, 'classes': classes, 'pareto': len(front)},
        'base': {'expr': to_text(expr), 'cost': b_cost, 'bound': b_err,
                 'work': b_work, 'lat': b_lat,
                 'ops': op_count(expr), 'ns': res['base_ns'],
                 'real_err': err_base, 'abs_err': abs_base},
        'opt': {'expr': to_text(pick[2]), 'cost': pick[0], 'bound': pick[1],
                'work': pick[3], 'lat': pick[4],
                'ops': op_count(pick[2]), 'ns': res['opt_ns'],
                'real_err': err_opt, 'abs_err': abs_opt},
        'most_accurate': {'expr': to_text(best_acc[2]), 'cost': best_acc[0], 'bound': best_acc[1],
                          'ops': op_count(best_acc[2]),
                          'gain': (b_err / best_acc[1]) if best_acc[1] > 0 else float('inf'),
                          'ns': res.get('acc_ns'), 'real_err': err_acc, 'abs_err': abs_acc},
        'speedup': res['base_ns'] / res['opt_ns'] if res['opt_ns'] else float('nan'),
    }


def fmt(d):
    s = []
    s.append('=' * 72)
    s.append('КЕЙС: ' + d['name'])
    e = d['egraph']
    s.append('e-граф: {} узлов, {} классов, фронт Парето: {} точек'.format(
        e['nodes'], e['classes'], e['pareto']))
    for key, title in (('base', 'БЫЛО '), ('opt', 'СТАЛО')):
        x = d[key]
        s.append('{}: {}'.format(title, x['expr']))
        s.append('       операции {} · работа {:.1f} + крит.путь {:.1f} · граница ошибки {:.3e}'.format(
            x['ops'], x['work'], x['lat'], x['bound']))
        s.append('       замер node: {:.3f} мс · реальная ошибка {:.3e} абс · {:.3e} отн'.format(
            x['ns'] / 1e6, float(x['abs_err']), float(x['real_err'])))
    ab, ao = d['base']['abs_err'], d['opt']['abs_err']
    if ao == 0 or (ab > 0 and ab / ao > Decimal(10) ** 6):
        acc = 'ошибка практически исчезла (было {:.2e} отн, стало {:.2e} отн)'.format(
            float(d['base']['real_err']), float(d['opt']['real_err']))
    elif ab > ao:
        acc = 'ошибка меньше в {:.1f} раз'.format(float(ab / ao))
    elif ab == ao:
        acc = 'ошибка та же'
    else:
        acc = 'ошибка выросла в {:.1f} раз (в пределах бюджета)'.format(float(ao / ab))
    s.append('ИТОГО: ускорение x{:.2f} · {}'.format(d['speedup'], acc))
    ma = d['most_accurate']
    s.append('Режим «максимум точности»: {}'.format(ma['expr']))
    s.append('       операции {} · стоимость {:.1f} · граница ошибки лучше в {:.3g} раз'.format(
        ma['ops'], ma['cost'], ma['gain']))
    if ma.get('ns'):
        s.append('       замер node: {:.3f} мс · реальная ошибка {:.3e} отн (было {:.3e})'.format(
            ma['ns'] / 1e6, float(ma['real_err']), float(d['base']['real_err'])))
    s.append('самая точная форма из фронта: ' + d['most_accurate']['expr'])
    return '\n'.join(s)


if __name__ == '__main__':
    names = sys.argv[1:] or list(CASES)
    results = []
    for nm in names:
        r = run_case(nm, CASES[nm])
        results.append(r)
        print(fmt(r), flush=True)
    (BENCH / 'results.json').write_text(
        json.dumps([{k: (str(v) if isinstance(v, Decimal) else v) for k, v in r.items()}
                    for r in results], ensure_ascii=False, indent=2, default=str),
        encoding='utf-8')
