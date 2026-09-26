# -*- coding: utf-8 -*-
"""Сборка готовых к запуску файлов: один .pyz и три самостоятельных бинарника.

Почему это вообще отдельная задача. У репозитория может быть зелёная проверка и
воспроизводимые числа, а чужими руками он всё равно не запускается, если первый
шаг звучит как «поставь Python, склонируй, разберись с путями». Ноль звёзд и ноль
обращений — это ровно про порог входа, а не про качество границ.

Собираются две разные вещи, и обе нужны:

* **pareto-fp.pyz** — архив Python-приложения. Один файл, весит десятки килобайт,
  работает на любой системе, где есть Python 3.10 и старше, собирается за секунду
  и не зависит ни от какого стороннего инструмента. Это главный артефакт: у людей,
  которым интересен численный код, Python есть почти всегда.
* **бинарник под систему** — для тех, у кого Python нет или ставить его нельзя.
  Собирается PyInstaller-ом, и обязательно НА той системе, под которую нужен:
  кросс-сборки PyInstaller не делает. Поэтому в CI это три отдельных задания.

    python tools/build_release.py           # только .pyz
    python tools/build_release.py --binary  # плюс бинарник под текущую систему
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / 'dist'


# Что попадает в артефакт. Замерочные драйверы (`run_*.py`, кроме самого `run.py`
# с его набором кейсов) и генераторы отчётов человеку с чужим файлом не нужны:
# они тянут за собой node, clang, пути к bench и наборы бенчмарков. Оставляем
# только то, без чего утилита не посчитает: анализ, поиск, печать, разбор C,
# программы с ветвлениями, эталон для самопроверки.
PRODUCT_MODULES = (
    'affine', 'analysis', 'api', 'budget', 'cfront', 'cli', 'codegen', 'eft',
    'egraph', 'evalfp', 'exactref', 'fma_compat', 'mixed', 'parser', 'precision',
    'program', 'reductions', 'rules', 'symbolic_cost', 'symbolic_error', 'taylor',
    'to_mlir', 'toolchain',
)


def build_pyz():
    """Собрать однофайловое приложение Python."""
    stage = DIST / '_pyz'
    if stage.exists():
        shutil.rmtree(stage)
    (stage / 'pareto').mkdir(parents=True)

    missing = [m for m in PRODUCT_MODULES if not (ROOT / 'pareto' / (m + '.py')).exists()]
    if missing:
        raise SystemExit('these modules are listed for the release but absent: '
                         + ', '.join(missing))
    for name in PRODUCT_MODULES:
        src = ROOT / 'pareto' / (name + '.py')
        shutil.copy2(src, stage / 'pareto' / src.name)

    # Точка входа архива. Аргументы разбирает сам CLI, поэтому здесь ровно вызов.
    (stage / '__main__.py').write_text(
        'import sys\n'
        'from pareto.cli import main\n'
        'sys.exit(main())\n', encoding='utf-8')

    DIST.mkdir(exist_ok=True)
    target = DIST / 'pareto-fp.pyz'
    if target.exists():
        target.unlink()
    zipapp.create_archive(stage, target, interpreter='/usr/bin/env python3')
    shutil.rmtree(stage)
    return target


def build_binary():
    """Собрать самостоятельный бинарник под ТЕКУЩУЮ систему."""
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit('PyInstaller is not installed: python -m pip install pyinstaller')

    entry = DIST / '_entry.py'
    DIST.mkdir(exist_ok=True)
    entry.write_text('import sys\n'
                     'from pareto.cli import main\n'
                     'sys.exit(main())\n', encoding='utf-8')
    name = 'pareto-fp'
    cmd = [sys.executable, '-m', 'PyInstaller', '--onefile', '--noconfirm',
           '--name', name, '--distpath', str(DIST),
           '--workpath', str(DIST / '_work'), '--specpath', str(DIST / '_spec'),
           '--paths', str(ROOT), str(entry)]
    subprocess.run(cmd, check=True, cwd=str(ROOT))
    entry.unlink(missing_ok=True)
    suffix = '.exe' if os.name == 'nt' else ''
    return DIST / (name + suffix)


def smoke(path):
    """Проверить, что собранный артефакт действительно считает, а не только стартует.

    Проверка идёт по СМЫСЛУ, а не по коду возврата: разность квадратов на близких
    аргументах обязана переписаться, иначе собрался не тот код.
    """
    if path.suffix == '.pyz':
        cmd = [sys.executable, str(path)]
    else:
        cmd = [str(path)]
    cmd += ['x*x - y*y', '--domain', 'x=1000..1000.001', '--domain', 'y=999.999..1000']
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    if 'Pareto front' not in out:
        raise SystemExit('the built artifact does not print a front:\n' + out)
    if 'proven error bound' not in out:
        raise SystemExit('the built artifact does not print a bound:\n' + out)
    return out.splitlines()[0]


def main(argv=None):
    ap = argparse.ArgumentParser(description='build the ready-to-run artifacts')
    ap.add_argument('--binary', action='store_true',
                    help='also build a standalone binary for the current system')
    a = ap.parse_args(argv)

    pyz = build_pyz()
    print('built {} ({:.0f} KB)'.format(pyz, pyz.stat().st_size / 1024))
    print('  smoke: ' + smoke(pyz))

    if a.binary:
        exe = build_binary()
        print('built {} ({:.1f} MB)'.format(exe, exe.stat().st_size / 1024 / 1024))
        print('  smoke: ' + smoke(exe))
    return 0


if __name__ == '__main__':
    sys.exit(main())
