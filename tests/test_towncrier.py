import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from towncrier.db import Store
from towncrier.app import esc
from towncrier.engine import Scheduler

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
        self.s.save({"name":"Morning","channel":2,"message":"hello","interval":60,"enabled":True})
        row = self.s.schedules()[0]
        self.assertEqual(row["channel"], 2)
        self.s.save({"name":"Changed","channel":3,"message":"updated","interval":120,"enabled":False}, row["id"])
        self.assertEqual(self.s.get(row["id"])["name"], "Changed")
        self.s.delete(row["id"])
        self.assertIsNone(self.s.get(row["id"]))
        with self.assertRaises(ValueError): self.s.save({"name":"x","channel":2,"message":"x","interval":59})
    def test_scheduler_uses_fake_radio_and_advances_after_send(self):
        self.s.save({"name":"x","channel":1,"message":"x","interval":60})
        FakeRadio.sent = []
        with patch("towncrier.engine.Radio", FakeRadio):
            Scheduler(self.s, FakeStop()).run()
        self.assertEqual(FakeRadio.sent, [(1, "x")])
        self.assertEqual(self.s.get(1)["status"], "sent")
    def test_sent_moves_next_send(self):
        self.s.save({"name":"x","channel":1,"message":"x","interval":60})
        row = self.s.schedules()[0]
        self.s.sent(row["id"], 100, 60)
        self.assertEqual(self.s.get(row["id"])["next_send"], 160)
    def test_escape(self): self.assertEqual(esc('<script>'), '&lt;script&gt;')

if __name__ == "__main__": unittest.main()
