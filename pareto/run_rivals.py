# -*- coding: utf-8 -*-
"""Прогон против FPTaylor и Daisy на одних и тех же задачах FPBench.

Herbie переписывает формулу и проверяет результат выборкой. FPTaylor и Daisy — соседи
ближе: они, как и мы, **доказывают** верхнюю границу ошибки. FPTaylor делает это
символическими формами Тейлора с глобальной оптимизацией остатка, Daisy — интервальными
диапазонами с аффинной моделью ошибки. Поэтому сравнение с ними идёт не по «красивее
форма», а по единственному числу, которое обе стороны обязаны гарантировать.

Таблица считает три вещи на каждой задаче:

1. **Анализ против анализа.** Границы всех трёх инструментов для одной и той же исходной
   записи. Здесь мы честно слабее FPTaylor: у него метод тоньше.
2. **Переписывание против анализа.** Наша граница для НАШЕЙ формы против их границ для
   исходной записи — то, что видит пользователь, которому важно итоговое число.
3. **Переписывание плюс их анализ.** Их же границы, посчитанные для нашей формы. Если
   число падает, значит выигрыш даёт переписывание, а не наша модель ошибки, и результат
   не зависит от того, верят ли нашему анализатору.

Чтобы сравнение было честным, из фронта берётся форма, которую все три инструмента
понимают **одинаково**: без `fma` (FPTaylor раскрывает его в два округления, у Daisy его
нет), без `approx` (там меняется сама математика: полином вместо функции) и без
`expm1`/`log1p`/`hypot`, которых нет ни у того, ни у другого. Формы на компенсированных
преобразованиях (TwoSum и родня) остаются: в точной арифметике они тождественны исходной
записи, и соперники вольны анализировать их как обычный код.

Запуск:

    FPTAYLOR_JS=tools/fptaylor_js DAISY_HOME=/path/to/daisy python pareto/run_rivals.py

Без соответствующей переменной инструмент просто пропускается, и в таблице стоит "n/a".
Установка обоих описана в tools/README.md.
"""
from __future__ import annotations

import json
import math
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from decimal import Decimal, getcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pareto.analysis import pareto_extract, refine_front, tree_cost_refined
from pareto.codegen import to_text
from pareto.egraph import EGraph
from pareto.fpbench_cases import FPBENCH_CASES
from pareto.hard_cases import HARD_CASES
from pareto.rules import RULES
from pareto.run import exact
from pareto.run_herbie_metric import eval_float

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / 'bench'
getcontext().prec = 60

POINTS = 400
SEED = 20260925

REPORT = 'rivals_report.txt'
RESULTS = 'rivals_results.json'

# Задачи, на которых оба соперника публикуют числа и наш поиск укладывается в разумное время.
DEFAULT_CASES = ['verhulst', 'predatorPrey', 'sine', 'sqroot', 'rigidBody1',
                 'rigidBody2', 'turbine1', 'turbine2', 'turbine3', 'carbonGas']

# Второй полигон — наш тяжёлый набор. На FPBench исходная запись уже почти хороша, и вся
# разница между инструментами умещается в один-два ulp. Тяжёлые задачи взяты из мест, где
# наивная запись теряет десятки бит: там видно, что переписывание решает больше, чем
# качество анализа.
HARD_DEFAULTS = ['sq_expand', 'cube_diff', 'ratio_cancel', 'quadratic_root', 'variance',
                 'exp_minus_one', 'log_one_plus', 'exp_series']

# Операции, которые понимают все три инструмента одинаково.
SHARED_OPS = {'num', 'var', 'neg', '+', '-', '*', '/', 'sqrt'}


def shared_form(tree):
    """True, если дерево читается всеми тремя инструментами с одной и той же семантикой."""
    op = tree[0]
    if op == 'eft':          # обёртка компенсированного преобразования, код внутри обычный
        return shared_form(tree[1])
    if op == 'approx':       # приближение меняет саму функцию — соперникам его не предъявить
        return False
    if op not in SHARED_OPS:
        return False
    if op in ('num', 'var'):
        return True
    return all(shared_form(k) for k in tree[1:])


def _strip(tree):
    """Убирает служебные обёртки: снаружи остаётся ровно тот код, который исполняется."""
    if tree[0] in ('approx', 'eft'):
        return _strip(tree[1])
    if tree[0] in ('num', 'var'):
        return tree
    return (tree[0],) + tuple(_strip(k) for k in tree[1:])


def _num(v):
    """Константа в виде ТОЧНОГО десятичного разложения своего double.

    Без этого сравнение было бы нечестным в нашу пользу. Мы считаем ошибку программы,
    в которой литералы — уже числа double: `exact()` берёт эталоном `Decimal(float(c))`,
    и округление самого литерала в границу не входит. FPTaylor и Daisy читают `1.11`
    как вещественное число и честно добавляют ошибку его округления. Если отдать им
    короткую запись, они получат задачу строже нашей, а таблица покажет преимущество,
    которого нет. Точное разложение double ложится в double без округления, и все три
    инструмента начинают с одной и той же константы.
    """
    return str(Decimal(float(v)))


# Одноместные функции, которые есть и у FPTaylor, и у Daisy под теми же именами.
UNARY = ('sqrt', 'exp', 'log')


class Untranslatable(Exception):
    """Операции, которой у соперника нет: молча подменять её нельзя."""


def to_fptaylor(tree):
    """Запись дерева на входном языке FPTaylor."""
    t = _strip(tree)
    op = t[0]
    if op == 'num':
        return _num(t[1])
    if op == 'var':
        return t[1]
    if op == 'neg':
        return '(-' + to_fptaylor(t[1]) + ')'
    if op in UNARY:
        return op + '(' + to_fptaylor(t[1]) + ')'
    if op not in ('+', '-', '*', '/'):
        raise Untranslatable(op)
    return '(' + to_fptaylor(t[1]) + ' ' + op + ' ' + to_fptaylor(t[2]) + ')'


def to_scala(tree):
    """Запись дерева на Scala-диалекте Daisy (тот же синтаксис, что в их testcases)."""
    t = _strip(tree)
    op = t[0]
    if op == 'num':
        return _num(t[1])
    if op == 'var':
        return t[1]
    if op == 'neg':
        return '(-(' + to_scala(t[1]) + '))'
    if op in UNARY:
        return op + '(' + to_scala(t[1]) + ')'
    if op not in ('+', '-', '*', '/'):
        raise Untranslatable(op)
    return '(' + to_scala(t[1]) + ' ' + op + ' ' + to_scala(t[2]) + ')'


# ---------------------------------------------------------------- FPTaylor

FPTAYLOR_CFG = """print-precision = 12
verbosity = 1
"""


def fptaylor_bound(name, tree, domain, timeout=900):
    """Граница абсолютной ошибки по FPTaylor. None, если он не установлен или не справился."""
    home = os.environ.get('FPTAYLOR_JS')
    if not home:
        return None, 'n/a'
    home = Path(home)
    if not (home / 'fptaylor.js').exists():
        return None, 'n/a'

    vars_block = '\n'.join(f'  real {v} in [{_num(domain[v][0])}, {_num(domain[v][1])}];'
                           for v in sorted(domain))
    try:
        body = to_fptaylor(tree)
    except Untranslatable as e:
        return None, f'no {e} in FPTaylor'
    text = (f'Variables\n{vars_block}\n\nExpressions\n'
            f'  {name} rnd64= {body};\n')

    with tempfile.TemporaryDirectory() as td:
        inp = Path(td) / 'task.txt'
        cfg = Path(td) / 'task.cfg'
        inp.write_text(text, encoding='utf-8')
        cfg.write_text(FPTAYLOR_CFG, encoding='utf-8')
        try:
            p = subprocess.run(['node', str(home / 'run.js'), str(inp), str(cfg)],
                               capture_output=True, text=True, timeout=timeout)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None, 'timeout'
    if p.returncode != 0:
        return None, 'error'
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        return None, 'error'
    for res in data.get('results') or []:
        for err in res.get('errors') or []:
            if 'absolute' in err.get('errorName', ''):
                val = err.get('error')
                if isinstance(val, list) and val and val[0] is not None:
                    return float(val[0]), 'ok'
                try:
                    return float(err['errorStr']), 'ok'
                except (KeyError, TypeError, ValueError):
                    pass
    return None, 'no bound'


# ---------------------------------------------------------------- Daisy

DAISY_RE = re.compile(r'Absolute error:\s*([0-9.eE+-]+)')


def daisy_bound(name, tree, domain, timeout=int(__import__('os').environ.get('DAISY_TIMEOUT', 180))):
    """Граница абсолютной ошибки по Daisy (анализ по умолчанию: интервалы + аффинная ошибка)."""
    home = os.environ.get('DAISY_HOME')
    if not home:
        return None, 'n/a'
    home = Path(home)
    script = home / 'daisy'
    if not script.exists():
        return None, 'n/a'

    names = sorted(domain)
    args = ', '.join(f'{v}: Real' for v in names)
    pre = ' && '.join(f'{_num(domain[v][0])} <= {v} && {v} <= {_num(domain[v][1])}'
                      for v in names)
    try:
        body = to_scala(tree)
    except Untranslatable as e:
        return None, f'no {e} in Daisy'
    src = (f'import daisy.lang._\nimport Real._\n\n'
           f'object Task {{\n\n'
           f'  def {name}({args}): Real = {{\n'
           f'    require({pre})\n\n'
           f'    {body}\n'
           f'  }}\n\n}}\n')

    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / 'Task.scala'
        f.write_text(src, encoding='utf-8')
        cmd = ['bash', str(script), '--silent', str(f)]
        try:
            p = subprocess.run(cmd, cwd=str(home), capture_output=True, text=True,
                               timeout=timeout)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None, 'timeout'
    m = DAISY_RE.search(p.stdout + p.stderr)
    if not m:
        return None, 'no bound'
    try:
        return float(m.group(1)), 'ok'
    except ValueError:
        return None, 'no bound'


# ---------------------------------------------------------------- наш прогон


def measured_error(tree, expr, domain, rng):
    names = sorted(domain)
    pts = [{n: domain[n][0] for n in names}, {n: domain[n][1] for n in names}]
    pts += [{n: rng.uniform(*domain[n]) for n in names} for _ in range(POINTS)]
    worst = Decimal(0)
    for env in pts:
        try:
            got = eval_float(tree, env)
            ref = exact(expr, {k: Decimal(v) for k, v in env.items()})
        except (ValueError, ZeroDivisionError, OverflowError, KeyError):
            continue
        if not math.isfinite(got):
            continue
        worst = max(worst, abs(Decimal(got) - ref))
    return float(worst)


def _fmt(x, width=12):
    if x is None:
        return 'n/a'.rjust(width)
    if not math.isfinite(x):
        return 'inf'.rjust(width)
    return f'{x:.3e}'.rjust(width)


def _ratio(a, b):
    """Во сколько раз a меньше b. None, если сравнивать нечего."""
    if a is None or b is None or a <= 0 or not math.isfinite(a) or not math.isfinite(b):
        return None
    return b / a


def main(argv=None):
    argv = list(argv or [])
    hard = '--hard' in argv
    names = [a for a in argv if not a.startswith('--')]
    cases = HARD_CASES if hard else FPBENCH_CASES
    names = names or (HARD_DEFAULTS if hard else DEFAULT_CASES)
    global REPORT, RESULTS
    REPORT, RESULTS = (('rivals_hard_report.txt', 'rivals_hard_results.json') if hard
                       else ('rivals_report.txt', 'rivals_results.json'))
    if '--report-only' in argv:
        # Пересобрать таблицу из сохранённых чисел, ничего не считая заново: прогон
        # длинный, и менять вёрстку отчёта не повод гонять соперников ещё раз.
        rows = json.loads((BENCH / RESULTS).read_text(encoding='utf-8'))
        write_report(rows)
        print((BENCH / REPORT).read_text(encoding='utf-8'))
        return 0
    rows = []
    # 26.09.2026: шаг очной ставки дважды умирал в CI молча — шестнадцать минут
    # без единой строки, потом «раннер получил сигнал выключения». Без отметок
    # невозможно сказать, на ком он умер: на нашем поиске, FPTaylor или Daisy.
    def _note(m):
        print(m, flush=True)

    for name in names:
        case = cases[name]
        domain = case['domain']
        _note(f'[{name}] начал')
        rng = random.Random(SEED)

        t0 = time.perf_counter()
        _, base_bound, _, _, _ = tree_cost_refined(case['expr'], domain)
        eg = EGraph()
        root = eg.add_expr(case['expr'])
        eg.saturate(RULES, iters=case.get('iters', 6),
                    node_limit=case.get('node_limit', 20000), domain=domain)
        front, _ = pareto_extract(eg, root, domain, keep=8)
        front = refine_front(front, domain)
        search_sec = time.perf_counter() - t0
        _note(f'[{name}] наш поиск: {search_sec:.1f} с')

        finite = [p for p in front if p[1] is not None and math.isfinite(p[1])]
        best = min(finite, key=lambda p: (p[1], p[0])) if finite else None
        shared = [p for p in finite if shared_form(p[2])]
        pick = min(shared, key=lambda p: (p[1], p[0])) if shared else None

        row = {
            'name': name,
            'ours_written': base_bound,
            'ours_best': best[1] if best else None,
            'ours_shared': pick[1] if pick else None,
            'best_form': to_text(best[2]) if best else None,
            'shared_form': to_text(pick[2]) if pick else None,
            'measured_shared': (measured_error(pick[2], case['expr'], domain, rng)
                                if pick else None),
            'search_sec': search_sec,
        }

        _note(f'[{name}] FPTaylor по исходной')
        row['fptaylor_written'], row['fptaylor_written_status'] = \
            fptaylor_bound(name, case['expr'], domain)
        _note(f'[{name}] Daisy по исходной')
        row['daisy_written'], row['daisy_written_status'] = \
            daisy_bound(name, case['expr'], domain)
        if pick is not None:
            _note(f'[{name}] FPTaylor по нашей форме')
            row['fptaylor_ours'], row['fptaylor_ours_status'] = \
                fptaylor_bound(name, pick[2], domain)
            _note(f'[{name}] Daisy по нашей форме')
            row['daisy_ours'], row['daisy_ours_status'] = \
                daisy_bound(name, pick[2], domain)
        else:
            row['fptaylor_ours'] = row['daisy_ours'] = None
            row['fptaylor_ours_status'] = row['daisy_ours_status'] = 'no shared form'

        rows.append(row)
        print(f'{name:<14} ours {_fmt(row["ours_shared"])}  '
              f'fptaylor {_fmt(row["fptaylor_written"])} -> {_fmt(row["fptaylor_ours"])}  '
              f'daisy {_fmt(row["daisy_written"])} -> {_fmt(row["daisy_ours"])}')
        # Пишем после каждой задачи: прогон долгий, и прерванный должен оставить
        # готовую часть таблицы, а не пустой файл.
        write_report(rows)

    print()
    print((BENCH / REPORT).read_text(encoding='utf-8'))
    return 0


def write_report(rows):
    (BENCH / RESULTS).write_text(
        json.dumps(rows, ensure_ascii=False, indent=1, default=float), encoding='utf-8')

    out = [
        'FPTaylor и Daisy против нас на наборе FPBench (rosa). Все числа — доказанные',
        'верхние границы абсолютной ошибки для double, одна и та же исходная запись и',
        'один и тот же домен. Наша форма — лучшая с фронта Парето из тех, что все три',
        'инструмента читают одинаково (без fma, approx, expm1/log1p/hypot).',
        '',
        'Таблица 1. Анализ против анализа: граница для ИСХОДНОЙ записи.',
        f'{"case":<14}{"ours":>12}{"FPTaylor":>12}{"Daisy":>12}   кто тоньше',
    ]
    for r in rows:
        trio = [('ours', r['ours_written']), ('FPTaylor', r['fptaylor_written']),
                ('Daisy', r['daisy_written'])]
        live = [(k, v) for k, v in trio if v is not None and math.isfinite(v)]
        who = min(live, key=lambda kv: kv[1])[0] if live else '—'
        out.append(f'{r["name"]:<14}{_fmt(r["ours_written"])}{_fmt(r["fptaylor_written"])}'
                   f'{_fmt(r["daisy_written"])}   {who}')

    out += ['',
            'Таблица 2. Что получает пользователь: наша граница для НАШЕЙ формы против их',
            'границ для исходной записи (им переписывать нечем).',
            f'{"case":<14}{"ours(rewr)":>12}{"FPTaylor":>12}{"Daisy":>12}{"vs FPT":>9}{"vs Daisy":>10}']
    for r in rows:
        f_r = _ratio(r['ours_shared'], r['fptaylor_written'])
        d_r = _ratio(r['ours_shared'], r['daisy_written'])
        out.append(f'{r["name"]:<14}{_fmt(r["ours_shared"])}{_fmt(r["fptaylor_written"])}'
                   f'{_fmt(r["daisy_written"])}'
                   f'{(f"{f_r:8.2f}x" if f_r else "     n/a")}'
                   f'{(f"{d_r:9.2f}x" if d_r else "      n/a")}')

    out += ['',
            'Таблица 3. Переписывание отдельно от нашего анализа: их же границы, посчитанные',
            'для нашей формы. Падение числа — заслуга переписывания, а не нашей модели ошибки.',
            f'{"case":<14}{"FPTaylor was":>13}{"FPTaylor now":>13}{"Daisy was":>13}{"Daisy now":>13}']
    for r in rows:
        out.append(f'{r["name"]:<14}{_fmt(r["fptaylor_written"], 13)}'
                   f'{_fmt(r["fptaylor_ours"], 13)}{_fmt(r["daisy_written"], 13)}'
                   f'{_fmt(r["daisy_ours"], 13)}')

    # Проверка на месте: наша граница обязана быть не ниже измеренной ошибки на той же
    # форме, и не ниже границы FPTaylor быть не обязана — но если она ниже ЕГО, а сверху
    # ещё и ниже измеренной, это уже не «мы точнее», а дефект. Такое печатаем отдельно.
    suspect = [r['name'] for r in rows
               if r['ours_shared'] is not None and r['measured_shared'] is not None
               and math.isfinite(r['ours_shared'])
               and r['ours_shared'] < r['measured_shared']]
    out += ['',
            'Санитарная проверка: наша граница ниже измеренной ошибки на той же форме — '
            + (', '.join(suspect) if suspect else 'нигде')]

    wins_f = sum(1 for r in rows if _ratio(r['ours_shared'], r['fptaylor_written']) and
                 _ratio(r['ours_shared'], r['fptaylor_written']) > 1.0)
    wins_d = sum(1 for r in rows if _ratio(r['ours_shared'], r['daisy_written']) and
                 _ratio(r['ours_shared'], r['daisy_written']) > 1.0)
    better_f = sum(1 for r in rows if r['fptaylor_ours'] is not None
                   and r['fptaylor_written'] is not None
                   and r['fptaylor_ours'] < r['fptaylor_written'] * 0.999)
    better_d = sum(1 for r in rows if r['daisy_ours'] is not None
                   and r['daisy_written'] is not None
                   and r['daisy_ours'] < r['daisy_written'] * 0.999)
    out += ['',
            f'наша форма даёт границу ниже, чем FPTaylor на исходной записи: {wins_f} из {len(rows)}',
            f'то же против Daisy: {wins_d} из {len(rows)}',
            f'наша форма улучшает границу САМОГО FPTaylor: {better_f} из {len(rows)}',
            f'наша форма улучшает границу САМОЙ Daisy: {better_d} из {len(rows)}',
            f'суммарное время поиска: {sum(r["search_sec"] for r in rows):.1f} c']

    (BENCH / REPORT).write_text('\n'.join(out) + '\n', encoding='utf-8')


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
