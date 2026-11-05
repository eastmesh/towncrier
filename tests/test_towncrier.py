import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from towncrier.db import Store
from towncrier.app import esc
from towncrier.engine import Scheduler, send_message, split_message

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
        self.s.save({"name":"Morning","channel":2,"messages":["hello","world"],"interval":1800,"enabled":True})
        row = self.s.schedules()[0]
        self.assertEqual(row["channel"], 2)
        self.assertEqual(row["messages"], ["hello", "world"])
        self.s.save({"name":"Changed","channel":3,"messages":["updated"],"interval":3600,"enabled":False}, row["id"])
        self.assertEqual(self.s.get(row["id"])["name"], "Changed")
        self.s.delete(row["id"])
        self.assertIsNone(self.s.get(row["id"]))
        with self.assertRaises(ValueError): self.s.save({"name":"x","channel":2,"messages":[],"interval":1740})
    def test_legacy_single_message_is_migrated(self):
        path = Path(self.tmp.name) / "legacy.db"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE schedules(id INTEGER PRIMARY KEY,name TEXT,channel INTEGER,message TEXT,interval INTEGER,enabled INTEGER,next_send REAL,last_send REAL,last_error TEXT,status TEXT)")
            db.execute("INSERT INTO schedules VALUES(1,'legacy',2,'hello',1800,1,0,NULL,NULL,'waiting')")
        self.assertEqual(Store(path).get(1)["messages"], ["hello"])
    def test_send_message_delays_between_messages(self):
        FakeRadio.sent = []
        with patch("towncrier.engine.time.sleep") as sleep:
            send_message(FakeRadio("", ""), 1, ["first", "second"])
        self.assertEqual(FakeRadio.sent, [(1, "first"), (1, "second")])
        sleep.assert_called_once_with(3)
    def test_send_message_delays_between_messages_on_each_channel(self):
        FakeRadio.sent = []
        with patch("towncrier.engine.time.sleep") as sleep:
            send_message(FakeRadio("", ""), [1, 3], ["first", "second"])
        self.assertEqual(FakeRadio.sent, [(1, "first"), (1, "second"), (3, "first"), (3, "second")])
        self.assertEqual(sleep.call_count, 2)
    def test_save_waits_until_interval_before_first_send(self):
        self.s.save({"name":"x","channels":[1],"message":"x","interval":1800})
        self.assertEqual(self.s.due(now=0), [])
    def test_multiple_channels_round_trip(self):
        self.s.save({"name":"x","channels":[1,3],"messages":["a","b"],"interval":1800})
        self.assertEqual(self.s.schedules()[0]["channels"], [1, 3])
    def test_scheduler_uses_fake_radio_and_advances_after_send(self):
        self.s.save({"name":"x","channel":1,"message":"x","interval":1800})
        with self.s.connect() as db: db.execute("UPDATE schedules SET next_send=0")
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
    def test_split_message_preserves_newlines(self):
        message = "first line\nsecond line\nthird"
        self.assertEqual("".join(split_message(message)), message)
    def test_sent_moves_next_send(self):
        self.s.save({"name":"x","channel":1,"message":"x","interval":1800})
        row = self.s.schedules()[0]
        self.s.sent(row["id"], 100, 1800)
        self.assertEqual(self.s.get(row["id"])["next_send"], 1900)
    def test_escape(self): self.assertEqual(esc('<script>'), '&lt;script&gt;')

if __name__ == "__main__": unittest.main()
