"""Tests for DRIFT's judgement, not its measurements.

The measurements are real numbers from performance counters; the thing that can
be wrong is what DRIFT concludes from them. These tests pin the three-way split
that separates "not allowed to run fast" from "does less per cycle", because
getting that backwards would make the program confidently wrong.

Run:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import drift  # noqa: E402


BINDING = {
    "cpu": "AMD Ryzen 5 3600 6-Core Processor",
    "cores": "12",
    "interp": "py3.14.0",
    "os": "Windows11",
    "version": "1.0.0",
}


def row(work=1000.0, clock=110.0, memory=230.0, boot_s=30.0,
        note="", **over) -> dict:
    """A reading, with efficiency derived the way the real sampler derives it."""
    r = {
        "when": "2026-09-01T10:00:00+07:00",
        **BINDING,
        "work": work,
        "clock": clock,
        "efficiency": work / clock * 100.0 if clock else float("nan"),
        "memory": memory,
        "boot_s": boot_s,
        "busy": 3.0,
        "note": note,
    }
    r.update(over)
    return r


PEERS = [row(), row(work=1010.0), row(work=990.0)]


class TestVerdict(unittest.TestCase):

    def test_first_run_says_baseline(self):
        out = drift.analyse(row(), [])
        self.assertIn("baseline", out.lower())

    def test_stable_reports_noise(self):
        out = drift.analyse(row(work=1005.0), PEERS)
        self.assertIn("not moved beyond noise", out)

    def test_clock_lagging_work_means_cooling(self):
        # Throughput down 10%, clock down 10%, efficiency therefore flat.
        out = drift.analyse(row(work=900.0, clock=99.0), PEERS)
        self.assertIn("COOLING OR POWER DELIVERY", out)
        self.assertNotIn("COMPUTE SIDE", out)

    def test_efficiency_loss_means_compute(self):
        # Throughput down 10% at the SAME clock: less done per MHz.
        out = drift.analyse(row(work=900.0, clock=110.0), PEERS)
        self.assertIn("COMPUTE SIDE", out)
        self.assertNotIn("COOLING OR POWER", out)

    def test_faster_is_reported_as_faster(self):
        out = drift.analyse(row(work=1200.0, clock=115.0), PEERS)
        self.assertIn("UP", out)
        self.assertIn("Nothing is degrading", out)

    def test_memory_drop_blamed_on_storage_not_cpu(self):
        out = drift.analyse(row(work=1005.0, memory=120.0), PEERS)
        self.assertIn("MEMORY is down", out)
        self.assertIn("not the processor", out)

    def test_slow_boot_flagged(self):
        out = drift.analyse(row(work=1005.0, boot_s=45.0), PEERS)
        self.assertIn("boot was", out)

    def test_busy_run_is_flagged_as_noisy(self):
        out = drift.analyse(row(note="busy:48%"), PEERS)
        self.assertIn("Caution", out)
        self.assertIn("busy", out)

    def test_reading_from_another_python_is_not_compared(self):
        # A Python upgrade changes the speed of the workload itself. Treating
        # that as hardware drift would be a fabricated finding.
        peers = [row(interp="py3.12.0"), row(interp="py3.12.0")]
        out = drift.analyse(row(interp="py3.14.0"), peers)
        self.assertIn("baseline", out.lower())
        self.assertNotIn("COOLING", out)

    def test_reading_from_another_cpu_is_not_compared(self):
        peers = [row(cpu="Intel i5"), row(cpu="Intel i5")]
        out = drift.analyse(row(cpu="AMD Ryzen 5 3600 6-Core Processor"), peers)
        self.assertIn("baseline", out.lower())

    def test_one_bad_reading_does_not_move_the_baseline(self):
        # Median, not mean: an outlier must not create a fake trend.
        peers = [row(work=1000.0), row(work=1000.0), row(work=100.0)]
        out = drift.analyse(row(work=1000.0), peers)
        self.assertIn("not moved beyond noise", out)


class TestHelpers(unittest.TestCase):

    def test_nan_prints_as_not_available(self):
        self.assertEqual(drift._fmt(float("nan")), "n/a")

    def test_delta_of_nan_is_blank(self):
        self.assertEqual(drift._delta(float("nan")).strip(), "--")

    def test_num_tolerates_garbage(self):
        self.assertNotEqual(drift._num({"work": "abc"}, "work"),
                            drift._num({"work": "abc"}, "work"))  # still NaN

    def test_boot_trend_needs_enough_boots(self):
        out = drift._boot_trend([("2026-01-01", 10.0)] * 5)
        self.assertIn("not enough to trend", out[0])

    def test_boot_trend_detects_slowdown(self):
        history = ([("2024-12-09", 20.0)] * 10) + ([("2026-09-22", 40.0)] * 10)
        out = "\n".join(drift._boot_trend(history))
        self.assertIn("+20.0 s", out)
        self.assertIn("100% slower", out)

    def test_boot_trend_quiet_when_stable(self):
        history = ([("2024-12-09", 30.0)] * 10) + ([("2026-09-22", 30.5)] * 10)
        out = "\n".join(drift._boot_trend(history))
        self.assertNotIn("slower than it used to be", out)


class TestHistoryFile(unittest.TestCase):

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.orig = drift.HISTORY
        drift.HISTORY = self.tmp / "h.csv"

    def tearDown(self):
        drift.HISTORY = self.orig

    def test_round_trip(self):
        drift.append_row(row())
        drift.append_row(row(work=950.0))
        rows = drift.load_history()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["cpu"], BINDING["cpu"])

    def test_header_written_once(self):
        drift.append_row(row())
        drift.append_row(row())
        text = drift.HISTORY.read_text()
        self.assertEqual(text.count("when,version"), 1)

    def test_missing_file_is_not_an_error(self):
        self.assertEqual(drift.load_history(), [])

    def test_corrupt_file_is_not_an_error(self):
        drift.HISTORY.write_text("\x00\x00not a csv at all")
        self.assertIsInstance(drift.load_history(), list)

    def test_stale_header_is_not_read_as_data(self):
        # A file from a build with fewer columns would otherwise be read
        # misaligned, putting numbers in the wrong fields.
        drift.HISTORY.write_text("when,work\n2026-01-01,1000\n")
        self.assertEqual(drift.load_history(), [])

    def test_stale_header_file_is_kept_not_destroyed(self):
        drift.HISTORY.write_text("when,work\n2026-01-01,1000\n")
        drift.append_row(row())
        kept = list(self.tmp.glob("h.csv.*.old"))
        self.assertEqual(len(kept), 1, "the old readings should be set aside")
        self.assertIn("2026-01-01", kept[0].read_text())
        self.assertEqual(len(drift.load_history()), 1)

    def test_runtime_is_recorded_and_binds_comparability(self):
        drift.append_row(row(runtime="python"))
        self.assertEqual(drift.load_history()[0]["runtime"], "python")
        self.assertIn("runtime", drift.BINDING_FIELDS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
