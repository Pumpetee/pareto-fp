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
| всего найдено | 212 | |
| принято фронтендом | 63 | 29.7% |
| **из них с вычислениями** | **25** | **11.8%** |
| чтение поля без вычислений | 38 | 17.9% |

Вторая строка — та, которую обычно показывают. Третья — честная: функция вида `return body->m` проходит фронтенд, но доказывать в ней нечего.

Главное число — доля от КАНДИДАТОВ, то есть от функций, где по тексту исходника есть вещественная арифметика. Остальные в знаменатель ставить нельзя: в `void`-процедуре или целочисленном счётчике доказывать нечего, и держать их там значило бы назначить себе цель, которой достичь невозможно.

**Принято из кандидатов: 21 из 69 — 30.4%**

## Границы

Граница доказана: **21**, не доказана: **3**.

Где перепись даёт больше всего:

| функция | как написано | после переписи | туже в |
|---|---:|---:|---:|
| `getImpulse` | 1.526e-05 | 2.274e-13 | 67108864× |
| `cpvlength` | 1.375e-05 | 2.274e-13 | 60491113× |
| `cpflerp` | 2.897e-10 | 1.746e-10 | 2× |
| `cpMomentForCircle` | 8.205e-07 | 5.600e-07 | 1× |
| `k_scalar_body` | 1.670e+00 | 1.187e+00 | 1× |
| `cpvdot` | 2.328e-10 | 1.746e-10 | 1× |
| `cpvcross` | 2.328e-10 | 1.746e-10 | 1× |
| `cpvlengthsq` | 2.328e-10 | 1.746e-10 | 1× |
| `cpBBMergedArea` | 6.876e-10 | 5.211e-10 | 1× |
| `cpBBArea` | 6.876e-10 | 5.211e-10 | 1× |
| `defaultSpringTorque` | 2.301e-10 | 1.746e-10 | 1× |
| `defaultSpringForce` | 2.301e-10 | 1.746e-10 | 1× |

## Может вернуть не число

На этих диапазонах код отдаёт NaN или бесконечность. Это не про точность, а про то, вернётся ли вообще число.

- `midlerp`: `/` от `(s1 - s0)` на `[-2.00e+03, 2.00e+03]` — the divisor range covers zero, so the code returns inf or NaN
- `cpShapeGetDensity`: `/` от `shape->massInfo.area` на `[-1.00e+03, 1.00e+03]` — the divisor range covers zero, so the code returns inf or NaN

## На чём фронтенд отказывает

| причина | функций |
|---|---:|
| it is neither an argument nor a local variable of  | 79 |
| call to cpv with 2 argument(s) is not supported | 15 |
| call to cpTransformNewTranspose with 6 argument(s) | 10 |
| call to cpBBNew with 4 argument(s) is not supporte | 4 |
| the bound of the loop counter must be a literal co | 3 |
| call to cpvdist with 2 argument(s) is not supporte | 2 |
| expected ';', found ',' | 2 |
| call to cpvperp with 1 argument(s) is not supporte | 2 |
| statement starting at 'cpBBTreeVelocityFunc' is no | 1 |
| statement starting at 'body' is not supported | 1 |

