module {
  func.func @exp_sum_opt(%arg0: f64, %arg1: f64) -> f64 {
    %v1 = arith.addf %arg1, %arg0 : f64
    %v2 = math.exp %v1 : f64
    return %v2 : f64
  }
}
