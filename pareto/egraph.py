# -*- coding: utf-8 -*-
"""E-граф с насыщением равенствами (equality saturation).

Минимальная, но настоящая реализация: hashcons + union-find + насыщение
набором правил переписывания. Узел — кортеж (op, eclass_id, ...).
Листья: ('var', имя) и ('num', значение).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product


# ---------- выражения (дерево) ----------
# ('+', a, b) | ('-', a, b) | ('*', a, b) | ('/', a, b)
# ('neg', a) | ('sqrt', a) | ('exp', a) | ('log', a)
# ('var', 'x') | ('num', 2.0)

# approx — приближение подвыражения рядом, несущее собственную погрешность метода.
# Для e-графа это непрозрачный лист: внутрь правила не лезут (там уже не тождество),
# зато алгебра работает ВОКРУГ него — и ровно это сокращает b - b в формуле корней
# квадратного уравнения после подстановки ряда.
LEAVES = ('var', 'num', 'approx', 'eft')


def is_leaf(e):
    return isinstance(e, tuple) and e[0] in LEAVES


class UnionFind:
    def __init__(self):
        self.parent = []

    def make(self):
        self.parent.append(len(self.parent))
        return len(self.parent) - 1

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a == b:
            return a
        # меньший id становится каноническим — стабильность между прогонами
        if b < a:
            a, b = b, a
        self.parent[b] = a
        return a


@dataclass
class EGraph:
    uf: UnionFind = field(default_factory=UnionFind)
    hashcons: dict = field(default_factory=dict)      # node -> eclass
    classes: dict = field(default_factory=dict)       # eclass -> set(node)
    _dirty: list = field(default_factory=list)
    # Домен нужен правилам с предусловием: сократить общий множитель в дроби
    # можно только там, где он заведомо не обращается в ноль, а это видно
    # исключительно по интервалу. Пусто — такие правила просто не срабатывают.
    domain: dict = field(default_factory=dict)
    ivs: dict = field(default_factory=dict)

    # ---------- построение ----------
    def canon(self, node):
        op = node[0]
        if op in LEAVES:
            return node
        return (op,) + tuple(self.uf.find(a) for a in node[1:])

    def add_node(self, node):
        node = self.canon(node)
        if node in self.hashcons:
            return self.uf.find(self.hashcons[node])
        eid = self.uf.make()
        self.hashcons[node] = eid
        self.classes.setdefault(eid, set()).add(node)
        return eid

    def add_expr(self, e):
        if is_leaf(e):
            return self.add_node(e)
        kids = tuple(self.add_expr(k) for k in e[1:])
        return self.add_node((e[0],) + kids)

    def merge(self, a, b):
        a, b = self.uf.find(a), self.uf.find(b)
        if a == b:
            return a
        root = self.uf.union(a, b)
        other = b if root == a else a
        self.classes.setdefault(root, set()).update(self.classes.pop(other, set()))
        self._dirty.append(root)
        return root

    def rebuild(self):
        """Перестроить hashcons после слияний, пока не стабилизируется."""
        while self._dirty:
            self._dirty = []
            new_hash = {}
            new_classes = {}
            for eid, nodes in list(self.classes.items()):
                root = self.uf.find(eid)
                for n in nodes:
                    cn = self.canon(n)
                    if cn in new_hash and new_hash[cn] != root:
                        root = self.merge(new_hash[cn], root)
                    new_hash[cn] = root
                    new_classes.setdefault(root, set()).add(cn)
            self.hashcons = new_hash
            self.classes = {}
            for eid, nodes in new_classes.items():
                r = self.uf.find(eid)
                self.classes.setdefault(r, set()).update(self.canon(n) for n in nodes)

    # ---------- сопоставление с образцом ----------
    # образец: ('+', '?a', '?b') — строки с '?' это переменные образца
    def match(self, pattern):
        """Вернуть список (eclass, подстановка) для всех совпадений."""
        out = []
        for eid in list(self.classes):
            if self.uf.find(eid) != eid:
                continue
            for subst in self._match_class(pattern, eid, {}):
                out.append((eid, subst))
        return out

    def _match_class(self, pat, eid, subst):
        eid = self.uf.find(eid)
        if isinstance(pat, str) and pat.startswith('?'):
            if pat in subst:
                if subst[pat] == eid:
                    yield subst
                return
            s = dict(subst)
            s[pat] = eid
            yield s
            return
        for node in list(self.classes.get(eid, ())):
            if node[0] != pat[0] or len(node) != len(pat):
                continue
            if node[0] in LEAVES:
                if node[1] == pat[1]:
                    yield subst
                continue
            partial = [subst]
            ok = True
            for sub_pat, kid in zip(pat[1:], node[1:]):
                nxt = []
                for s in partial:
                    nxt.extend(self._match_class(sub_pat, kid, s))
                if not nxt:
                    ok = False
                    break
                partial = nxt
            if ok:
                for s in partial:
                    yield s

    def instantiate(self, pattern, subst):
        if isinstance(pattern, str) and pattern.startswith('?'):
            return subst[pattern]
        if pattern[0] in LEAVES:
            return self.add_node(pattern)
        kids = tuple(self.instantiate(p, subst) for p in pattern[1:])
        return self.add_node((pattern[0],) + kids)

    # ---------- насыщение ----------
    def fold_constants(self):
        """Считает узлы, у которых все аргументы — числа, и сливает их с результатом.

        Без этого шага переписывание выдавало формы вроде `(2 + 2) * (b * a)`: сложение
        двойки с двойкой выполнялось в рантайме на каждом вызове. Herbie на том же кейсе
        отдавал `a * (4 * b)` и выигрывал у нас по стоимости на ровном месте — не лучшим
        поиском, а тем, что не тащил в код арифметику, известную на этапе компиляции.
        """
        import math as _m

        def value(eid):
            for n in self.classes.get(self.uf.find(eid), ()):
                if n[0] == 'num':
                    return n[1]
            return None

        merged = 0
        for eid in list(self.classes):
            for n in list(self.classes.get(self.uf.find(eid), ())):
                op = n[0]
                if op in LEAVES:      # approx тоже лист: внутрь не лезем
                    continue
                vals = [value(k) for k in n[1:]]
                if any(v is None for v in vals):
                    continue
                try:
                    if op == '+':
                        r = vals[0] + vals[1]
                    elif op == '-':
                        r = vals[0] - vals[1]
                    elif op == '*':
                        r = vals[0] * vals[1]
                    elif op == '/':
                        r = vals[0] / vals[1]
                    elif op == 'neg':
                        r = -vals[0]
                    elif op == 'sqrt':
                        r = _m.sqrt(vals[0])
                    elif op == 'exp':
                        r = _m.exp(vals[0])
                    elif op == 'log':
                        r = _m.log(vals[0])
                    elif op == 'expm1':
                        r = _m.expm1(vals[0])
                    elif op == 'log1p':
                        r = _m.log1p(vals[0])
                    elif op == 'hypot':
                        r = _m.hypot(vals[0], vals[1])
                    elif op == 'fma':
                        r = vals[0] * vals[1] + vals[2]
                    else:
                        continue
                except (ValueError, ZeroDivisionError, OverflowError):
                    continue
                if not _m.isfinite(r):
                    continue
                self.merge(self.uf.find(eid), self.add_node(('num', float(r))))
                merged += 1
        if merged:
            self.rebuild()
        return merged

    def refresh_intervals(self, rounds=3):
        """Пересчитать интервалы классов — опора для правил с предусловием.

        Дорого: проход по всем классам несколько раз. На графе в шесть тысяч узлов
        пересчёт каждый круг насыщения превращает секундный прогон в бесконечный,
        поэтому зовётся редко — см. saturate.
        """
        if not self.domain:
            self.ivs = {}
            return self.ivs
        from pareto.analysis import class_intervals
        self.ivs = class_intervals(self, self.domain, rounds=rounds)
        return self.ivs

    def nonzero(self, eid):
        """Точно ли класс не обращается в ноль на домене.

        Осторожность здесь не формальность: правило сокращения без такой проверки
        уже ломало корректность — на sqrt(x)−sqrt(x) деление 0/0 схлопывалось в
        единицу, и фронт выдавал форму, не равную исходному выражению.
        """
        iv = self.ivs.get(self.uf.find(eid))
        if iv is None:
            return False
        lo, hi = iv
        return lo > 0.0 or hi < 0.0

    def saturate(self, rules, iters=8, node_limit=60000, domain=None):
        """rules: список (lhs, rhs, двусторонее?) или (lhs, функция).

        domain включает правила с предусловием: без него они молчат, потому что
        проверить ненулевость делителя нечем.
        """
        if domain:
            self.domain = domain
        self.fold_constants()
        self.refresh_intervals()
        for _round in range(iters):
            # интервалы освежаем не каждый круг: они нужны лишь условным правилам,
            # а их проверка терпит небольшое отставание — лишь бы не была неверной,
            # а устаревший интервал делает правило строже, а не слабее
            if _round and _round % 3 == 0:
                self.refresh_intervals()
            matches = []
            for rule in rules:
                lhs, rhs = rule[0], rule[1]
                for eid, subst in self.match(lhs):
                    matches.append((eid, rhs, subst))
                if len(rule) > 2 and rule[2]:  # двустороннее правило
                    for eid, subst in self.match(rhs):
                        matches.append((eid, lhs, subst))
            before_nodes = len(self.hashcons)
            before_classes = len({self.uf.find(e) for e in self.classes})
            for eid, rhs, subst in matches:
                # лимит проверяем здесь, а не только в конце круга: один круг на
                # выражении с корнями легко добавляет больше ста тысяч узлов, и
                # проверка постфактум опаздывает на минуты машинного времени
                if len(self.hashcons) > node_limit:
                    break
                try:
                    # callable получает и eid: правилу с предусловием часто нужен
                    # контекст, а не только подстановка
                    new_id = rhs(self, subst, eid) if callable(rhs) else self.instantiate(rhs, subst)
                except KeyError:
                    continue
                if new_id is None:
                    continue
                self.merge(eid, new_id)
            self.rebuild()
            self.fold_constants()      # правила рождают новые константные узлы каждый круг
            self.refresh_intervals()   # и новые классы, чьи интервалы нужны условным правилам
            # рост считаем по факту: появились узлы или схлопнулись классы
            after_classes = len({self.uf.find(e) for e in self.classes})
            grew = (len(self.hashcons) != before_nodes) or (after_classes != before_classes)
            if len(self.hashcons) > node_limit:
                break
            if not grew:
                break
        return self

    def size(self):
        return len(self.hashcons), len({self.uf.find(e) for e in self.classes})
