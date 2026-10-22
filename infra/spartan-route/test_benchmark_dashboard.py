"""Disposable representative benchmark contracts; no operator database input."""
import tempfile
import unittest
from pathlib import Path

from benchmark_dashboard import populate, representative_benchmark, representative_rows
from spartan_route.telemetry import TelemetryStore


class BenchmarkTests(unittest.TestCase):
    def test_seeded_fixture_has_wide_context_skew_and_variable_whole_turns(self):
        rows = list(representative_rows(1000, 2048, 7126, '2026-10-22'))
        self.assertEqual(rows, list(representative_rows(1000, 2048, 7126, '2026-10-22')))
        self.assertNotEqual(rows, list(representative_rows(1000, 2048, 7127, '2026-10-22')))
        self.assertEqual(len({row[3] for row in rows}), 1000)
        self.assertGreater(sum(row[1] == 'session-0' for row in rows), 500)
        counts = {}
        for row in rows:
            counts[row[2]] = counts.get(row[2], 0) + 1
            self.assertGreater(len(row[6]), 2048)
        self.assertGreater(len(set(counts.values())), 3)

    def test_file_queries_preserve_whole_turn_counts_and_filter(self):
        with tempfile.TemporaryDirectory() as root:
            store = TelemetryStore(str(Path(root) / 'fixture.db'))
            try:
                populate(store, 500, 512, 7126, '2026-10-22')
                payload = store.live_sessions(session_limit=10, turn_limit=5, prompt='needle')
                self.assertTrue(payload['sessions'])
                for session in payload['sessions']:
                    expected = store._conn.execute(
                        "SELECT turn_id, count(*) AS n FROM routing_log WHERE session_id=? AND prompt_preview='needle' "
                        "GROUP BY turn_id ORDER BY max(id) DESC LIMIT 5", (session['id'],),
                    ).fetchall()
                    self.assertEqual([turn['id'] for turn in session['turns']], [row['turn_id'] for row in expected])
                    self.assertEqual(session['metrics']['requests'], sum(row['n'] for row in expected))
                    self.assertEqual([turn['metrics']['requests'] for turn in session['turns']], [row['n'] for row in expected])
                self.assertEqual(store.today_summary('2026-10-22')['loggedRequests'], 500)
            finally:
                store.close()

    def test_bounded_run_reopens_and_matches_all_payloads(self):
        result = representative_benchmark(100, 128, 7126, 3, 10)
        self.assertEqual(result['dataset']['rows'], 100)
        self.assertGreater(result['dataset']['databaseFamilyBytes'], 0)
        self.assertEqual(set(result['payloadSha256']), {'filtered', 'unfiltered', 'summary'})
        for measurement in result['timings'].values():
            self.assertEqual(len(measurement['samplesMs']), 3)
            self.assertGreaterEqual(measurement['minMs'], 0)
        with self.assertRaisesRegex(ValueError, 'budget'):
            representative_benchmark(50000, 16384, 7126, 3, 10)
        with self.assertRaisesRegex(ValueError, 'budget'):
            representative_benchmark(99, 128, 7126, 3, 10)


if __name__ == '__main__':
    unittest.main()
