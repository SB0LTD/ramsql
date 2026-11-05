Debug builds run with `--workers` no longer crash on the first query.

- Each reactor thread had a fixed 4 MiB stack, but one Debug `dispatch` frame was 4.6 MB. Debug code keeps a stack copy of the 1.15 MB protocol session union every time it is compared or switched on by value.
- ZPM #123 gives Debug workers the 16 MiB main-thread stack; optimized builds keep 4 MiB.
- The server's per-worker memory grant now follows the worker stack size.
- Protocol tests read a small `protocol` field instead of comparing the union, which removes five of those copies.

CI now builds Debug and runs `tools/test_workers.py --smoke` with four reactors on Linux and Windows.
