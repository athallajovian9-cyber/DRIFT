# Changelog

## 1.0.0

First release.

**The question.** Every hardware monitor on Windows answers "what is it doing
right now". None answer "is it getting worse". DRIFT answers that, using only
what Windows already publishes — no driver, no admin, no install.

**No temperature, deliberately.** On most desktops the temperature path does
not exist without a kernel driver:

```
MSAcpi_ThermalZoneTemperature  ->  UNAVAILABLE: Not supported
Win32_Fan                      ->  returns nothing
```

That is why the established monitors ship a signed driver and ask for
administrator. DRIFT uses the CPU's effective clock instead, which falls for
the same reasons a temperature would have told you about, and needs nothing
installed.

**Three numbers, not one.** Raw throughput moves with the boost clock, so it
cannot tell "not allowed to run fast" from "does less per cycle". DRIFT records
throughput, the clock it ran at, and efficiency (throughput per unit of clock),
which separates cooling and power degradation from compute degradation.

**Reads history that already exists.** Windows logs one boot-performance event
per boot going back years. On the machine this was built for that is 74 boots
since December 2024, and it means the first run answers the question instead of
waiting weeks for a baseline.

**Refuses to lie.** Readings are never compared across a change in CPU,
interpreter, OS, or build (compiled vs script), because a Python upgrade would
otherwise look exactly like a hardware speed change. Baselines use the median,
so one bad reading cannot create a trend, and a reading taken while the machine
was busy is marked rather than trusted.

### Fixed during the build

These were found by running the thing rather than by reading it:

- **The packaged exe lost its history on every run.** Nuitka's onefile build
  runs from a temporary extraction folder, so `__file__` pointed there and the
  history was written to `%TEMP%\onefile_*\`. For a program whose entire purpose
  is accumulating history, that is fatal. It now resolves the real executable.
- **A change of columns corrupted the history silently.** Adding a column left
  older rows misaligned under the new header, putting throughput values in the
  `os` column. A file with a different header is now set aside rather than
  written through.
- **`version` was recorded as empty** while still being used as a comparability
  key.
- **The boot-history read failed intermittently**, writing `nan` into the
  history — which reads as "measured, found nothing" rather than "the read
  failed". Now retried once.

### Tests

24 tests, covering the judgement rather than the measurements: that the cooling
and compute cases are told apart, that a reading from a different Python build
is refused, and that one bad reading cannot move a baseline.
