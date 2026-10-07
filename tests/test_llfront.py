# -*- coding: utf-8 -*-
"""Чтение промежуточного представления LLVM.

Это единственный путь к C++: шаблоны, классы и перегрузку операторов
разворачивает компилятор, а мы читаем арифметику. Тест работает с текстом
представления напрямую, без компилятора, чтобы проверка шла везде — в том числе
там, где clang не установлен.
"""
import unittest

from pareto.analysis import tree_cost_refined
from pareto.codegen import to_text
from pareto.llfront import LLError, build, parse_module

NORM3 = '''
define dso_local noundef float @norm3(float noundef %0, float noundef %1, float noundef %2) {
  %4 = fmul float %0, %0
  %5 = fmul float %1, %1
  %6 = fadd float %4, %5
  %7 = fmul float %2, %2
  %8 = fadd float %6, %7
  %9 = tail call noundef float @llvm.sqrt.f32(float %8)
  ret float %9
}
'''

WITH_MEMORY = '''
define float @reads(ptr %p) {
  %1 = load float, ptr %p, align 4
  ret float %1
}
'''

WITH_LOOP = '''
define float @loops(float %0) {
  br label %2

2:
  %3 = phi float [ 0.0, %1 ], [ %4, %2 ]
  %4 = fadd float %3, %0
  ret float %4
}
'''

MIXED = '''
define double @widen(float %0) {
  %2 = fpext float %0 to double
  %3 = fmul double %2, %2
  ret double %3
}
'''


class TestLLFront(unittest.TestCase):

    def test_reads_float32_arithmetic(self):
        mod = parse_module(NORM3)
        self.assertIn('norm3', mod)
        args, body = mod['norm3']
        self.assertEqual(len(args), 3)
        tree = build(args, body)
        text = to_text(tree)
        # Каждая операция одинарной точности обязана нести своё округление:
        # без него модель считала бы в двойной и ЗАНИЗИЛА границу.
        self.assertIn('f32', text)
        self.assertIn('sqrt', text)
        # Три умножения, два сложения, один корень — шесть округлений.
        self.assertEqual(text.count('f32'), 6)

    def test_bound_is_float32_sized(self):
        args, body = parse_module(NORM3)['norm3']
        tree = build(args, body)
        dom = {'a0': (-1e3, 1e3), 'a1': (-1e3, 1e3), 'a2': (-1e3, 1e3)}
        bound = tree_cost_refined(tree, dom)[1]
        # Для одинарной точности на этом домене граница порядка десятых. Если
        # округления потеряются, она упадёт на девять порядков — именно так я
        # однажды и ошибся, сняв округление с вызова как «лишнее».
        self.assertGreater(bound, 1e-3)
        self.assertLess(bound, 1e2)

    def test_memory_is_refused(self):
        args, body = parse_module(WITH_MEMORY)['reads']
        with self.assertRaises(LLError):
            build(args, body)

    def test_loop_is_refused(self):
        args, body = parse_module(WITH_LOOP)['loops']
        with self.assertRaises(LLError):
            build(args, body)

    def test_precision_change_is_kept(self):
        args, body = parse_module(MIXED)['widen']
        tree = build(args, body)
        text = to_text(tree)
        # Расширение точности округления не добавляет, сужение добавляет.
        self.assertNotIn('f32(', text.replace('f32(a0', ''))


if __name__ == '__main__':
    unittest.main()
