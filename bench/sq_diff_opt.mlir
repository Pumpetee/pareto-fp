module {
  func.func @sq_diff_opt(%arg0: f64, %arg1: f64) -> f64 {
    %v1 = arith.addf %arg0, %arg1 : f64
    %v2 = arith.subf %arg0, %arg1 : f64
    %v3 = arith.mulf %v1, %v2 : f64
    return %v3 : f64
  }
}
