# -*- coding: utf-8 -*-
"""Итоговое табло по трудному полигону: мы против Herbie, обе метрики сразу.

Формы Herbie берутся из сохранённого `bench/herbie_hard_improved.fpcore`, поэтому
Racket для пересчёта не нужен — меняется только наша сторона, и табло пересобирается
за секунды после каждой правки правил.

Считаются обе линейки:
  * наша — доказанная граница абсолютной ошибки и модель стоимости;
  * его — средние биты ошибки на выборке входов, log2(1 + ulp).

    python pareto/run_hard_scoreboard.py
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pareto.analysis import pareto_extract, tree_cost
from pareto.codegen import to_text
from pareto.egraph import EGraph
from pareto.hard_cases import HARD_CASES
from pareto.rules import RULES
from pareto.run_herbie import herbie_body, sexp_to_tree
from pareto.run_herbie_metric import bits_of_error

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / 'bench'
SAMPLES = 1500
SEED = 20260925


def main():
    text = (BENCH / 'herbie_hard_improved.fpcore').read_text(encoding='utf-8')
    rows = []

    for name, case in HARD_CASES.items():
        rng = random.Random(SEED)
        names = sorted(case['domain'])
        pts = [{n: rng.uniform(*case['domain'][n]) for n in names} for _ in range(SAMPLES)]

        eg = EGraph()
        root = eg.add_expr(case['expr'])
        eg.saturate(RULES, iters=case.get('iters', 10), node_limit=case.get('node_limit', 60000))
        front, _ = pareto_extract(eg, root, case['domain'], keep=10)

        node = herbie_body(text, name)
        h_tree = sexp_to_tree(node) if node is not None else None

        base_bits = bits_of_error(case['expr'], case, pts)[0]
        # наша точка — самая точная в пределах разумной цены, то есть то, что инструмент отдаёт
        ours = min(front, key=lambda p: (p[1], p[0]))
        ours_bits = bits_of_error(ours[2], case, pts)[0]

        row = {'name': name, 'base_bits': base_bits,
               'ours_form': to_text(ours[2]), 'ours_bound': ours[1], 'ours_cost': ours[0],
               'ours_bits': ours_bits}

        if h_tree is None:
            row.update({'herbie_form': None})
        else:
            h_cost, h_bound, _, _, _ = tree_cost(h_tree, case['domain'])
            try:
                h_bits = bits_of_error(h_tree, case, pts)[0]
            except Exception:
                h_bits = float('nan')
            covered = [p for p in front if p[0] <= h_cost and p[1] <= h_bound]
            row.update({'herbie_form': to_text(h_tree), 'herbie_bound': h_bound,
                        'herbie_cost': h_cost, 'herbie_bits': h_bits,
                        'covered': bool(covered)})
        rows.append(row)

    (BENCH / 'hard_scoreboard.json').write_text(
        json.dumps(rows, ensure_ascii=False, indent=1, default=float), encoding='utf-8')

    out = ['TABLE: hard benchmark, this project versus Herbie.',
           f'bits = mean log2(1 + ulp) over {SAMPLES} sampled inputs, lower is better.',
           'bound = proven worst-case absolute error, cost = our machine-cost model.', '',
           f'{"case":<16}{"written":>9}{"ours":>8}{"herbie":>8}   {"our bound":>11}{"его bound":>12}'
           f'{"our cost":>10}{"his cost":>10}']
    win = tie = loss = nohit = 0
    for r in rows:
        if r['herbie_form'] is None:
            out.append(f'{r["name"]:<16}{r["base_bits"]:9.2f}{r["ours_bits"]:8.2f}{"n/a":>8}   '
                       f'{r["ours_bound"]:11.2e}{"-":>12}{r["ours_cost"]:10.1f}{"-":>10}')
            nohit += 1
            continue
        if r['ours_bits'] < r['herbie_bits'] - 0.02:
            win += 1
        elif r['herbie_bits'] < r['ours_bits'] - 0.02:
            loss += 1
        else:
            tie += 1
        out.append(f'{r["name"]:<16}{r["base_bits"]:9.2f}{r["ours_bits"]:8.2f}{r["herbie_bits"]:8.2f}   '
                   f'{r["ours_bound"]:11.2e}{r["herbie_bound"]:12.2e}'
                   f'{r["ours_cost"]:10.1f}{r["herbie_cost"]:10.1f}')
    out += ['', f'on accuracy (his metric): we win {win}, tie {tie}, lose {loss}, '
                f'{nohit} case(s) his answer is outside our operation set.', '', 'forms:']
    for r in rows:
        out.append(f'  {r["name"]}')
        out.append(f'    ours  : {r["ours_form"]}')
        out.append(f'    herbie: {r["herbie_form"] or "outside our operation set"}')
    text_out = '\n'.join(out)
    (BENCH / 'hard_scoreboard.txt').write_text(text_out + '\n', encoding='utf-8')
    print(text_out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
