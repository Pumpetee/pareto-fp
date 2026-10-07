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
| всего найдено | 478 | |
| принято фронтендом | 131 | 27.4% |
| **из них с вычислениями** | **56** | **11.7%** |
| чтение поля без вычислений | 75 | 15.7% |

Вторая строка — та, которую обычно показывают. Третья — честная: функция вида `return body->m` проходит фронтенд, но доказывать в ней нечего.

## Границы

Граница доказана: **48**, не доказана: **8**.

Где перепись даёт больше всего:

| функция | как написано | после переписи | туже в |
|---|---:|---:|---:|
| `b2Distance` | 9.942e-01 | 1.221e-04 | 8145× |
| `b2Length` | 3.187e-01 | 6.104e-05 | 5222× |
| `b2DistanceSquared` | 9.883e-01 | 2.500e-01 | 4× |
| `b2PlaneSeparation` | 1.875e-01 | 6.250e-02 | 3× |
| `b2TransformPoint.x` | 1.875e-01 | 6.250e-02 | 3× |
| `b2Perimeter` | 7.324e-04 | 2.441e-04 | 3× |
| `b2InvTransformPoint.x` | 3.721e-01 | 1.250e-01 | 3× |
| `b2ComputeAngularVelocity` | 1.890e+02 | 6.400e+01 | 3× |
| `b2Lerp.x` | 1.555e-01 | 6.250e-02 | 2× |
| `b2Weight3.x` | 2.813e-01 | 1.250e-01 | 2× |
| `b2Dot3` | 2.813e-01 | 1.250e-01 | 2× |
| `b2MulAdd.x` | 6.250e-02 | 3.125e-02 | 2× |

## Может вернуть не число

На этих диапазонах код отдаёт NaN или бесконечность. Это не про точность, а про то, вернётся ли вообще число.

- `b2MixFriction`: `sqrt` от `(float)((frictionA * frictionB))` на `[-1.00e+06, 1.00e+06]` — the argument can be negative, so the code returns NaN
- `b2SpringDamper`: `/` от `(float)(((float)((1.0 + (float)(((float)((2.0 * dampingRatio)) * (floa` на `[-3.95e+13, 3.95e+13]` — the divisor range covers zero, so the code returns inf or NaN

## На чём фронтенд отказывает

| причина | функций |
|---|---:|
| it is neither an argument nor a local variable of  | 116 |
| type b2Pos is declared more than once with differe | 25 |
| type b2WorldTransform is declared more than once w | 17 |
| cannot read an expression starting at '&' | 16 |
| statement starting at 'def' is not supported | 16 |
| this method proves bounds on straight-line code, o | 12 |
| that is an array or an output parameter, and neith | 9 |
| statement starting at 'b2RecR_JointBase' is not su | 9 |
| declaration of 'const' inside the body is not supp | 8 |
| cannot read an expression starting at '.' | 5 |

