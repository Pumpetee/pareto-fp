/*
 * Mixed precision, read out of the declarations rather than guessed. In C
 * `float a, b; a*b` is computed in binary32 and `a*2.0` in binary64, because the
 * literal is a double; assignment into a float variable rounds, and so does the
 * return of a float function. All of that changes the proven bound, so the tool
 * follows the language rules instead of pretending everything is double.
 *
 * Two questions this answers that the expression mode cannot:
 *
 *   pareto-fp --file examples/mixed_precision.c --function energy
 *       what does this actually cost me, written as it is?
 *
 *   pareto-fp "0.5*m*v*v + m*9.81*h" --domain m=1..2 --domain v=0..30 \
 *             --domain h=0..100 --target 1e-3
 *       which parts can I drop to float and still stay inside my error target?
 */

// @domain m: 1.0 .. 2.0
// @domain v: 0.0 .. 30.0
// @domain h: 0.0 .. 100.0
float energy(float m, float v, float h) {
    float kinetic = 0.5f * m * v * v;
    float potential = m * 9.81f * h;
    return kinetic + potential;
}
