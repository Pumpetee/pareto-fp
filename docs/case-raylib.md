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
| всего найдено | 58 | |
| принято фронтендом | 30 | 51.7% |
| **из них с вычислениями** | **28** | **48.3%** |
| чтение поля без вычислений | 2 | 3.4% |

Вторая строка — та, которую обычно показывают. Третья — честная: функция вида `return body->m` проходит фронтенд, но доказывать в ней нечего.

## Границы

Граница доказана: **20**, не доказана: **8**.

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
| `MatrixDeterminant` | 7.474e+06 | 1.049e+06 | 7× |
| `Vector4DistanceSqr` | 2.727e+00 | 5.000e-01 | 5× |
| `Vector2DistanceSqr` | 9.883e-01 | 2.500e-01 | 4× |
| `Vector3DistanceSqr` | 1.857e+00 | 5.000e-01 | 4× |
| `Vector4LengthSqr` | 4.375e-01 | 1.250e-01 | 3× |

## Может вернуть не число

На этих диапазонах код отдаёт NaN или бесконечность. Это не про точность, а про то, вернётся ли вообще число.

- `GetMusicTimeLength`: `/` от `music.stream.sampleRate` на `[-1.00e+03, 1.00e+03]` — the divisor range covers zero, so the code returns inf or NaN
- `Normalize`: `/` от `(float)((end - start))` на `[-2.00e+03, 2.00e+03]` — the divisor range covers zero, so the code returns inf or NaN
- `Remap`: `/` от `(float)((inputEnd - inputStart))` на `[-2.00e+03, 2.00e+03]` — the divisor range covers zero, so the code returns inf or NaN

## На чём фронтенд отказывает

| причина | функций |
|---|---:|
| it is neither an argument nor a local variable of  | 7 |
| statement starting at 'clock_gettime' is not suppo | 3 |
| statement starting at 'glGetFloatv' is not support | 2 |
| call to glfwGetTime with 0 argument(s) is not supp | 2 |
| statement starting at 'QueryPerformanceCounter' is | 2 |
| statement starting at 'ma_device_get_master_volume | 1 |
| expected ')', found '<' | 1 |
| call to floorf with 1 argument(s) is not supported | 1 |
| expected ')', found '==' | 1 |
| the variable result is declared without a value. A | 1 |

