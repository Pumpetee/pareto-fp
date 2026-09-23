module {
  func.func @log_ratio_opt(%arg0: f64, %arg1: f64) -> f64 {
    %v1 = arith.divf %arg0, %arg1 : f64
    %v2 = math.log %v1 : f64
    return %v2 : f64
  }
}
