module {
  func.func @two_div_opt(%arg0: f64, %arg1: f64, %arg2: f64) -> f64 {
    %v1 = arith.addf %arg0, %arg2 : f64
    %v2 = arith.divf %v1, %arg1 : f64
    return %v2 : f64
  }
}
