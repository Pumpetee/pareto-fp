module {
  func.func @poly_opt(%arg0: f64) -> f64 {
    %v1 = arith.constant 4.50000000000000000e+00 : f64
    %v2 = arith.mulf %arg0, %v1 : f64
    %v3 = arith.constant 3.50000000000000000e+00 : f64
    %v4 = arith.addf %v2, %v3 : f64
    %v5 = arith.mulf %arg0, %arg0 : f64
    %v6 = arith.mulf %v4, %v5 : f64
    %v7 = arith.constant 1.50000000000000000e+00 : f64
    %v8 = arith.constant 2.50000000000000000e+00 : f64
    %v9 = arith.mulf %v8, %arg0 : f64
    %v10 = arith.addf %v7, %v9 : f64
    %v11 = arith.addf %v6, %v10 : f64
    return %v11 : f64
  }
}
