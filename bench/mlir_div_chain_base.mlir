module {
  func.func @div_chain_base(%arg0: f64, %arg1: f64, %arg2: f64) -> f64 {
    %v1 = arith.divf %arg0, %arg1 : f64
    %v2 = arith.divf %v1, %arg2 : f64
    return %v2 : f64
  }
}
