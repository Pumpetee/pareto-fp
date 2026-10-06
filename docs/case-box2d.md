# Прогон pareto-fp по проекту `box2d`

Диапазон для каждого аргумента: `[-1000, 1000]`. Все числа ниже посчитаны этим запуском.

Повторить:

```
python tools/scan_repo.py C:\Users\user\_scan\box2d
python tools/case_report.py C:\Users\user\_scan\box2d --range -1e3..1e3
python tools/difftest_c.py C:\Users\user\_scan\box2d   # сверка с clang побитово
```

## Охват

| | функций | доля |
|---|---:|---:|
| всего найдено | 150 | |
| принято фронтендом | 91 | 60.7% |
| **из них с вычислениями** | **26** | **17.3%** |
| чтение поля без вычислений | 65 | 43.3% |

Вторая строка — та, которую обычно показывают. Третья — честная: функция вида `return body->m` проходит фронтенд, но доказывать в ней нечего.

## Границы

Граница доказана: **24**, не доказана: **2**.

Где перепись даёт больше всего:

| функция | как написано | после переписи | туже в |
|---|---:|---:|---:|
| `b2Distance` | 9.942e-01 | 1.221e-04 | 8145× |
| `b2Length` | 3.187e-01 | 6.104e-05 | 5222× |
| `b2PlaneSeparation` | 1.875e-01 | 6.250e-02 | 3× |
| `b2Perimeter` | 7.324e-04 | 2.441e-04 | 3× |
| `b2ComputeAngularVelocity` | 1.890e+02 | 6.400e+01 | 3× |
| `b2Dot3` | 2.813e-01 | 1.250e-01 | 2× |
| `b2RelativeCos` | 1.250e-01 | 6.250e-02 | 2× |
| `b2PrismaticJoint_GetTranslation` | 1.250e-01 | 6.250e-02 | 2× |
| `b2Dot` | 1.250e-01 | 6.250e-02 | 2× |
| `b2Cross` | 1.250e-01 | 6.250e-02 | 2× |
| `b2LengthSquared` | 1.250e-01 | 6.250e-02 | 2× |
| `b2DistanceSquared` | 1.250e-01 | 6.250e-02 | 2× |

## Может вернуть не число

На этих диапазонах код отдаёт NaN или бесконечность. Это не про точность, а про то, вернётся ли вообще число.

- `b2MixFriction`: `sqrt` от `(float)((frictionA * frictionB))` на `[-1.00e+06, 1.00e+06]` — the argument can be negative, so the code returns NaN
- `b2SpringDamper`: `/` от `(float)(((float)((1.0 + (float)(((float)((2.0 * dampingRatio)) * (floa` на `[-3.95e+13, 3.95e+13]` — the divisor range covers zero, so the code returns inf or NaN

## На чём фронтенд отказывает

| причина | функций |
|---|---:|
| it is neither an argument nor a local variable of  | 14 |
| that is an array or an output parameter, and neith | 7 |
| this method proves bounds on straight-line code, o | 7 |
| statement starting at 'LARGE_INTEGER' is not suppo | 4 |
| TypeError | 3 |
| declaration of 'const' inside the body is not supp | 3 |
| statement starting at 'B2_UNUSED' is not supported | 3 |
| call to RandomInt with 0 argument(s) is not suppor | 2 |
| call to b2Atan2 with 2 argument(s) is not supporte | 2 |
| expected ';', found '?' | 1 |

