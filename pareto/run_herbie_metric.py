# -*- coding: utf-8 -*-
"""Head-to-head with Herbie on Herbie's own metric, not on ours.

The obvious objection to the previous comparison is fair: we scored Herbie's forms
with our worst-case bound and our cost model, and winning under one's own metric is
not winning. So here the ruler is Herbie's: average bits of error over sampled
inputs, where bits = log2(1 + ulp distance between the computed double and the
correctly rounded exact value).

Exactness comes from a 60-digit Decimal evaluation of the ORIGINAL expression, then
rounded to the nearest double. Both our chosen form and Herbie's output are measured
against that same reference, on the same sample points, with a fixed seed.

One honest caveat: Herbie samples inputs over the floating-point representation,
this script samples uniformly over the value range given by the domain. On wide
domains (diff_sqrt spans 1e6..1e9) the two differ, and the numbers here are the
value-uniform variant.

    python pareto/run_herbie_metric.py
"""
from __future__ import annotations

import json
import math
import random
import struct
import sys
from decimal import Decimal, getcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pareto.codegen import to_text
from pareto.parser import parse
from pareto.run import CASES, exact

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / 'bench'
getcontext().prec = 60

SAMPLES = 4000
SEED = 20260925


def as_int(x):
    """Monotone integer image of a double: adjacent doubles differ by one."""
    bits = struct.unpack('<q', struct.pack('<d', x))[0]
    return bits if bits >= 0 else ~bits + (1 << 63) * 0 - (bits - (-(1 << 63)))


def ulp_distance(a, b):
    """How many doubles lie between a and b. NaN and infinities are penalised hard."""
    if math.isnan(a) or math.isnan(b) or math.isinf(a) or math.isinf(b):
        return float(2 ** 62)
    ia, ib = _ordered(a), _ordered(b)
    return abs(ia - ib)


def _ordered(x):
    """Doubles as a continuous ordered integer line (sign-magnitude to two's complement)."""
    bits = struct.unpack('<Q', struct.pack('<d', x))[0]
    if bits & (1 << 63):
        return -(bits & ~(1 << 63))
    return bits


def eval_float(tree, env):
    op = tree[0]
    if op == 'num':
        return float(tree[1])
    if op == 'var':
        return float(env[tree[1]])
    if op == 'neg':
        return -eval_float(tree[1], env)
    if op == 'sqrt':
        return math.sqrt(eval_float(tree[1], env))
    if op == 'exp':
        return math.exp(eval_float(tree[1], env))
    if op == 'log':
        return math.log(eval_float(tree[1], env))
    a = eval_float(tree[1], env)
    b = eval_float(tree[2], env)
    if op == '+':
        return a + b
    if op == '-':
        return a - b
    if op == '*':
        return a * b
    if op == '/':
        return a / b
    if op == 'fma':
        c = eval_float(tree[3], env)
        return math.fma(a, b, c) if hasattr(math, 'fma') else a * b + c
    raise AssertionError('unknown node: ' + op)


def bits_of_error(form, case, points):
    """Herbie's ruler: mean log2(1 + ulps) against the correctly rounded exact value."""
    total = 0.0
    worst = 0.0
    for env in points:
        dec_env = {k: Decimal(float(v)) for k, v in env.items()}
        ref = float(exact(case['expr'], dec_env))        # exact, then rounded to double
        try:
            got = eval_float(form, env)
        except (ValueError, OverflowError, ZeroDivisionError):
            got = float('nan')
        bits = math.log2(1.0 + ulp_distance(got, ref))
        total += bits
        worst = max(worst, bits)
    return total / len(points), worst


def sample(case, rng):
    dom = case['domain']
    names = sorted(dom)
    return [{n: rng.uniform(*dom[n]) for n in names} for _ in range(SAMPLES)]


def main():
    stored = json.loads((BENCH / 'herbie_results.json').read_text(encoding='utf-8'))
    rows = []
    for rec in stored:
        name = rec['name']
        case = CASES[name]
        rng = random.Random(SEED)
        pts = sample(case, rng)

        forms = {'original': case['expr'],
                 'ours': parse(rec['our_form']),
                 'herbie': parse(rec['herbie_form'])}
        res = {}
        for key, tree in forms.items():
            mean, worst = bits_of_error(tree, case, pts)
            res[key] = {'form': to_text(tree), 'mean_bits': mean, 'worst_bits': worst}
        rows.append({'name': name, **res})

    (BENCH / 'herbie_metric_results.json').write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')

    out = ['Head-to-head on HERBIE\'S metric: average bits of error over '
           f'{SAMPLES} sampled inputs (lower is better).',
           'Reference: 60-digit Decimal evaluation of the original expression, rounded to double.',
           '', f'{"case":<12}{"original":>10}{"ours":>10}{"herbie":>10}   verdict']
    wins = ties = losses = 0
    for r in rows:
        o, us, hb = r['original']['mean_bits'], r['ours']['mean_bits'], r['herbie']['mean_bits']
        if us < hb - 0.05:
            verdict, _ = 'ours better', wins
            wins += 1
        elif hb < us - 0.05:
            verdict = 'HERBIE BETTER'
            losses += 1
        else:
            verdict = 'tie'
            ties += 1
        out.append(f'{r["name"]:<12}{o:10.3f}{us:10.3f}{hb:10.3f}   {verdict}')
    out += ['', f'ours better on {wins}, tie on {ties}, Herbie better on {losses} '
                f'out of {len(rows)} cases.',
            '', 'Forms compared:']
    for r in rows:
        out.append(f'  {r["name"]}')
        out.append(f'    ours  : {r["ours"]["form"]}')
        out.append(f'    herbie: {r["herbie"]["form"]}')
    text = '\n'.join(out)
    (BENCH / 'herbie_metric_report.txt').write_text(text + '\n', encoding='utf-8')
    print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
