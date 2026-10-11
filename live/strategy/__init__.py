"""Cyber-Scout strategy engine, phase 1: robot model, match simulator, backtest, preview (see README.md)."""
import os as _os

# Everything here is small dense linear algebra: multi-threaded BLAS only burns CPU (seen: 15 cores on the server).
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    _os.environ.setdefault(_v, "1")
