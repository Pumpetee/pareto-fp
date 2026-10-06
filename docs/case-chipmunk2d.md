# Прогон pareto-fp по проекту `Chipmunk2D`

Диапазон для каждого аргумента: `[-1000, 1000]`. Все числа ниже посчитаны этим запуском.

Повторить:

```
python tools/scan_repo.py C:\Users\user\_scan\Chipmunk2D
python tools/case_report.py C:\Users\user\_scan\Chipmunk2D --range -1e3..1e3
python tools/difftest_c.py C:\Users\user\_scan\Chipmunk2D   # сверка с clang побитово
```

## Охват

| | функций | доля |
|---|---:|---:|
| всего найдено | 99 | |
| принято фронтендом | 36 | 36.4% |
| **из них с вычислениями** | **16** | **16.2%** |
| чтение поля без вычислений | 20 | 20.2% |

Вторая строка — та, которую обычно показывают. Третья — честная: функция вида `return body->m` проходит фронтенд, но доказывать в ней нечего.

## Границы

Граница доказана: **14**, не доказана: **2**.

Где перепись даёт больше всего:

| функция | как написано | после переписи | туже в |
|---|---:|---:|---:|
| `getImpulse` | 1.526e-05 | 2.274e-13 | 67108864× |
| `cpvlength` | 1.375e-05 | 2.274e-13 | 60491113× |
| `cpflerp` | 2.897e-10 | 1.746e-10 | 2× |
| `cpMomentForCircle` | 8.205e-07 | 5.600e-07 | 1× |
| `cpvdot` | 2.328e-10 | 1.746e-10 | 1× |
| `cpvcross` | 2.328e-10 | 1.746e-10 | 1× |
| `cpvlengthsq` | 2.328e-10 | 1.746e-10 | 1× |
| `cpBBArea` | 6.876e-10 | 5.211e-10 | 1× |
| `defaultSpringTorque` | 2.301e-10 | 1.746e-10 | 1× |
| `defaultSpringForce` | 2.301e-10 | 1.746e-10 | 1× |
| `cpMomentForBox` | 4.424e-08 | 3.939e-08 | 1× |

## Может вернуть не число

На этих диапазонах код отдаёт NaN или бесконечность. Это не про точность, а про то, вернётся ли вообще число.

- `midlerp`: `/` от `(s1 - s0)` на `[-2.00e+03, 2.00e+03]` — the divisor range covers zero, so the code returns inf or NaN

## На чём фронтенд отказывает

| причина | функций |
|---|---:|
| it is neither an argument nor a local variable of  | 36 |
| statement starting at 'cpFloat' is not supported | 9 |
| TypeError | 9 |
| statement starting at 'body' is not supported | 1 |
| call to cpvadd with 2 argument(s) is not supported | 1 |
| expected ';', found '(' | 1 |
| call to cpvsub with 2 argument(s) is not supported | 1 |
| parameter 'struct Notch notch' of FindSteiner is n | 1 |
| function SegmentQuery takes a pointer the shape of | 1 |
| function SegmentQueryFirst takes a pointer the sha | 1 |

