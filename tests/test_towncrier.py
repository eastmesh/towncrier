import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from towncrier.db import Store
from towncrier.app import esc
from towncrier.engine import Scheduler, split_message

class FakeStop:
    def __init__(self): self.calls = 0
    def is_set(self): return self.calls > 0
    def wait(self, _): self.calls += 1

class FakeRadio:
    sent = []
    def __init__(self, port, baud): pass
    def send(self, channel, message): self.sent.append((channel, message))

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / "x.db")
    def tearDown(self): self.tmp.cleanup()
    def test_round_trip_validation_and_crud(self):
        self.s.save({"name":"Morning","channel":2,"message":"hello","interval":1800,"enabled":True})
        row = self.s.schedules()[0]
        self.assertEqual(row["channel"], 2)
        self.s.save({"name":"Changed","channel":3,"message":"updated","interval":3600,"enabled":False}, row["id"])
        self.assertEqual(self.s.get(row["id"])["name"], "Changed")
        self.s.delete(row["id"])
        self.assertIsNone(self.s.get(row["id"]))
        with self.assertRaises(ValueError): self.s.save({"name":"x","channel":2,"message":"x","interval":1740})
    def test_scheduler_uses_fake_radio_and_advances_after_send(self):
        self.s.save({"name":"x","channel":1,"message":"x","interval":1800})
        FakeRadio.sent = []
        with patch("towncrier.engine.Radio", FakeRadio):
            Scheduler(self.s, FakeStop()).run()
        self.assertEqual(FakeRadio.sent, [(1, "x")])
        self.assertEqual(self.s.get(1)["status"], "sent")
    def test_split_message_targets_120_and_never_exceeds_130(self):
        chunks = split_message(" ".join(["word"] * 100))
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 130 for chunk in chunks))
        self.assertEqual(" ".join(chunks), " ".join(["word"] * 100))
    def test_sent_moves_next_send(self):
        self.s.save({"name":"x","channel":1,"message":"x","interval":1800})
        row = self.s.schedules()[0]
        self.s.sent(row["id"], 100, 1800)
        self.assertEqual(self.s.get(row["id"])["next_send"], 1900)
    def test_escape(self): self.assertEqual(esc('<script>'), '&lt;script&gt;')

if __name__ == "__main__": unittest.main()
