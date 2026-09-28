# DRIFT

**Is this machine getting worse, and what exactly is doing it.**

Every hardware monitor on Windows answers *"what is it doing right now."* None
of them answer *"is it getting worse."* DRIFT does, using only what Windows
already publishes — no driver, no admin, no install.

## It already found something

On the machine this was built for, DRIFT read the boot times Windows had been
recording for two years and reported this on the very first run:

```
last boot    41.0 s
  74 boots logged by Windows, 2024-12-09 to 2026-09-22
  oldest 10 avg 22.0 s   ->   newest 10 avg 35.5 s   (+13.5 s)
  that is 61% slower than it used to be.
```

Nobody installed anything for that. Windows had been measuring it all along.

Windows also breaks each boot into phases, and `tools/boot_forensics.ps1` reads
them out. The same machine:

```
                       total    kernel-path   desktop-phase
OLDEST 6 boots        20.4 s       7.6 s         12.9 s
NEWEST 6 boots        36.9 s      11.2 s         25.7 s
change               +16.5 s      +3.6 s        +12.8 s
```

78% of the slowdown is in the desktop phase — the time *after* the kernel is up,
spent starting programs. The kernel path barely moved and the CPU still holds
boost at 112% of base, so this is accumulated startup software, not a wearing
component. That distinction is the entire reason to measure the pattern instead
of the symptom: a tool that only reported "boot is 61% slower" would send you
shopping for a drive you do not need.

## No temperature, on purpose

The obvious way to answer "is it degrading" is temperature. On most desktops
there is no way to read one:

```
MSAcpi_ThermalZoneTemperature  ->  UNAVAILABLE: Not supported
Win32_Fan                      ->  returns nothing
```

That is exactly why HWiNFO, LibreHardwareMonitor and Open Hardware Monitor all
install a signed kernel driver and ask for administrator. DRIFT does not. It
uses the CPU's **effective clock** instead — a counter that already exists, and
which falls for the same reasons a temperature would have told you about.

## Why it records three numbers and not one

Raw speed is not a degradation signal, because a CPU's speed moves with its
boost clock. So a reading is three numbers:

| | |
|---|---|
| **throughput** | fixed work per second |
| **clock** | effective clock, % of base |
| **efficiency** | throughput divided by clock |

Those separate the two failure modes that a single score would report
identically:

- **throughput down, clock down, efficiency flat** → the core is fine, it is
  just not being allowed to run. Dust and dried thermal paste look like this.
- **throughput down, clock flat, efficiency down** → the core is doing less per
  MHz. That is the compute side, or something new sharing the machine.

## Use

```
RUN.bat          take a reading and say what changed
RUN.bat boots    the boot-time trend on its own (no waiting)
RUN.bat show     every stored reading and the current verdict
RUN.bat where    the history file and what machine this is
```

A reading takes about 20 seconds and briefly loads one CPU core. Run it weekly.
The history is one CSV next to the program; delete it to start over.

## What it refuses to do

Compare readings that should not be compared. The speed of the workload depends
on the Python version and on whether this is the compiled build or the plain
script, so a change in any of those starts a new baseline instead of inventing a
speed drop that is really a software change.

A reading taken while the machine was busy is still saved, but marked, because
its numbers are not trustworthy. Baselines use the **median**, not the mean, so
one bad reading cannot create a trend.

## Honest limits

- **The boot trend is Windows' data, not a measurement.** It is real and it is
  already on disk, but it measures *time to usable desktop*, which includes
  startup programs and services — not just disk or CPU. A rising boot time
  points at "something changed in your startup", not automatically at hardware.
- **One core, briefly.** The CPU test runs a single thread for about two
  seconds. It will not detect a problem that only appears under heavy
  all-core load.
- **No disk test yet.** Storage degradation is not measured. It is the obvious
  next addition.
- **The first reading is always a baseline.** There is nothing to compare it
  to, and DRIFT says so rather than guessing.
- **Attribution is a reading of the pattern, not a diagnosis.** It tells you
  which subsystem the numbers moved in. It cannot see inside your case.

## Running from an argument

Nothing here needs the network, and nothing is sent anywhere. The only things
it runs are Windows' own `typeperf` and PowerShell, both of which are already
part of the operating system.

## Tests

```
python -m unittest discover -s tests -v
```

21 tests, covering the judgement rather than the measurements: that the
cooling and compute cases are told apart, that a reading from a different
Python build is not compared, and that one bad reading cannot move a baseline.
