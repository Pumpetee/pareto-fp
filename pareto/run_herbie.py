# -*- coding: utf-8 -*-
"""Сравнение с Herbie — инструментом, который улучшает точность формул.

Herbie решает половину нашей задачи: он ищет численно устойчивую форму, но не
считает стоимость и не даёт доказанной границы ошибки. Сравнение с ним — первое,
что спросят в компиляторном и научном сообществе, поэтому оно должно быть в
репозитории, даже если местами не в нашу пользу.

Что делает скрипт: пишет кейсы в FPCore, зовёт `herbie improve`, забирает
предложенные формы и считает по ним нашу же модель ошибки и стоимости.

    HERBIE_RACKET="C:\\Program Files\\Racket\\racket.exe" python pareto/run_herbie.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pareto.analysis import pareto_extract, tree_cost
from pareto.codegen import to_text
from pareto.egraph import EGraph
from pareto.parser import variables
from pareto.rules import RULES
from pareto.run import CASES

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / 'bench'
# herbie не открывает пути с кириллицей, работаем во временной папке
WORK = Path(tempfile.gettempdir()) / 'pareto_herbie'
WORK.mkdir(parents=True, exist_ok=True)

FPCORE_OPS = {'+': '+', '-': '-', '*': '*', '/': '/'}
# expm1/log1p/hypot читаются в обе стороны: раньше ответы Herbie с ними считались
# «вне нашего подмножества», и сравнение молча теряло те самые кейсы, где он нас бил
FPCORE_FUN = {'sqrt': 'sqrt', 'exp': 'exp', 'log': 'log',
              'expm1': 'expm1', 'log1p': 'log1p'}
FPCORE_BIN_FUN = {'hypot': 'hypot'}


def find_racket():
    env = os.environ.get('HERBIE_RACKET')
    if env and os.path.exists(env):
        return env
    found = shutil.which('racket')
    if found:
        return found
    for p in (r'C:\Program Files\Racket\racket.exe', '/usr/bin/racket', '/usr/local/bin/racket'):
        if os.path.exists(p):
            return p
    raise SystemExit('не нашёл racket. Herbie ставится так: raco pkg install --auto herbie')


def to_fpcore(tree):
    op = tree[0]
    if op == 'num':
        return repr(float(tree[1]))
    if op == 'var':
        return tree[1]
    if op == 'neg':
        return '(- {})'.format(to_fpcore(tree[1]))
    if op in FPCORE_FUN:
        return '({} {})'.format(FPCORE_FUN[op], to_fpcore(tree[1]))
    if op in FPCORE_BIN_FUN:
        return '({} {} {})'.format(
            FPCORE_BIN_FUN[op], to_fpcore(tree[1]), to_fpcore(tree[2]))
    if op == 'fma':
        return '(fma {} {} {})'.format(*[to_fpcore(k) for k in tree[1:]])
    return '({} {} {})'.format(FPCORE_OPS[op], to_fpcore(tree[1]), to_fpcore(tree[2]))


def case_to_fpcore(name, case):
    tree, dom = case['expr'], case['domain']
    vs = variables(tree)
    pre = ' '.join('(<= {lo} {v} {hi})'.format(v=v, lo=repr(dom[v][0]), hi=repr(dom[v][1])) for v in vs)
    return '(FPCore ({vars}) :name "{name}" :precision binary64 :pre (and {pre})\n  {body})\n'.format(
        vars=' '.join(vs), name=name, pre=pre, body=to_fpcore(tree))


# ---------- разбор того, что вернул Herbie ----------
def sexp_tokens(s):
    return re.findall(r'\(|\)|[^\s()]+', s)


def parse_sexp(toks, i=0):
    if toks[i] != '(':
        return toks[i], i + 1
    out, i = [], i + 1
    while toks[i] != ')':
        node, i = parse_sexp(toks, i)
        out.append(node)
    return out, i + 1


def sexp_to_tree(node):
    """FPCore-выражение → наше дерево. Возвращает None, если встретилось незнакомое."""
    if isinstance(node, str):
        try:
            return ('num', float(node))
        except ValueError:
            return ('var', node)
    if not node:
        return None
    head = node[0]
    args = [sexp_to_tree(a) for a in node[1:]]
    if any(a is None for a in args):
        return None
    if head in ('+', '-', '*', '/') and len(args) == 2:
        return (head, args[0], args[1])
    if head == '-' and len(args) == 1:
        return ('neg', args[0])
    if head in FPCORE_FUN and len(args) == 1:
        return (head, args[0])
    if head == 'fma' and len(args) == 3:
        return ('fma', args[0], args[1], args[2])
    if head in FPCORE_BIN_FUN and len(args) == 2:
        return (head, args[0], args[1])
    return None       # let*, if и прочее вне нашего подмножества


def herbie_body(text, name):
    """Из выданного Herbie FPCore достаём тело нужной функции.

    Имя ищется строго по полю `:name "X"` внутри разобранного блока, а не по первым
    шестидесяти токенам исходного текста. Старый способ промахивался на кейсах с
    широким доменом: границы вида 1e150 Herbie печатает целым числом на полторы сотни
    цифр, окно уезжает, и телом `exp_series` в отчёте оказался `log1p(x)` из соседнего
    блока. Сравнение с чужим инструментом по перепутанным формам хуже, чем его
    отсутствие.
    """
    for m in re.finditer(r'\(FPCore', text):
        node, _ = parse_sexp(sexp_tokens(text[m.start():]))
        if not isinstance(node, list) or node[0] != 'FPCore':
            continue
        found = None
        for i, tok in enumerate(node):
            if tok == ':name' and i + 1 < len(node):
                found = str(node[i + 1]).strip('"')
                break
        if found != name:
            continue
        return node[-1]
    return None


def run(names, prefix='herbie'):
    racket = find_racket()
    src = WORK / 'cases.fpcore'
    out = WORK / 'improved.fpcore'
    src.write_text(''.join(case_to_fpcore(n, CASES[n]) for n in names), encoding='utf-8')

    r = subprocess.run([racket, '-l', 'herbie', '--', 'improve', str(src), str(out)],
                       capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5400)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError('herbie improve: ' + ((r.stderr or r.stdout or '')[-2500:]))
    text = out.read_text(encoding='utf-8')
    # префикс разводит полигоны: прогон трудного набора не должен затирать
    # результаты обычного, они нужны обоим отчётам одновременно
    shutil.copy(src, BENCH / (prefix + '_cases.fpcore'))
    shutil.copy(out, BENCH / (prefix + '_improved.fpcore'))

    rows = []
    for name in names:
        case = CASES[name]
        tree, dom = case['expr'], case['domain']
        b_cost, b_err, _, _, _ = tree_cost(tree, dom)

        eg = EGraph()
        root = eg.add_expr(tree)
        eg.saturate(RULES, iters=case.get('iters', 10), domain=dom)
        front, _ = pareto_extract(eg, root, dom, keep=10)
        ours = min(front, key=lambda p: (p[1], p[0]))       # наша самая точная форма

        node = herbie_body(text, name)
        h_tree = sexp_to_tree(node) if node is not None else None
        if h_tree is None:
            rows.append({'name': name, 'base_err': b_err, 'base_cost': b_cost,
                         'our_form': to_text(ours[2]), 'our_err': ours[1], 'our_cost': ours[0],
                         'herbie_form': None, 'note': 'форма Herbie вне нашего подмножества операций'})
            continue
        h_cost, h_err, _, _, _ = tree_cost(h_tree, dom)
        # Честное сравнение: у Herbie одна форма, у нас фронт. Вопрос не «кто лучше
        # в одной точке», а есть ли у нас точка НЕ ХУЖЕ его по обеим метрикам сразу.
        dom_pts = [p for p in front if p[0] <= h_cost and p[1] <= h_err]
        strictly = [p for p in dom_pts if p[0] < h_cost or p[1] < h_err]
        best_match = min(dom_pts, key=lambda p: (p[1], p[0])) if dom_pts else None
        rows.append({'name': name, 'base_err': b_err, 'base_cost': b_cost,
                     'our_form': to_text(ours[2]), 'our_err': ours[1], 'our_cost': ours[0],
                     'herbie_form': to_text(h_tree), 'herbie_err': h_err, 'herbie_cost': h_cost,
                     'dominates': bool(strictly), 'matches': bool(dom_pts),
                     'match_form': to_text(best_match[2]) if best_match else None,
                     'match_err': best_match[1] if best_match else None,
                     'match_cost': best_match[0] if best_match else None,
                     'front_size': len(front), 'note': ''})
    return rows


def fmt(rows):
    s = ['=' * 100, 'СРАВНЕНИЕ С HERBIE (оценка по нашей модели: граница ошибки и стоимость)', '']
    for r in rows:
        s.append('КЕЙС: ' + r['name'])
        s.append('  исходная : граница {:.3e} · стоимость {:.1f}'.format(float(r['base_err']), r['base_cost']))
        s.append('  наша     : {}'.format(r['our_form']))
        s.append('             граница {:.3e} · стоимость {:.1f}'.format(float(r['our_err']), r['our_cost']))
        if r['herbie_form']:
            s.append('  Herbie   : {}'.format(r['herbie_form']))
            s.append('             граница {:.3e} · стоимость {:.1f}'.format(
                float(r['herbie_err']), r['herbie_cost']))
            better = 'наша точнее' if r['our_err'] < r['herbie_err'] else (
                'Herbie точнее' if r['herbie_err'] < r['our_err'] else 'точность равна')
            cheaper = 'наша дешевле' if r['our_cost'] < r['herbie_cost'] else (
                'Herbie дешевле' if r['herbie_cost'] < r['our_cost'] else 'стоимость равна')
            s.append('  точка в точку: {} · {}'.format(better, cheaper))
            if r.get('dominates'):
                s.append('  ИТОГ     : на нашем фронте ({} точек) есть форма СТРОГО не хуже по обеим метрикам:'
                         .format(r['front_size']))
                s.append('             {} · граница {:.3e} · стоимость {:.1f}'.format(
                    r['match_form'], float(r['match_err']), r['match_cost']))
            elif r.get('matches'):
                s.append('  ИТОГ     : на фронте есть равная ему точка, лучше нет')
            else:
                s.append('  ИТОГ     : форму Herbie наш фронт НЕ покрывает — он нашёл то, чего у нас нет')
        else:
            s.append('  Herbie   : {}'.format(r['note']))
        s.append('')
    return '\n'.join(s)


if __name__ == '__main__':
    # --hard переключает на трудный полигон: на исходных семи кейсах оба инструмента
    # упираются в пол двойной точности, и сравнение там меряет шум, а не качество.
    args = sys.argv[1:]
    if '--hard' in args:
        args.remove('--hard')
        from pareto.hard_cases import HARD_CASES
        CASES.update(HARD_CASES)
        names = args or list(HARD_CASES)
        BENCH_PREFIX = 'herbie_hard'
    else:
        names = args or [n for n in CASES if n not in getattr(sys.modules.get(
            'pareto.hard_cases', object), 'HARD_CASES', {})]
        BENCH_PREFIX = 'herbie'
    rows = run(names, BENCH_PREFIX)
    text = fmt(rows)
    print(text)
    (BENCH / (BENCH_PREFIX + '_report.txt')).write_text(text + '\n', encoding='utf-8')
    (BENCH / (BENCH_PREFIX + '_results.json')).write_text(
        json.dumps(rows, ensure_ascii=False, indent=1, default=float), encoding='utf-8')
    print('\nотчёт:', BENCH / (BENCH_PREFIX + '_report.txt'),
          '· формулы:', BENCH / (BENCH_PREFIX + '_improved.fpcore'))
