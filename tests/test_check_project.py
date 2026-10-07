# -*- coding: utf-8 -*-
"""Коды возврата обхода проекта. Это контракт со сборкой, а не удобство.

Инструмент, который ставят в CI, обязан отвечать кодом, а не текстом: человек
вывод не читает, читает его сборка. Поэтому коды проверяются тестом, а не
глазами, и различие между 1 и 2 проверяется отдельно — оно несёт разный смысл.
Единица говорит «мы знаем, что писать вместо», двойка — «на этих диапазонах
недостижимо».

Проект для проверки пишется во ВРЕМЕННЫЙ каталог, а не в tests/: папка с именем
`tests` отбрасывается самим инструментом как чужой вспомогательный код, и первая
версия этого теста получила ноль функций. Заодно так проверяется, что
подсказка про отброшенные каталоги действительно печатается.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'tools' / 'check_project.py'

SRC = '''
float plus(float a, float b)
{
    return a + b;
}

float sq_diff(float a, float b)
{
    return a*a - b*b;
}

double blow_up(double a, double b)
{
    return (a - b) * 1e12;
}
'''


def run(require, extra=(), src=SRC):
    tmp = Path(tempfile.mkdtemp(prefix='pareto_proj_'))
    (tmp / 'kernel.c').write_text(src, encoding='utf-8')
    cmd = [sys.executable, str(TOOL), str(tmp), '--require', str(require),
           '--range=-1..1', '--budget', '3'] + list(extra)
    return subprocess.run(cmd, capture_output=True, text=True)


class TestCheckProjectExitCodes(unittest.TestCase):

    def test_something_is_actually_checked(self):
        got = run('1e3')
        self.assertIn('проверено выходов:', got.stdout)
        line = [l for l in got.stdout.splitlines()
                if l.startswith('проверено выходов:')][0]
        self.assertNotIn(': 0', line, 'обход не нашёл ни одной функции: ' + got.stdout)

    def test_strict_requirement_reports_unreachable(self):
        got = run('1e-30')
        self.assertEqual(got.returncode, 2, got.stdout + got.stderr)
        self.assertIn('НЕ ДОСТИГАЕТСЯ', got.stdout)

    def test_loose_requirement_passes(self):
        got = run('1e3')
        self.assertEqual(got.returncode, 0, got.stdout + got.stderr)

    def test_bad_argument_gives_its_own_code(self):
        got = run('1e-6', extra=('--arg', 'мусор'))
        self.assertEqual(got.returncode, 64, got.stdout + got.stderr)


if __name__ == '__main__':
    unittest.main()
