module {
  func.func @diff_sqrt_opt(%arg0: f64) -> f64 {
    %v1 = arith.constant 1.00000000000000000e+00 : f64
    %v2 = arith.addf %v1, %arg0 : f64
    %v3 = math.sqrt %v2 : f64
    %v4 = math.sqrt %arg0 : f64
    %v5 = arith.subf %v3, %v4 : f64
    return %v5 : f64
  }
}
