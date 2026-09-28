#!/usr/bin/env python3
"""
DRIFT - is this machine getting worse, and what exactly is doing it?

Every hardware monitor on Windows answers "what is it doing right now".
None of them answer "is it getting worse". That is a different question, and
answering it is the whole point of this program.

WHY THERE IS NO TEMPERATURE HERE
--------------------------------
Temperature is how everyone tries to answer this, and on most desktops it is
simply unavailable without a kernel driver:

    MSAcpi_ThermalZoneTemperature  ->  UNAVAILABLE: Not supported
    Win32_Fan                      ->  returns nothing

That is why HWiNFO, LibreHardwareMonitor and Open Hardware Monitor all install
a signed kernel driver and ask for administrator. This program cannot and does
not. Instead it uses the CPU's EFFECTIVE CLOCK, a counter Windows already
publishes, which drops when a machine throttles for exactly the same reasons
temperature would have told you about. No driver, no admin, no install.

THE ONE IDEA THAT MAKES THIS HONEST
-----------------------------------
Raw speed is not a degradation signal, because a CPU's speed moves around with
its boost clock. So DRIFT records three numbers, not one:

    work        fixed work per second            (raw throughput)
    clock       effective clock, % of base       (how fast it was running)
    efficiency  work divided by clock            (how much it did PER MHz)

Now the two failure modes separate cleanly:

    work down, clock down, efficiency flat   -> cooling or power delivery.
                                                The core is fine; it is not
                                                being allowed to run.
    work down, clock flat, efficiency down   -> the core itself is doing less
                                                per cycle. Same clock, less done.

A tool that only recorded "score" would report both of those identically.

WHAT IT REFUSES TO DO
---------------------
Compare runs that should not be compared. A Python upgrade changes how fast
Python runs, which has nothing to do with the hardware, so a version change
starts a new baseline instead of faking a speed drop. A run on a busy machine
is recorded but marked, because its numbers are not trustworthy.
"""

from __future__ import annotations

import csv
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------- constants

VERSION = "1.0.0"

# Enough work to be measurable, short enough that a weekly run is invisible.
# The actual iteration count is calibrated at run time, because the same loop
# runs roughly fifty times faster compiled than interpreted, and CPU speeds
# differ by an order of magnitude between machines. The reported score is work
# per second, so the count does not affect comparability - but a run that
# finished in five milliseconds would be measuring the timer, not the CPU.
CPU_TARGET_S = 2.0
MEM_TARGET_S = 1.0

CPU_CLOCK_COUNTER = r"\Processor Information(_Total)\% Processor Performance"
CPU_BUSY_COUNTER = r"\Processor Information(_Total)\% Processor Time"

HERE = Path(__file__).resolve().parent


def _app_dir() -> Path:
    """The folder the user actually put this program in.

    Nuitka's onefile build unpacks itself into a temporary folder and runs from
    there, so __file__ points at a throwaway copy. Writing the history beside
    it would lose every reading on exit - which is fatal for a program whose
    entire purpose is accumulating history. sys.argv[0] is the real
    executable, the one the user double-clicked.
    """
    if "__compiled__" in globals():
        return Path(os.path.abspath(sys.argv[0])).parent
    return HERE


APP_DIR = _app_dir()
HISTORY = APP_DIR / "drift_history.csv"

FIELDS = [
    "when", "version", "cpu", "cores", "interp", "runtime", "os",
    "work", "clock", "efficiency", "memory", "boot_s", "busy", "note",
]

# A change in any of these makes older readings non-comparable. Python's own
# speed is part of the measurement, so a Python upgrade would otherwise look
# exactly like the CPU getting slower. Same for runtime: the packaged .exe is
# compiled to native code and does the same loop far faster than the
# interpreter, so mixing the two would look like a 50x speed-up that never
# happened.
BINDING_FIELDS = ["cpu", "cores", "interp", "runtime", "os", "version"]


def _runtime() -> str:
    """Which build this is: the compiled .exe, or the plain script.

    These produce wildly different numbers for identical hardware, so they are
    never compared against each other.
    """
    return "compiled" if "__compiled__" in globals() else "python"


# ---------------------------------------------------------------- utilities

def _run(cmd: list[str], timeout: int = 60) -> str:
    """Run a command and return stdout. Never raises for a non-zero exit."""
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return p.stdout or ""
    except Exception:
        return ""


def _powershell(script: str, timeout: int = 60) -> str:
    return _run(["powershell", "-NoProfile", "-NonInteractive",
                 "-Command", script], timeout=timeout)


class CounterSampler:
    """Samples Windows performance counters in a background process.

    typeperf is part of Windows and needs no installation. It is used rather
    than a Python counter library because that would mean shipping a
    dependency, and the point of this program is that it ships with nothing.
    """

    def __init__(self, counters: list[str], seconds: int) -> None:
        self.path = APP_DIR / "_counters.csv"
        self.path.unlink(missing_ok=True)
        args = ["typeperf", *counters, "-si", "1", "-sc", str(seconds),
                "-o", str(self.path)]
        self.proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def finish(self) -> list[list[float]]:
        """Wait for the sampler and return one list of values per counter."""
        try:
            self.proc.wait(timeout=120)
        except Exception:
            self.proc.kill()
        rows: list[list[float]] = []
        try:
            with open(self.path, newline="") as fh:
                data = list(csv.reader(fh))
            for row in data[1:]:
                vals = []
                for cell in row[1:]:
                    try:
                        vals.append(float(cell))
                    except ValueError:
                        vals.append(float("nan"))
                if vals:
                    rows.append(vals)
        except Exception:
            rows = []
        finally:
            self.path.unlink(missing_ok=True)

        if not rows:
            return [[] for _ in counters]
        width = len(rows[0])
        return [[r[i] for r in rows if i < len(r) and r[i] == r[i]]
                for i in range(width)]


def _avg(values: list[float]) -> float:
    return statistics.mean(values) if values else float("nan")


def _fmt(value: float, places: int = 2, suffix: str = "") -> str:
    if value != value:          # NaN
        return "n/a"
    return f"{value:.{places}f}{suffix}"


def _machine() -> dict[str, str]:
    cpu = _powershell(
        "(Get-CimInstance Win32_Processor).Name"
    ).strip().splitlines()
    cores = _powershell(
        "(Get-CimInstance Win32_Processor).NumberOfLogicalProcessors"
    ).strip().splitlines()
    return {
        "cpu": cpu[0].strip() if cpu else "unknown",
        "cores": cores[0].strip() if cores else "?",
        "interp": f"py{platform.python_version()}",
        "runtime": _runtime(),
        "os": f"{platform.system()}{platform.release()}",
    }


# ---------------------------------------------------------------- workloads

def _timed_loop(iters: int) -> float:
    """Fixed, deterministic integer work. Returns seconds taken.

    The same loop every run is the entire measurement: if it takes longer this
    week than last week, something is slower, and nothing about this code
    changed to cause it.
    """
    start = time.perf_counter()
    x = 12345
    for _ in range(iters):
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
    return time.perf_counter() - start


def _calibrate_cpu(target: float = CPU_TARGET_S) -> int:
    """Iterations of the workload that take about `target` seconds.

    Calibrated every run rather than hardcoded so the measurement lands in the
    same place on a compiled build and an interpreted one.
    """
    probe = 1_000_000
    seconds = _timed_loop(probe)
    if seconds <= 0:
        return 20_000_000
    return max(1_000_000, min(int(probe * target / seconds), 400_000_000))


def _cpu_work(iters: int) -> float:
    return _timed_loop(iters)


def _mem_plan() -> tuple[int, int]:
    """Buffer size and repeat count that make the memory test take ~1 second.

    Repeats rather than one huge buffer: a compiled build scans 128 MB in a few
    milliseconds, and growing the buffer to compensate would mean allocating
    gigabytes on a machine that may not have them.
    """
    size = 128 * 1024 * 1024
    buf = bytearray(size)
    start = time.perf_counter()
    sum(buf)
    once = time.perf_counter() - start
    del buf
    if once <= 0:
        return size, 8
    return size, max(1, min(int(MEM_TARGET_S / once), 64))


def _memory_work(size: int, repeats: int) -> float:
    """Fixed sequential scan of a fixed buffer, repeated. Returns seconds.

    Deliberately touches memory nothing else is using, so this measures the
    memory subsystem rather than the page cache.
    """
    buf = bytearray(size)
    start = time.perf_counter()
    for _ in range(repeats):
        sum(buf)            # the scan itself; result is always 0
    elapsed = time.perf_counter() - start
    del buf
    return elapsed


_BOOT_PS = (
    "$all = Get-WinEvent -LogName "
    "'Microsoft-Windows-Diagnostics-Performance/Operational' "
    "-FilterXPath '*[System[(EventID=100)]]' -ErrorAction SilentlyContinue; "
    "foreach ($ev in $all) { [xml]$x = $ev.ToXml(); "
    "$b = ($x.Event.EventData.Data | "
    "Where-Object { $_.Name -eq 'BootTime' }).'#text'; "
    "if ($b) { $ev.TimeCreated.ToString('yyyy-MM-dd') + \"`t\" + [int]$b } }"
)


_boot_cache: list[tuple[str, float]] | None = None


def _boot_history(refresh: bool = False) -> list[tuple[str, float]]:
    """Every boot Windows has timed, oldest first, as (date, seconds).

    Windows has been recording boot performance on this machine since at least
    Dec 2024 with no tool installed and nothing configured. That log is a real
    degradation history that already exists, so it costs nothing to read and
    gives an answer on the very first run - before DRIFT has any history of
    its own. Cached, because reading 74 events through PowerShell is not free.
    """
    global _boot_cache
    if _boot_cache is not None and not refresh:
        return _boot_cache
    rows: list[tuple[str, float]] = []
    # Retried once: the event query occasionally comes back empty and succeeds
    # on the second try. A single failed read must not become a NaN in the
    # history - a missing number in a trend file reads as a reading that was
    # taken and found nothing, which is a different claim from "the read
    # failed".
    for _try, timeout in ((1, 180), (2, 240)):
        for line in _powershell(_BOOT_PS, timeout=timeout).splitlines():
            date, _, ms = line.strip().partition("\t")
            try:
                rows.append((date.strip(), int(ms) / 1000.0))
            except ValueError:
                continue
        if rows:
            break
    rows.sort()
    if rows:
        _boot_cache = rows
    return rows


def _boot_seconds() -> float:
    """How long Windows last took to boot, from its own diagnostic log."""
    history = _boot_history()
    return history[-1][1] if history else float("nan")


def _boot_trend(history: list[tuple[str, float]], window: int = 10) -> list[str]:
    """Compare the newest boots against the oldest ones.

    Uses the first and last `window` boots rather than a straight line through
    all of them: a single slow boot is noise (an update, a new driver), and a
    trend that jumps on one reading would be a liar.
    """
    if len(history) < window * 2:
        return [f"Only {len(history)} boot(s) logged - not enough to trend yet."]
    first = [s for _, s in history[:window]]
    last = [s for _, s in history[-window:]]
    a, b = statistics.mean(first), statistics.mean(last)
    span = f"{history[0][0]} to {history[-1][0]}"
    out = [f"last boot    {history[-1][1]:.1f} s",
           f"  {len(history)} boots logged by Windows, {span}",
           f"  oldest {window} avg {a:.1f} s   ->   newest {window} avg {b:.1f} s"
           f"   ({b - a:+.1f} s)"]
    if a > 0 and (b - a) / a > 0.25:
        out.append(f"  that is {(b - a) / a * 100:.0f}% slower than it used to be."
                   f" Windows is taking longer to become usable than it did in"
                   f" {history[0][0][:7]}.")
    return out


def _busy_level() -> float:
    """Average CPU use over a second, to flag a reading taken on a busy box."""
    s = CounterSampler([CPU_BUSY_COUNTER], 2)
    series = s.finish()
    return _avg(series[0]) if series and series[0] else float("nan")


# ---------------------------------------------------------------- sampling

def take_sample() -> dict:
    """Measure everything once and return a history row."""
    spec = _machine()
    busy = _busy_level()

    # Calibrated before sampling starts, so the calibration load cannot pollute
    # the idle baseline.
    iters = _calibrate_cpu()
    mem_size, mem_repeats = _mem_plan()

    # One sampler for the whole run: idle -> loaded -> settling. Slicing one
    # timeline is more reliable than three separate short samples, because
    # typeperf's first interval covers a period that has already passed.
    sampler = CounterSampler([CPU_CLOCK_COUNTER], 11)

    time.sleep(4)                       # samples 0-3: idle
    work_s = _cpu_work(iters)           # samples 4-6: loaded
    time.sleep(4)                       # samples 7-10: settling

    series = sampler.finish()
    clock_all = series[0] if series else []
    idle = clock_all[1:4]
    loaded = clock_all[4:7]

    clock_idle = _avg(idle)
    clock_load = _avg(loaded)
    work = iters / work_s if work_s > 0 else float("nan")

    # work per unit of clock: the number that separates "not allowed to run
    # fast" from "does less per cycle"
    if work == work and clock_load == clock_load and clock_load > 0:
        efficiency = work / clock_load * 100.0
    else:
        efficiency = float("nan")

    mem_s = _memory_work(mem_size, mem_repeats)
    mb = mem_size / 1024 / 1024 * mem_repeats
    memory = mb / mem_s if mem_s > 0 else float("nan")

    note = ""
    if busy == busy and busy > 15:
        note = f"busy:{busy:.0f}%"

    return {
        "when": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "version": VERSION,
        **spec,
        "work": work,
        "clock": clock_load,
        "efficiency": efficiency,
        "memory": memory,
        "boot_s": _boot_seconds(),
        "busy": busy if busy == busy else "",
        "note": note,
        "_clock_idle": clock_idle,
    }


# ---------------------------------------------------------------- history

def _header_matches() -> bool:
    """Does the stored history use the columns this build writes?

    If the columns change between versions, every older row reads misaligned -
    a throughput value landing in the `os` column, and a word coming back where
    a number belongs. Comparing that would invent findings out of column
    offsets, so a file with a different header is set aside instead.
    """
    try:
        with open(HISTORY, newline="") as fh:
            return fh.readline().strip().split(",") == FIELDS
    except Exception:
        return False


def _set_aside() -> None:
    """Move a history this build cannot read out of the way, keeping it."""
    try:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        HISTORY.rename(HISTORY.with_name(HISTORY.name + f".{stamp}.old"))
    except Exception:
        try:
            HISTORY.unlink()
        except Exception:
            pass


def load_history() -> list[dict]:
    if not HISTORY.exists() or not _header_matches():
        return []
    try:
        with open(HISTORY, newline="") as fh:
            return [r for r in csv.DictReader(fh)]
    except Exception:
        return []


def append_row(row: dict) -> None:
    # An older file with different columns is kept, renamed, rather than
    # written through - writing through it would corrupt both the old readings
    # and the new one.
    if HISTORY.exists() and not _header_matches():
        _set_aside()
    fresh = not HISTORY.exists()
    with open(HISTORY, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        if fresh:
            writer.writeheader()
        writer.writerow(row)


def _num(row: dict, key: str) -> float:
    try:
        return float(row.get(key) or "nan")
    except ValueError:
        return float("nan")


def _comparable(rows: list[dict], current: dict) -> list[dict]:
    """Rows measured under the same conditions as the current one.

    A different Python build, a different CPU, or a different Windows release
    makes old readings meaningless rather than interesting. Refusing to
    compare them is the difference between a trend and a coincidence.
    """
    return [r for r in rows
            if all(r.get(k) == current.get(k) for k in BINDING_FIELDS)]


# ---------------------------------------------------------------- the verdict

def analyse(current: dict, rows: list[dict]) -> str:
    """Say what changed and what it means, in that order."""
    peers = _comparable(rows, current)
    if not peers:
        return ("First reading on this machine, interpreter and build. There "
                "is no baseline yet, so there is nothing to compare against.\n"
                "This is the baseline. Run it again in a few days.")

    line = []
    line.append(f"comparing against {len(peers)} earlier reading"
                f"{'s' if len(peers) > 1 else ''}, "
                f"oldest {peers[0]['when'][:10]}, newest {peers[-1]['when'][:10]}")

    warnings = []
    if current.get("note"):
        warnings.append(
            f"this reading was taken while the machine was busy "
            f"({current['note'].split(':', 1)[-1]} CPU) - treat it as noisy")

    # Median, not mean: one bad reading (a Windows update, a background
    # scan) should not move the baseline.
    def base(key: str) -> float:
        vals = [_num(r, key) for r in peers]
        vals = [v for v in vals if v == v]
        return statistics.median(vals) if vals else float("nan")

    work_b, clock_b, eff_b = base("work"), base("clock"), base("efficiency")
    mem_b, boot_b = base("memory"), base("boot_s")

    work_n, clock_n, eff_n = _num(current, "work"), _num(current, "clock"), _num(current, "efficiency")
    mem_n, boot_n = _num(current, "memory"), _num(current, "boot_s")

    def pct(now: float, then: float) -> float:
        if now != now or then != then or then == 0:
            return float("nan")
        return (now - then) / then * 100.0

    d_work, d_clock, d_eff = pct(work_n, work_b), pct(clock_n, clock_b), pct(eff_n, eff_b)
    d_mem, d_boot = pct(mem_n, mem_b), pct(boot_n, boot_b)

    line.append("")
    line.append(f"  throughput   {_fmt(work_n, 0):>12} /s   {_delta(d_work)}")
    line.append(f"  clock        {_fmt(clock_n, 1):>12} %    {_delta(d_clock)}")
    line.append(f"  efficiency   {_fmt(eff_n, 0):>12}      {_delta(d_eff)}")
    line.append(f"  memory       {_fmt(mem_n, 0):>12} MB/s {_delta(d_mem)}")
    line.append(f"  last boot    {_fmt(boot_n, 1):>12} s    {_delta(d_boot)}")
    line.append("")

    # Meaning, given the numbers rather than instead of them. The three-way
    # split on throughput/clock/efficiency is the whole reason this program
    # records three numbers instead of one score.
    WEAK = 4.0       # below this, treat as noise rather than a trend
    verdict = []
    if d_work != d_work:
        verdict.append("Not enough comparable history to judge yet.")
    elif d_work <= -WEAK and d_eff == d_eff and d_eff > -WEAK:
        verdict.append(
            f"Throughput is down {abs(d_work):.0f}% and the clock kept pace "
            f"with it, while efficiency held at {_fmt(eff_n, 0)}. The core is "
            f"still doing the same work per MHz. This is the signature of "
            f"COOLING OR POWER DELIVERY, not of the CPU itself: it is not "
            f"being allowed to run fast any more. Dust in the cooler and dried "
            f"thermal paste both look exactly like this.")
    elif d_work <= -WEAK and d_eff == d_eff and d_eff <= -WEAK:
        verdict.append(
            f"Throughput is down {abs(d_work):.0f}% and efficiency is down "
            f"{abs(d_eff):.0f}% at a comparable clock. The core is completing "
            f"less work per MHz, which points at the COMPUTE SIDE rather than "
            f"cooling - or at something new sharing the machine.")
    elif d_work <= -WEAK:
        verdict.append(
            f"Throughput is down {abs(d_work):.0f}% with no clear clock or "
            f"efficiency explanation in the data.")
    elif d_work >= WEAK:
        verdict.append(
            f"Throughput is UP {d_work:.0f}%. Nothing is degrading.")
    else:
        verdict.append(
            f"Throughput has not moved beyond noise ({d_work:+.1f}%, and the "
            f"threshold for a real change is {WEAK:.0f}%).")

    if d_mem == d_mem and d_mem <= -WEAK * 2 and (d_work != d_work or d_work > -WEAK):
        verdict.append(
            f"MEMORY is down {abs(d_mem):.0f}% while the CPU held steady. "
            f"Storage or memory is the suspect here, not the processor.")
    if d_boot == d_boot and d_boot >= WEAK * 3:
        verdict.append(
            f"Last boot was {d_boot:.0f}% slower than the median. A one-off is "
            f"normal; a run of these usually means something new is starting "
            f"with Windows rather than the disk wearing out.")

    if warning := warnings:
        verdict.append("Caution: " + "; ".join(warning) + ".")

    return "\n".join(line + verdict)


def _delta(value: float) -> str:
    if value != value:
        return "  --"
    return f"{value:+.1f}%"


# ---------------------------------------------------------------- commands

def cmd_run(_args: list[str]) -> int:
    print("DRIFT - taking a reading...")
    print("  this takes about 20 seconds and briefly loads one CPU core\n")
    row = take_sample()
    rows = load_history()

    print(f"  throughput   {_fmt(row['work'], 0)} /s")
    print(f"  clock        {_fmt(row['clock'], 1)} % of base   "
          f"(idle {_fmt(row['_clock_idle'], 1)} %)")
    print(f"  efficiency   {_fmt(row['efficiency'], 0)}")
    print(f"  memory       {_fmt(row['memory'], 0)} MB/s")

    # Boot history costs nothing to read and already exists, so it is printed
    # even on the very first run, when nothing else can be compared yet.
    boots = _boot_history()
    print()
    if boots:
        print("\n".join(_boot_trend(boots)))

    append_row(row)
    print()
    print(analyse(row, rows))
    print()
    print(f"saved to {HISTORY}")
    return 0


def cmd_boots(_args: list[str]) -> int:
    """The boot trend on its own - the one answer available immediately."""
    boots = _boot_history(refresh=True)
    if not boots:
        print("Windows has no boot-performance events on this machine.")
        print("(The log is normally written by the Diagnostics-Performance "
              "service; if boot tracing is off, nothing here can see it.)")
        return 1
    print("\n".join(_boot_trend(boots)))
    print()
    print("the last 15, oldest first:")
    for date, secs in boots[-15:]:
        bar = "#" * max(1, int(secs / 2))
        print(f"  {date}  {secs:6.1f} s  {bar}")
    return 0


def cmd_show(_args: list[str]) -> int:
    rows = load_history()
    if not rows:
        print("No readings yet. Run:  python drift.py run")
        return 1
    print(f"{len(rows)} reading(s) in {HISTORY}\n")
    print(f"  {'when':<20} {'throughput':>12} {'clock':>8} {'eff':>8} "
          f"{'memory':>9} {'boot':>7}  note")
    for r in rows:
        print(f"  {r['when'][:19]:<20} {_fmt(_num(r,'work'),0):>12} "
              f"{_fmt(_num(r,'clock'),1):>8} {_fmt(_num(r,'efficiency'),0):>8} "
              f"{_fmt(_num(r,'memory'),0):>9} {_fmt(_num(r,'boot_s'),1):>7}  "
              f"{r.get('note','')}")
    print()
    print(analyse(rows[-1], rows[:-1]))
    return 0


def cmd_where(_args: list[str]) -> int:
    print(f"history : {HISTORY}")
    print(f"version : {VERSION}")
    for k, v in _machine().items():
        print(f"{k:<8}: {v}")
    return 0


def main(argv: list[str]) -> int:
    commands = {"run": cmd_run, "show": cmd_show, "where": cmd_where,
                "boots": cmd_boots}
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(__doc__.strip().split("\n\n")[0])
        print("\ncommands:")
        print("  run     take a reading, save it, and report what changed")
        print("  show    print every reading and the current verdict")
        print("  boots   the boot-time trend Windows already recorded")
        print("  where   show the history file and what machine this is")
        return 0
    if argv[1] not in commands:
        print(f"unknown command: {argv[1]}")
        return 2
    return commands[argv[1]](argv[2:])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
