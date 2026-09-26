/*
 * Three kernels from the rosa suite of FPBench, written the way they appear in
 * real code: local variables, no hand-inlining. The same three functions are used
 * by FPTaylor, Daisy, Gappa and Salsa in their papers, so the bounds this tool
 * prints for them can be put next to the published ones directly.
 *
 * Ranges live in comments next to the function. They are mandatory: without
 * knowing the inputs there is no error bound to prove.
 *
 *   pareto-fp --file examples/rosa_turbine.c --list
 *   pareto-fp --file examples/rosa_turbine.c --function turbine1
 */

// @domain v: -4.5 .. -0.3
// @domain w: 0.4 .. 0.9
// @domain r: 3.8 .. 7.8
double turbine1(double v, double w, double r) {
    double rr = r * r;
    double wr = w * w * r * r;
    return 3.0 + 2.0 / rr - 0.125 * (3.0 - 2.0 * v) * wr / (1.0 - v) - 4.5;
}

// @domain v: -4.5 .. -0.3
// @domain w: 0.4 .. 0.9
// @domain r: 3.8 .. 7.8
double turbine2(double v, double w, double r) {
    double wr = w * w * r * r;
    return 6.0 * v - 0.5 * v * wr / (1.0 - v) - 2.5;
}

// @domain v: -4.5 .. -0.3
// @domain w: 0.4 .. 0.9
// @domain r: 3.8 .. 7.8
double turbine3(double v, double w, double r) {
    double rr = r * r;
    double wr = w * w * r * r;
    return 3.0 - 2.0 / rr - 0.125 * (1.0 + 2.0 * v) * wr / (1.0 - v) - 0.5;
}
