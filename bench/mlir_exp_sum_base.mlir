module {
  func.func @exp_sum_base(%arg0: f64, %arg1: f64) -> f64 {
    %v1 = math.exp %arg0 : f64
    %v2 = math.exp %arg1 : f64
    %v3 = arith.mulf %v1, %v2 : f64
    return %v3 : f64
  }
}
