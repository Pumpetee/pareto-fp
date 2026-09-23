module {
  func.func @poly_base(%arg0: f64) -> f64 {
    %v1 = arith.constant 1.50000000000000000e+00 : f64
    %v2 = arith.constant 2.50000000000000000e+00 : f64
    %v3 = arith.mulf %v2, %arg0 : f64
    %v4 = arith.addf %v1, %v3 : f64
    %v5 = arith.constant 3.50000000000000000e+00 : f64
    %v6 = arith.mulf %arg0, %arg0 : f64
    %v7 = arith.mulf %v5, %v6 : f64
    %v8 = arith.addf %v4, %v7 : f64
    %v9 = arith.constant 4.50000000000000000e+00 : f64
    %v10 = arith.mulf %arg0, %arg0 : f64
    %v11 = arith.mulf %arg0, %v10 : f64
    %v12 = arith.mulf %v9, %v11 : f64
    %v13 = arith.addf %v8, %v12 : f64
    return %v13 : f64
  }
}
