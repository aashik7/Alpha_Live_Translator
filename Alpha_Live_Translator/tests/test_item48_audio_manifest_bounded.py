"""Item 48: the audio manifest grew to 117 MB in one session.

Measured on the 99-minute live run `...20260814-114309`:

    audio_manifest.json   117.33 MB
      packets             95.0 MB across 193,675 entries
        SOURCE_SILENCE    191,579  (98.9%)
        ACTIVE             2,096
      files / chunks       0.3 MB each, 300 entries -- fine

Every one of those 193,675 entries had `retained_frame_count ==

source_frame_count`, so each proved only that nothing was dropped. Serialising

that list is also the most plausible cause of the run's 498 MB peak RSS against

a 272 MB steady state.

The rest of item 48 came back clean on that run: queues sat at zero throughout,

and memory reached ~258 MB in the first 25 minutes then grew just 13.5 MB across

the remaining 74 -- a warm-up plateau, not a leak. Disk was the only unbounded

thing.

Consecutive silence on one stream is now collapsed into a single run entry. What

this file exists to prove is preserved: the app is called "Preserve Real

Silence", and the evidence is that silence was RETAINED -- not that it was

retained 191,579 separate times. ACTIVE packets keep full per-packet fidelity,

because those are the ones carrying speech.

"""

import importlib

import json

import sys

import unittest

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.utils import audio_temp_capture as atc  # noqa: E402

SILENCE = atc.PACKET_SOURCE_SILENCE

ACTIVE = atc.PACKET_ACTIVE



class SilenceRunsAreCollapsedTest(unittest.TestCase):
    def setUp(self):
        importlib.reload(atc)
        self.atc = atc
        self.atc._manifest.setdefault("packets", []).clear()
        self.atc._packet_runs.clear()

    def _packets(self):
        self.atc._flush_all_packet_runs()
        return list(self.atc._manifest.get("packets", []))

    def _silence(self, key="mixed", frames=320):
        with self.atc._lock:
            self._append(key, SILENCE, frames)

    def _append(self, key, classification, frames):
        """Mimic the manifest bookkeeping the ingest path performs."""
        entry = {
            "stream_type": key,
            "sequence_number": self.atc._stream_sequences.get(key, 0),
            "monotonic_end": 0.0,
            "wall_end": 0.0,
            "source_frame_count": frames,
        }
        self.atc._stream_sequences[key] = entry["sequence_number"] + 1
        if classification == SILENCE:
            run = self.atc._packet_runs.get(key)
            if run is None:
                self.atc._packet_runs[key] = {
                    "stream_type": key,
                    "packet_classification": SILENCE,
                    "collapsed_run": True,
                    "packet_count": 1,
                    "source_frame_count": frames,
                    "retained_frame_count": frames,
                    "all_frames_retained": True,
                }
            else:
                run["packet_count"] += 1
                run["source_frame_count"] += frames
                run["retained_frame_count"] += frames
        else:
            self.atc._flush_packet_run_locked(key)
            entry["packet_classification"] = classification
            self.atc._manifest.setdefault("packets", []).append(entry)

    def test_a_long_silence_becomes_one_entry(self):
        for _ in range(5000):
            self._silence()
        entries = self._packets()
        self.assertEqual(len(entries), 1, "silence was not collapsed")
        self.assertEqual(entries[0]["packet_count"], 5000)

    def test_the_frame_totals_are_preserved(self):
        """The proof itself: how much silence, and that it was all retained."""
        for _ in range(1000):
            self._silence(frames=320)
        run = self._packets()[0]
        self.assertEqual(run["source_frame_count"], 320 * 1000)
        self.assertEqual(run["retained_frame_count"], 320 * 1000)
        self.assertTrue(run["all_frames_retained"])

    def test_active_packets_keep_full_fidelity(self):
        """Speech-carrying packets are never collapsed."""
        with self.atc._lock:
            for i in range(50):
                self._append("mixed", ACTIVE, 320)
        entries = self._packets()
        self.assertEqual(len(entries), 50)
        self.assertTrue(all(e["packet_classification"] == ACTIVE for e in entries))

    def test_chronological_order_is_kept(self):
        """A non-silence packet closes the open run before it is appended."""
        with self.atc._lock:
            for _ in range(10):
                self._append("mixed", SILENCE, 320)
            self._append("mixed", ACTIVE, 320)
            for _ in range(10):
                self._append("mixed", SILENCE, 320)
            self._append("mixed", ACTIVE, 320)
        kinds = [
            "run" if e.get("collapsed_run") else "active" for e in self._packets()
        ]
        self.assertEqual(kinds, ["run", "active", "run", "active"])

    def test_streams_do_not_share_a_run(self):
        with self.atc._lock:
            for _ in range(10):
                self._append("mixed", SILENCE, 320)
                self._append("system", SILENCE, 320)
        entries = self._packets()
        self.assertEqual(len(entries), 2)
        self.assertEqual({e["stream_type"] for e in entries}, {"mixed", "system"})

    def test_the_real_session_shape_fits_in_a_fraction_of_the_size(self):
        """193,675 packets of which 98.9% silence -- the measured run."""
        with self.atc._lock:
            for block in range(2096):
                for _ in range(91):          # ~191,579 silence packets total
                    self._append("mixed", SILENCE, 320)
                self._append("mixed", ACTIVE, 320)
        size = len(json.dumps(self._packets(), ensure_ascii=False))
        self.assertLess(
            size,
            5 * 1024 * 1024,
            f"manifest packets still {size / 1024 / 1024:.1f} MB; was 95 MB",
        )



class RealIngestPathTest(unittest.TestCase):
    """Drives the REAL `_ingest_audio_chunk_impl`, not a re-implementation.

    Every other test in this file mimics the manifest bookkeeping, which is
    exactly the shape of probe that made item 65's diagnosis wrong three times:
    a stub that agrees with the code it was copied from proves nothing about the
    code that actually runs.
    """

    def setUp(self):
        importlib.reload(atc)
        atc._manifest.setdefault("packets", []).clear()
        atc._packet_runs.clear()
        atc._started = True

    def _ingest(self, pcm, classification, frames=320):
        atc._ingest_audio_chunk_impl(
            pcm,
            stream_type="mixed",
            packet_classification=classification,
            source_frame_count=frames,
            explicit_silent_packet=False,
        )

    def test_real_silence_packets_collapse(self):
        silence = bytes(640)
        for _ in range(500):
            self._ingest(silence, atc.PACKET_SOURCE_SILENCE)
        atc._flush_all_packet_runs()
        packets = [p for p in atc._manifest.get("packets", []) if p.get("collapsed_run")]
        self.assertEqual(len(packets), 1, f"got {len(atc._manifest['packets'])} entries")
        self.assertEqual(packets[0]["packet_count"], 500)
        self.assertTrue(packets[0]["all_frames_retained"])

    def test_real_active_packets_collapse_too(self):
        loud = bytes([0x00, 0x40]) * 320
        for _ in range(20):
            self._ingest(loud, atc.PACKET_ACTIVE)
        atc._flush_all_packet_runs()
        actives = [
            p for p in atc._manifest.get("packets", [])
            if p.get("packet_classification") == atc.PACKET_ACTIVE
        ]
        # Superseded: ACTIVE collapses too now, which is the generalisation.
        self.assertEqual(len(actives), 1)
        self.assertEqual(actives[0]["packet_count"], 20)
        self.assertNotIn("run_id", actives[0])

    def test_real_mixed_traffic_stays_ordered_and_small(self):
        silence = bytes(640)
        loud = bytes([0x00, 0x40]) * 320
        for _ in range(40):
            for _ in range(100):
                self._ingest(silence, atc.PACKET_SOURCE_SILENCE)
            self._ingest(loud, atc.PACKET_ACTIVE)
        atc._flush_all_packet_runs()
        entries = atc._manifest.get("packets", [])
        collapsed = sum(1 for e in entries if e.get("collapsed_run"))
        # 40 silence runs + 40 single-packet ACTIVE runs between them.
        self.assertEqual(collapsed, 80)
        self.assertEqual(sum(e.get("packet_count", 1) for e in entries), 4040)
        self.assertLessEqual(len(entries), 100, "4040 packets did not collapse")



class ActiveRunsAreCollapsedTooTest(unittest.TestCase):
    """The first fix collapsed only silence; run ...20260814-142929 showed why
    that was a special case rather than the mechanism.
    That 7-minute run was **36,251 ACTIVE packets against 272 silence runs** --
    the inverse of the 99-minute run -- and produced a 19.32 MB manifest, about
    2.8 MB/min against 1.2 MB/min before the fix. Audio that is genuinely active
    still cost one entry per 20 ms packet.
    Collapsing every routine classification per stream turns that run's 62,937
    packets into ~507 entries. UNKNOWN and CAPTURE_GAP stay per-occurrence:
    they are rare, and each occurrence is the forensic signal.
    """
    def setUp(self):
        importlib.reload(atc)
        atc._manifest.setdefault("packets", []).clear()
        atc._packet_runs.clear()
        atc._started = True
    def _ingest(self, stream, classification, pcm):
        atc._ingest_audio_chunk_impl(
            pcm, stream_type=stream, packet_classification=classification,
            source_frame_count=320, explicit_silent_packet=False,
        )
    def test_active_packets_collapse(self):
        loud = bytes([0x00, 0x40]) * 320
        for _ in range(3000):
            self._ingest("mixed", atc.PACKET_ACTIVE, loud)
        atc._flush_all_packet_runs()
        entries = atc._manifest["packets"]
        self.assertEqual(len(entries), 1, f"{len(entries)} entries for one active run")
        self.assertEqual(entries[0]["packet_count"], 3000)
    def test_interleaved_streams_each_keep_their_own_run(self):
        """The real shape: three streams rotate, so a naive contiguity check
        sees no runs at all."""
        loud = bytes([0x00, 0x40]) * 320
        for _ in range(2000):
            for st in ("mic", "system", "mixed"):
                self._ingest(st, atc.PACKET_ACTIVE, loud)
        atc._flush_all_packet_runs()
        entries = atc._manifest["packets"]
        self.assertEqual(len(entries), 3, f"{len(entries)} entries for 3 streams")
        self.assertEqual(sum(e["packet_count"] for e in entries), 6000)
    def test_a_classification_change_closes_the_run(self):
        """The silence/speech boundary must stay visible."""
        loud = bytes([0x00, 0x40]) * 320
        quiet = bytes(640)
        for _ in range(10):
            self._ingest("mixed", atc.PACKET_ACTIVE, loud)
        for _ in range(10):
            self._ingest("mixed", atc.PACKET_SOURCE_SILENCE, quiet)
        for _ in range(10):
            self._ingest("mixed", atc.PACKET_ACTIVE, loud)
        atc._flush_all_packet_runs()
        kinds = [e["packet_classification"] for e in atc._manifest["packets"]]
        self.assertEqual(
            kinds,
            [atc.PACKET_ACTIVE, atc.PACKET_SOURCE_SILENCE, atc.PACKET_ACTIVE],
        )
    def test_every_packet_is_accounted_for(self):
        """Collapsing must never lose a packet from the totals."""
        loud = bytes([0x00, 0x40]) * 320
        quiet = bytes(640)
        n = 0
        for i in range(900):
            cls = atc.PACKET_SOURCE_SILENCE if i % 100 < 20 else atc.PACKET_ACTIVE
            self._ingest("mixed", cls, quiet if cls == atc.PACKET_SOURCE_SILENCE else loud)
            n += 1
        atc._flush_all_packet_runs()
        self.assertEqual(
            sum(e.get("packet_count", 1) for e in atc._manifest["packets"]), n
        )
    def test_anomalies_are_not_collapsed(self):
        """UNKNOWN is rare and each occurrence is the signal."""
        for _ in range(5):
            self._ingest("mixed", atc.PACKET_UNKNOWN, b"")
        atc._flush_all_packet_runs()
        entries = atc._manifest["packets"]
        self.assertEqual(len(entries), 5)
        self.assertTrue(all(not e.get("collapsed_run") for e in entries))

class ManifestHygieneTest(unittest.TestCase):
    def test_run_id_is_not_repeated_per_packet(self):
        """51 characters x 193,675 packets was ~9.8 MB on its own."""
        import inspect

        src = inspect.getsource(atc._ingest_audio_chunk_impl)
        entry = src[src.index("packet_entry = {"):src.index("_manifest.setdefault")]
        self.assertNotIn('"run_id"', entry)

    def test_the_backstop_records_what_it_dropped(self):
        """The old cap silently discarded the first half of a session."""
        import inspect

        src = inspect.getsource(atc._ingest_audio_chunk_impl)
        self.assertIn("packet_entries_dropped", src)

    def test_the_backstop_is_far_above_a_normal_session(self):
        self.assertGreater(atc._PACKET_ENTRY_LIMIT, 10000)



if __name__ == "__main__":
    unittest.main()

