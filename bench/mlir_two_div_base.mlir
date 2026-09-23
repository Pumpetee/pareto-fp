module {
  func.func @two_div_base(%arg0: f64, %arg1: f64, %arg2: f64) -> f64 {
    %v1 = arith.divf %arg0, %arg1 : f64
    %v2 = arith.divf %arg2, %arg1 : f64
    %v3 = arith.addf %v1, %v2 : f64
    return %v3 : f64
  }
}
