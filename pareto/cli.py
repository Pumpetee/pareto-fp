# -*- coding: utf-8 -*-
"""Вход для человека, у которого есть своё выражение или свой файл.

    python -m pareto.cli "sqrt(x+1) - sqrt(x)" --domain x=1e6..1e9
    python -m pareto.cli "x*x - y*y" --domain x=1..2 --domain y=1..2 --json
    python -m pareto.cli --file kernel.c --function turbine1
    python -m pareto.cli "x*x - y*y" --domain x=1..2 --domain y=1..2 --target 1e-14
    python -m pareto.cli --file kernel.c --function turbine1 --require 1e-13
    python -m pareto.cli --file kernel.c --function turbine1 -o kernel_fast.c

Коды возврата. Обычный прогон — 0, ошибка в аргументах или в разборе — 64. При
`--require` код возврата и есть ответ, поэтому он различает три исхода: 0 —
требование держится как написано, 1 — как написано не держится, но найденная
форма держит, 2 — не держит ни одна найденная форма.

Нужен только Python: ни node, ни clang. Компилятор участвует лишь в замерах
(`pareto/run_fairbench.py`), здесь считается модель.

Вывод английский и ASCII намеренно. Консоль Windows по умолчанию в cp1252, и
самый первый прогон в CI умер ровно на этом: неASCII-байт в stdout.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pareto import budget as _budget
from pareto.api import (analyse_c_function, analyse_expression, check_requirement,
                        precision_report, rewritten_c)
from pareto.apply import rewrite_source, unified_diff
from pareto.cfront import CParseError
from pareto.parser import ParseError, parse, variables
from pareto.program import ProgramError


EX_USAGE = 64   # sysexits.h: ошибка в том, о чём попросили, а не ответ на вопрос


def die(message):
    """Пользовательская ошибка: сообщение в stderr, код 64.

    Не 1 и не 2, потому что оба заняты ответом `--require`: скрипт сборки обязан
    отличать «требование не держится» от «ты опечатался во флаге».
    """
    sys.stderr.write('pareto-fp: error: {}\n'.format(message))
    raise SystemExit(EX_USAGE)


def parse_domain(items, vars_=(), required=True):
    """`x=1..2` или `x=1e6..1e9`. Без диапазонов доказывать границу нечем."""
    dom = {}
    for it in items or []:
        if '=' not in it or '..' not in it:
            die('a range is written as x=1..2, got: ' + it)
        name, rng = it.split('=', 1)
        lo, hi = rng.split('..', 1)
        try:
            dom[name.strip()] = (float(lo), float(hi))
        except ValueError:
            die('range bounds must be numbers: ' + it)
    if required:
        missing = [v for v in vars_ if v not in dom]
        if missing:
            die('no range given for: {}. Example: --domain {}=1..2'.format(
                ', '.join(missing), missing[0]))
    return dom


# Оставлено для совместимости: этим именем пользуются замерочные скрипты и тесты.
def analyse(text, dom, keep=10, iters=10, budget=2.0, cost_budget=1.5, refine=False):
    return analyse_expression(text, dom, keep=keep, iters=iters, budget=budget,
                              cost_budget=cost_budget, refine=refine)


def render(r):
    b = r['base']
    out = ['input expression: ' + r['input'],
           'model cost: {:.1f} | proven error bound: {:.3e}'.format(b['cost'], b['err']), '',
           'Pareto front (every non-dominated form):',
           '{:>9} {:>12}  {}'.format('cost', 'bound', 'form')]
    for p in r['front']:
        out.append('{:>9.1f} {:>12.3e}  {}'.format(p['cost'], p['err'], p['form']))
    out += ['']
    fa, ex = r['fastest'], r['most_exact']
    speed = (b['cost'] / fa['cost']) if fa['cost'] else float('inf')
    out.append('cheapest form   : {}'.format(fa['form']))
    out.append('                  {:.2f}x cheaper, error bound {:.3e}'.format(speed, fa['err']))
    out.append('most accurate   : {}'.format(ex['form']))
    if b['err'] > 0 and ex['err'] > 0:
        out.append('                  bound {:.3g}x smaller, costs {:.2f}x'.format(
            b['err'] / ex['err'], (ex['cost'] / b['cost']) if b['cost'] else 1.0))
    elif ex['err'] == 0:
        out.append('                  model error is zero, costs {:.2f}x'.format(
            (ex['cost'] / b['cost']) if b['cost'] else 1.0))
    out += ['', 'paste into code : ' + fa['c']]
    if r.get('precision'):
        out += [''] + render_precision(r['precision'])
    if r.get('cut'):
        out += ['', 'the time budget cut these stages, so the bound is looser than it '
                    'could be: ' + ', '.join(r['cut'])]
    return '\n'.join(out)


def render_precision(p):
    out = ['Precision:', '{:>10} {:>12}  {}'.format('format', 'bound', 'note')]
    for u in p['uniform']:
        out.append('{:>10} {:>12.3e}  {}'.format(
            u['format'], u['err'], 'every operation in this format'))
    t = p.get('tuned')
    if t:
        out += ['']
        if t['meets']:
            out.append('narrowest mix that still meets {:.3e}: bound {:.3e}, '
                       '{} subexpression(s) narrowed'.format(
                           t['target'], t['err'], t['narrowed']))
            out.append('                  ' + t['c'])
        else:
            out.append('no assignment of formats meets {:.3e}: even binary64 everywhere '
                       'gives {:.3e}'.format(t['target'], t['err']))
    return out


def render_function(r, show_code=True):
    out = ['function        : {} -> {}'.format(r['function'], r['result_format']),
           'input ranges    : ' + ', '.join(
               '{} in [{:g}, {:g}]'.format(k, v[0], v[1]) for k, v in r['domain'].items()),
           'execution paths : {} ({} boxes used to decide the conditions)'.format(
               len(r['paths']), r['boxes']), '']
    out.append('{:<8} {:>12} {:>12}  {}'.format('path', 'as written', 'rewritten', 'condition'))
    for p in r['paths']:
        cond = ' and '.join(p['guards_text']) or '(always)'
        if not p['reachable']:
            out.append('{:<8} {:>12} {:>12}  {}'.format(
                p['label'], 'unreachable', 'unreachable', cond))
            continue
        out.append('{:<8} {:>12.3e} {:>12.3e}  {}'.format(
            p['label'], p['base'], p['best'], cond))
    out += ['']
    if r['divergence']:
        out.append('Unstable comparisons. The condition is decided on COMPUTED values, so')
        out.append('near the boundary the program may take the other branch. Then the error')
        out.append('includes the whole jump between the branches, not just rounding:')
        for d in r['divergence']:
            out.append('  {} vs {}: jump up to {:.3e} on {} box(es)'.format(
                d['paths'][0], d['paths'][1], d['gap'], d['boxes']))
        out += ['']
    out.append('proven bound as written : {:.3e}'.format(r['base_bound']))
    out.append('proven bound rewritten  : {:.3e}'.format(r['best_bound']))
    if r['divergence_extra'] > 0:
        out.append('  of which rounding     : {:.3e} as written, {:.3e} rewritten'.format(
            r['stable_base'], r['stable_best']))
        out.append('  of which branch jump  : {:.3e}'.format(r['divergence_extra']))
    import math as _math
    if (r['base_bound'] > 0 and r['best_bound'] > 0
            and _math.isfinite(r['base_bound']) and _math.isfinite(r['best_bound'])):
        out.append('  improvement           : {:.3g}x tighter'.format(
            r['base_bound'] / r['best_bound']))
    elif not _math.isfinite(r['base_bound']):
        # Бесконечная граница — это не «очень плохо», а «доказать не удалось»:
        # где-то по дороге интервал накрыл ноль в знаменателе или величина ушла за
        # пределы формата. Делить одну бесконечность на другую и печатать nan было
        # бы издевательством над читателем.
        out.append('  the bound could not be established: somewhere on the way an '
                   'interval covered a division by zero or the value left the range of '
                   'the format. Narrow the input ranges and try again')
    if show_code:
        out += ['', 'rewritten body:', rewritten_c(r)]
    if r.get('cut'):
        out += ['', 'the time budget cut these stages, so the bound is looser than it '
                    'could be: ' + ', '.join(r['cut'])]
    return '\n'.join(out)


def main(argv=None):
    # Консоль Windows по умолчанию cp1252 — принудительно UTF-8, чтобы вывод
    # никогда не падал на одном байте.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            try:
                stream.reconfigure(encoding='utf-8', errors='replace')
            except Exception:
                pass

    ap = argparse.ArgumentParser(
        prog='pareto-fp',
        description='Rewrites a numeric expression, or a whole function from a C file, into '
                    'an equivalent one: cheaper or more accurate, with a proven error bound.')
    ap.add_argument('expr', nargs='?', help='the expression, e.g. "sqrt(x+1) - sqrt(x)"')
    ap.add_argument('--file', metavar='PATH',
                    help='read a function from a C file instead of an expression')
    ap.add_argument('--function', metavar='NAME',
                    help='which function of the file to analyse (default: the first one)')
    ap.add_argument('--list', action='store_true',
                    help='list the functions found in the file and exit')
    ap.add_argument('--domain', action='append', metavar='x=1..2',
                    help='range of a variable, repeat the flag for each one')
    ap.add_argument('--budget', type=float, default=2.0,
                    help='how much accuracy may be traded for speed, as a factor (default 2)')
    ap.add_argument('--cost-budget', type=float, default=1.5,
                    help='how much cost may be traded for accuracy, as a factor (default 1.5)')
    ap.add_argument('--keep', type=int, default=10, help='how many front points to show')
    ap.add_argument('--iters', type=int, default=10, help='e-graph saturation iterations')
    ap.add_argument('--time-budget', type=float, default=None, metavar='SECONDS',
                    help='wall-clock target, 30 by default, 0 for no limit. Checked between '
                         'stages and never in the middle of one, so the real time can exceed '
                         'it by the length of the stage in flight. Cutting the search short '
                         'only loosens the bound, it never makes it wrong: every stage it '
                         'skips would have offered a better candidate or a tighter estimate, '
                         'never a valid one. Stages that were skipped are named in the output')
    ap.add_argument('--target', type=float, default=None, metavar='BOUND',
                    help='find the narrowest floating-point formats whose proven bound still '
                         'stays under this value')
    ap.add_argument('--precision', action='store_true',
                    help='also report the bound with every operation in float32 and float16')
    ap.add_argument('--no-refine', action='store_true',
                    help='skip domain branching (faster, looser bound)')
    ap.add_argument('--json', action='store_true', help='machine-readable output')
    ap.add_argument('--require', type=float, default=None, metavar='BOUND',
                    help='check that the error provably stays under this value and say so '
                         'with the exit code: 0 the code as written already does, 1 it does '
                         'not but the rewritten form does, 2 no form found does. This is the '
                         'mode for a build or a pre-commit check')
    ap.add_argument('-o', '--output', metavar='PATH',
                    help='write the whole file with the rewritten function body into PATH '
                         '(- for stdout). The same path as the input means in place. '
                         'Everything else in the file is kept byte for byte')
    ap.add_argument('--diff', action='store_true',
                    help='print the change to the file as a unified diff instead of a report')
    # Ошибка в аргументах не должна выглядеть как ответ проверки: у `--require`
    # код 2 означает «требование недостижимо», и путать его с опечаткой в флаге
    # нельзя. Поэтому пользовательские ошибки уходят на 64 (EX_USAGE).
    def _error(message):
        ap.print_usage(sys.stderr)
        ap.exit(EX_USAGE, '{}: error: {}\n'.format(ap.prog, message))

    ap.error = _error
    a = ap.parse_args(argv)

    if not a.expr and not a.file:
        ap.error('give an expression or --file PATH')
    if a.expr and a.file:
        ap.error('give either an expression or --file, not both')
    if (a.output or a.diff) and not a.file:
        ap.error('--output and --diff rewrite a file, so they need --file PATH')

    # Часы по умолчанию есть, и это решение в пользу человека, а не в пользу цифры.
    # Полный прогон отдельных задач FPBench занимает минуты, и первый запуск на своём
    # файле не должен выглядеть как зависание. Тридцати секунд хватает на всё, кроме
    # самых тяжёлых выражений, а на них честнее отдать более широкую границу и назвать,
    # что не досчитали. Кому нужно предельное качество — `--time-budget 0`.
    chosen = a.time_budget
    if chosen is None:
        chosen = _budget.default_seconds()
    if chosen is None:
        chosen = 30.0
    _budget.set_budget(chosen)

    if a.file:
        return _run_file(a)
    return _run_expr(a)


def render_requirement(q):
    """Вердикт проверки. Формулировки нарочно без «ошибка равна»: речь о границе."""
    out = ['', 'Requirement: error must provably stay under {:.3e}'.format(q['required'])]
    out.append('  as written  : {:.3e}{}'.format(
        q['as_written'], '   MET' if q['verdict'] == 'met' else '   NOT MET'))
    if q['verdict'] == 'met':
        out.append('verdict: met as written, nothing to change')
        return out
    out.append('  rewritten   : {:.3e}{}'.format(
        q['rewritten'], '   MET' if q['verdict'] == 'needs_rewrite' else '   NOT MET'))
    if q['verdict'] == 'needs_rewrite':
        out.append('verdict: not met as written; the rewritten form meets it')
        if q.get('form'):
            out.append('           ' + q['form'])
    else:
        out.append('verdict: no form found meets it. Either the requirement is below what '
                   'this arithmetic can')
        out.append('           deliver on these ranges, or the ranges are wider than the '
                   'code really sees')
    return out


def _run_expr(a):
    try:
        tree = parse(a.expr)
    except ParseError as e:
        die('could not parse the expression: {}'.format(e))
    dom = parse_domain(a.domain, variables(tree))

    r = analyse_expression(tree, dom, keep=a.keep, iters=a.iters, budget=a.budget,
                           cost_budget=a.cost_budget, refine=not a.no_refine)
    if a.precision or a.target is not None:
        r['precision'] = precision_report(tree, dom, target=a.target)
    r['cut'] = _budget.cut_stages()

    code = 0
    if a.require is not None:
        # Для проверки берём САМУЮ тугую границу на фронте, а не ту форму, которую
        # инструмент предложил бы по умолчанию: вопрос звучит «можно ли вообще это
        # доказать», и отвечать на него формой, выбранной по компромиссу с
        # быстродействием, было бы подменой вопроса.
        best = min(r['front'], key=lambda x: x['err'])
        r['requirement'] = check_requirement(a.require, r['base']['err'], best['err'],
                                             best_form=best['c'])
        code = r['requirement']['exit_code']

    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        text = render(r)
        if r.get('requirement'):
            text += '\n' + '\n'.join(render_requirement(r['requirement']))
        print(text)
    return code


def _run_file(a):
    path = Path(a.file)
    try:
        src = path.read_text(encoding='utf-8')
    except OSError as e:
        die('cannot read {}: {}'.format(path, e))

    if a.list:
        from pareto.cfront import functions, read_domains
        names = functions(src)
        if not names:
            die('no function returning double or float found in ' + str(path))
        doms = read_domains(src)
        print('functions in {}: {}'.format(path.name, ', '.join(names)))
        if doms:
            print('ranges from comments: ' + ', '.join(
                '{} in [{:g}, {:g}]'.format(k, v[0], v[1]) for k, v in doms.items()))
        else:
            print('no @domain comments found; pass ranges with --domain x=1..2')
        return 0

    dom = parse_domain(a.domain, required=False)
    try:
        r = analyse_c_function(src, a.function, dom=dom, keep=a.keep, iters=a.iters,
                               refine=not a.no_refine)
    except (CParseError, ProgramError) as e:
        die('cannot analyse this function: {}'.format(e))
    r['cut'] = _budget.cut_stages()

    code = 0
    if a.require is not None:
        r['requirement'] = check_requirement(a.require, r['base_bound'], r['best_bound'])
        code = r['requirement']['exit_code']

    new_src = notes = None
    if a.output or a.diff:
        new_src, notes = rewrite_source(src, r)

    if a.json:
        printable = dict(r)
        printable['paths'] = [{k: v for k, v in p.items()
                               if k not in ('expr', 'form', 'guards')} for p in r['paths']]
        printable['divergence'] = [{'paths': d['paths'], 'gap': d['gap'],
                                    'boxes': d['boxes']} for d in r['divergence']]
        printable['result_c'] = rewritten_c(r)
        if new_src is not None:
            printable['rewritten_file'] = new_src
            printable['notes'] = notes
        print(json.dumps(printable, ensure_ascii=False, indent=2))
        return code

    if a.diff:
        d = unified_diff(src, new_src, path=str(path))
        print(d if d.strip() else 'no change: the function is already in its best form')
        for n in notes:
            print('note: ' + n)
        if a.output is None:
            return code

    if a.output:
        if a.output == '-':
            sys.stdout.write(new_src)
        else:
            out = Path(a.output)
            try:
                out.write_text(new_src, encoding='utf-8')
            except OSError as e:
                die('cannot write {}: {}'.format(out, e))
            for n in notes:
                print('note: ' + n)
            print('wrote {} with the body of {} rewritten; the rest of the file is '
                  'unchanged'.format(out, r['function']))
        return code

    text = render_function(r)
    if r.get('requirement'):
        text += '\n' + '\n'.join(render_requirement(r['requirement']))
    print(text)
    return code


if __name__ == '__main__':
    sys.exit(main())
