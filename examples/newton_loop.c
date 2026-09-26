/*
 * A loop with a known trip count. Unrolling is the only way this method can prove
 * anything about a loop, so the trip count has to be a constant; a loop bounded by
 * a variable is refused out loud rather than guessed at.
 *
 * After unrolling the body is straight-line code and everything else applies. The
 * rewritten output is printed with temporaries, not as one inlined expression: the
 * cost model counts a shared subexpression once, so the printed code has to share
 * it too.
 *
 * The declared range is narrower than you might expect, and that is the honest
 * limit of the method rather than an oversight. Three Newton steps divide three
 * times, and on t in [0.25, 4] the interval for the third denominator already
 * covers zero — the analysis then says "infinity", which means "could not prove
 * it", not "the error is huge". Narrow the range and the bound appears.
 *
 * Worth saying out loud: on this function the search finds nothing better, and
 * prints so. Newton's form is already the accurate one, and a tool that always
 * reports an improvement is a tool that is not measuring anything.
 *
 *   pareto-fp --file examples/newton_loop.c --function sqrt_newton
 */

// @domain t: 0.5 .. 2.0
double sqrt_newton(double t) {
    double x = 1.0;
    for (int i = 0; i < 3; i++) {
        x = x - (x * x - t) / (2.0 * x);
    }
    return x;
}
