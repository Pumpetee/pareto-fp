# Прогон pareto-fp по проекту `raylib`

Диапазон для каждого аргумента: `[-1000, 1000]`. Все числа ниже посчитаны этим запуском.

Повторить:

```
python tools/scan_repo.py C:\Users\user\_scan\raylib
python tools/case_report.py C:\Users\user\_scan\raylib --range -1e3..1e3
python tools/difftest_c.py C:\Users\user\_scan\raylib   # сверка с clang побитово
```

## Охват

| | функций | доля |
|---|---:|---:|
| всего найдено | 340 | |
| принято фронтендом | 157 | 46.2% |
| **из них с вычислениями** | **113** | **33.2%** |
| чтение поля без вычислений | 44 | 12.9% |

Вторая строка — та, которую обычно показывают. Третья — честная: функция вида `return body->m` проходит фронтенд, но доказывать в ней нечего.

Главное число — доля от КАНДИДАТОВ, то есть от функций, где по тексту исходника есть вещественная арифметика. Остальные в знаменатель ставить нельзя: в `void`-процедуре или целочисленном счётчике доказывать нечего, и держать их там значило бы назначить себе цель, которой достичь невозможно.

**Принято из кандидатов: 88 из 179 — 49.2%**

## Границы

Предел времени на функцию: 5 с. Из-за него на части функций поиск оборван, и граница ниже могла быть туже: Pareto extraction, compensated schemes, domain branching, per-box refinement of the branches, recheck of the extracted forms, saturation rounds, series and compensated candidates, series expansion of subexpressions, sqrt-square candidates. Снимается ключом `--budget 0`.

Граница доказана: **76**, не доказана: **37**.

Где перепись даёт больше всего:

| функция | как написано | после переписи | туже в |
|---|---:|---:|---:|
| `Vector4Distance` | 1.651e+00 | 1.221e-04 | 13528× |
| `Vector3Distance` | 1.363e+00 | 1.221e-04 | 11166× |
| `Vector4Length` | 6.615e-01 | 6.104e-05 | 10838× |
| `QuaternionLength` | 6.615e-01 | 6.104e-05 | 10838× |
| `Vector3Length` | 5.304e-01 | 6.104e-05 | 8690× |
| `Vector2Distance` | 9.942e-01 | 1.221e-04 | 8145× |
| `Vector2Length` | 3.187e-01 | 6.104e-05 | 5222× |
| `QuaternionFromEuler.x` | 9.227e-05 | 1.192e-07 | 774× |
| `GetSplinePointCatmullRom.x` | 1.381e+06 | 1.311e+05 | 11× |
| `GetSplinePointBezierCubic.x` | 2.167e+06 | 2.621e+05 | 8× |
| `MatrixDeterminant` | 7.474e+06 | 1.049e+06 | 7× |
| `Vector3CubicHermite.x` | 1.648e+06 | 2.621e+05 | 6× |

## Может вернуть не число

На этих диапазонах код отдаёт NaN или бесконечность. Это не про точность, а про то, вернётся ли вообще число.

- `GetMusicTimeLength`: `/` от `music.stream.sampleRate` на `[-1.00e+03, 1.00e+03]` — the divisor range covers zero, so the code returns inf or NaN
- `Normalize`: `/` от `(float)((end - start))` на `[-2.00e+03, 2.00e+03]` — the divisor range covers zero, so the code returns inf or NaN
- `Remap`: `/` от `(float)((inputEnd - inputStart))` на `[-2.00e+03, 2.00e+03]` — the divisor range covers zero, so the code returns inf or NaN
- `Vector2Divide`: `/` от `v2.y` на `[-1.00e+03, 1.00e+03]` — the divisor range covers zero, so the code returns inf or NaN
- `Vector2Invert`: `/` от `v.y` на `[-1.00e+03, 1.00e+03]` — the divisor range covers zero, so the code returns inf or NaN
- `Vector3Divide`: `/` от `v2.z` на `[-1.00e+03, 1.00e+03]` — the divisor range covers zero, so the code returns inf or NaN
- `Vector3Project`: `/` от `(float)(((float)(((float)((v2.x * v2.x)) + (float)((v2.y * v2.y)))) + ` на `[-3.00e+06, 3.00e+06]` — the divisor range covers zero, so the code returns inf or NaN
- `Vector3Reject`: `/` от `(float)(((float)(((float)((v2.x * v2.x)) + (float)((v2.y * v2.y)))) + ` на `[-3.00e+06, 3.00e+06]` — the divisor range covers zero, so the code returns inf or NaN
- `Vector3Barycenter`: `/` от `(float)(((float)(((float)(((float)(((float)(((float)((b.x - a.x)) * (f` на `[-2.88e+14, 2.88e+14]` — the divisor range covers zero, so the code returns inf or NaN
- `Vector3Unproject`: `/` от `(float)(((float)(((float)(((float)(((float)(((float)(((float)(((float)` на `[-6.14e+27, 6.14e+27]` — the divisor range covers zero, so the code returns inf or NaN
- `Vector3Unproject`: `/` от `(float)(((float)(((float)(((float)(((float)(((float)(((float)(((float)` на `[-inf, inf]` — the divisor range covers zero, so the code returns inf or NaN
- `Vector3Invert`: `/` от `v.z` на `[-1.00e+03, 1.00e+03]` — the divisor range covers zero, so the code returns inf or NaN
- `Vector4Divide`: `/` от `v2.w` на `[-1.00e+03, 1.00e+03]` — the divisor range covers zero, so the code returns inf or NaN
- `Vector4Invert`: `/` от `v.w` на `[-1.00e+03, 1.00e+03]` — the divisor range covers zero, so the code returns inf or NaN
- `MatrixInvert`: `/` от `(float)(((float)(((float)(((float)(((float)(((float)(((float)(((float)` на `[-2.40e+13, 2.40e+13]` — the divisor range covers zero, so the code returns inf or NaN
- `QuaternionDivide`: `/` от `q2.w` на `[-1.00e+03, 1.00e+03]` — the divisor range covers zero, so the code returns inf or NaN
- `rlMatrixInvert`: `/` от `(float)(((float)(((float)(((float)(((float)(((float)(((float)(((float)` на `[-2.40e+13, 2.40e+13]` — the divisor range covers zero, so the code returns inf or NaN

## На чём фронтенд отказывает

| причина | функций |
|---|---:|
| it is neither an argument nor a local variable of  | 45 |
| that is an array or an output parameter, and neith | 33 |
| character '#' is not part of the supported subset  | 8 |
| the bound of the loop counter must be a literal co | 8 |
| statement starting at 'par_shapes_mesh' is not sup | 5 |
| a cast to an integer type discards the fractional  | 5 |
| array declarations are not supported; a reduction  | 4 |
| cannot read an expression starting at '{' | 4 |
| this method proves bounds on straight-line code, o | 3 |
| assignment to unknown name 'mat' | 3 |

