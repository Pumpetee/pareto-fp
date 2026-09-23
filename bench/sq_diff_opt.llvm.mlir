module {
  llvm.func @sq_diff_opt(%arg0: f64, %arg1: f64) -> f64 {
    %0 = llvm.fadd %arg0, %arg1 : f64
    %1 = llvm.fsub %arg0, %arg1 : f64
    %2 = llvm.fmul %0, %1 : f64
    llvm.return %2 : f64
  }
}

