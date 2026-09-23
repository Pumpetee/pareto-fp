module {
  func.func @sq_diff_base(%arg0: f64, %arg1: f64) -> f64 {
    %v1 = arith.mulf %arg0, %arg0 : f64
    %v2 = arith.mulf %arg1, %arg1 : f64
    %v3 = arith.subf %v1, %v2 : f64
    return %v3 : f64
  }
}
