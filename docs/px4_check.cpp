// Проверка трёх находок в полётной математике PX4. Запускается в одну команду.
//
//   git clone --depth 1 https://github.com/PX4/PX4-Matrix.git
//   clang++ -std=c++14 -O2 -I PX4-Matrix docs/px4_check.cpp -o px4_check
//   ./px4_check
//
// Ничего нашего внутри нет: только PX4, стандартная libm и printf. Ответы
// печатаются рядом с правильными, чтобы сверять глазами, а не на слово.

#include <stdio.h>
#include <math.h>
#include <stdlib.h>
#include "matrix/math.hpp"

using matrix::Vector3f;
using matrix::Quatf;
using matrix::Eulerf;
using matrix::Dcmf;

static int confirmed = 0;

static void line(const char *what, double got, const char *right)
{
    printf("  %-40s = %-16g  правильно %s\n", what, got, right);
}

// Отдельная печать с девятью значащими цифрами. В находке про тангаж вся суть
// в том, что аргумент арксинуса равен 1.00000012, а не 1 — при обычной точности
// вывода это различие исчезает, и самое важное число выглядит безобидно.
static void line9(const char *what, double got, const char *right)
{
    printf("  %-40s = %-16.9g  правильно %s\n", what, got, right);
}

int main(void)
{
    printf("PX4-Matrix, одинарная точность, сборка clang++ -O2\n\n");

    // ---- 1. Длина вектора -------------------------------------------------
    printf("1. Vector3f::norm() — длина вектора\n");
    Vector3f small(1e-25f, 1e-25f, 1e-25f);
    Vector3f big(1e20f, 1e20f, 1e20f);
    line("norm() при компонентах 1e-25", (double)small.norm(), "1.732e-25");
    line("norm() при компонентах 1e20", (double)big.norm(), "1.732e+20");
    line("через hypotf, то же самое",
         (double)hypotf(hypotf(1e-25f, 1e-25f), 1e-25f), "1.732e-25");
    line("через hypotf, то же самое",
         (double)hypotf(hypotf(1e20f, 1e20f), 1e20f), "1.732e+20");
    if (small.norm() != 0.0f || isfinite(big.norm())) {
        printf("  (на этой сборке ведёт себя иначе — сообщите)\n");
    } else {
        ++confirmed;
    }
    printf("\n");

    // ---- 2. Нормировка ----------------------------------------------------
    printf("2. Vector3f::normalized() — единичный вектор\n");
    Vector3f u = small.normalized();
    printf("  normalized() при компонентах 1e-25        = (%g, %g, %g)"
           "  правильно (0.577, 0.577, 0.577)\n",
           (double)u(0), (double)u(1), (double)u(2));
    if (!isfinite(u(0))) ++confirmed;
    printf("  вектор НЕ нулевой, правильный ответ в формат укладывается\n\n");

    // ---- 3. Угол тангажа --------------------------------------------------
    printf("3. Eulerf(Quatf) — угол тангажа\n");
    Quatf q(0.691335618f, 0.148509696f, 0.691335559f, -0.148509696f);
    Dcmf d(q);
    Eulerf e(q);
    printf("  кватернион (0.691335618, 0.148509696, 0.691335559, -0.148509696)\n");
    line("его норма", (double)q.norm(), "1 (нормирован)");
    line9("-dcm(2,0), аргумент арксинуса", (double)(-d(2, 0)),
          "не больше 1");
    printf("  %-44s = %-16g  правильно около -0.785\n",
           "тангаж", (double)e.theta());
    if (isnan((float)e.theta())) ++confirmed;
    printf("  причина: asin(-dcm(2,0)) без обрезки аргумента,"
           " а округление вытолкнуло его за 1\n\n");

    // ---- частота третьего случая -----------------------------------------
    printf("Насколько часто третий случай. Перебор кватернионов около тангажа\n"
           "90 градусов, каждый нормирован в одинарной точности:\n");
    srand(7);
    int nan_count = 0, tried = 0;
    for (int i = 0; i < 2000000; ++i) {
        float pitch = (float)(M_PI / 2) - (float)(rand() % 1000) * 1e-9f;
        float roll = (float)((rand() / (double)RAND_MAX) * 2 * M_PI - M_PI);
        float yaw = (float)((rand() / (double)RAND_MAX) * 2 * M_PI - M_PI);
        Quatf qq(Eulerf(roll, pitch, yaw));
        qq.normalize();
        Eulerf ee(qq);
        ++tried;
        if (isnan((float)ee.theta())) ++nan_count;
    }
    printf("  проверено %d, тангаж получился NaN в %d случаях (%.1f%%)\n\n",
           tried, nan_count, 100.0 * nan_count / tried);

    printf("Оговорка: выборка нарочно сделана около 90 градусов — это режим\n"
           "перехода VTOL и подвеса, смотрящего вниз. При обычном тангаже\n"
           "этого не происходит.\n");
    printf("\nподтверждено пунктов: %d из 3\n", confirmed);
    return 0;
}
