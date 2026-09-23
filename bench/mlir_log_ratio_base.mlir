module {
  func.func @log_ratio_base(%arg0: f64, %arg1: f64) -> f64 {
    %v1 = math.log %arg0 : f64
    %v2 = math.log %arg1 : f64
    %v3 = arith.subf %v1, %v2 : f64
    return %v3 : f64
  }
}
