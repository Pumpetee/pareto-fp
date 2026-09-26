/*
 * Conditionals, and what they actually cost. Three cases, and each says something
 * different.
 *
 * `safe_diff_sqrt` — the guard is exact (a variable against a constant rounds
 * nothing), so the bound is pure rounding, and each branch is rewritten on its own
 * region: the arithmetic that is best on x > 1 is not the arithmetic that is best
 * below it. A compiler specialises inside a branch for exactly this reason.
 *
 * `expm1_guard` — the answer here is not a rewrite of the branch but the removal of
 * its reason. The tool finds expm1, and with it the large branch becomes as
 * accurate as the series shortcut was, so the guard has nothing left to protect.
 * Note what the tool does NOT claim: it measures each branch against ITS OWN ideal
 * value, so it cannot tell you how far `1 + x/2` is from the true expm1(x)/x. That
 * is a question about your specification, not about your rounding.
 *
 * `jumpy` — the case where the bound is not about rounding at all. The condition is
 * computed with catastrophic cancellation, so near the boundary the program takes
 * the other branch, and the error against the ideal value is the whole JUMP between
 * the branches. No rewriting of the arithmetic can help. The tool splits the bound
 * into the two parts so it is obvious which one dominates.
 *
 *   pareto-fp --file examples/branch_sqrt.c --function safe_diff_sqrt
 *   pareto-fp --file examples/branch_sqrt.c --function expm1_guard
 *   pareto-fp --file examples/branch_sqrt.c --function jumpy
 */

#include <math.h>

/* Guard against cancellation. Both branches compute the same thing, so there is no
   jump at all, and the win is 1e4 times on the branch that had the cancellation. */
// @domain x: 0.0 .. 1000000000.0
double safe_diff_sqrt(double x) {
    if (x > 1.0) {
        return sqrt(x + 1.0) - sqrt(x);
    }
    return sqrt(x + 1.0) - sqrt(x);
}

/* (exp(x) - 1) / x with the classic series shortcut for small x. */
// @domain x: 0.0001 .. 1.0
double expm1_guard(double x) {
    if (x > 0.001) {
        return (exp(x) - 1.0) / x;
    }
    return 1.0 + x / 2.0;
}

/* Discontinuous at the boundary, and the condition is computed with catastrophic
   cancellation: (x + 1e16) - 1e16 is x in real arithmetic and a multiple of 2 in
   binary64. The bound is dominated by the jump, which is the honest answer. */
// @domain x: 0.1 .. 1.9
double jumpy(double x) {
    double c = (x + 1.0e16) - 1.0e16;
    if (c > 0.0) {
        return x * x;
    }
    return x * x + 1.0;
}
