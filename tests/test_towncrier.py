import tempfile, unittest
from pathlib import Path
from towncrier.db import Store
from towncrier.app import esc
class StoreTests(unittest.TestCase):
 def setUp(self): self.tmp=tempfile.TemporaryDirectory(); self.s=Store(Path(self.tmp.name)/"x.db")
 def tearDown(self): self.tmp.cleanup()
 def test_round_trip_and_validation(self):
  self.s.save({"name":"Morning","channel":2,"message":"hello","interval":60,"enabled":True}); r=self.s.schedules()[0]; self.assertEqual(r["channel"],2); self.assertEqual(len(self.s.due(r["next_send"])),1)
  with self.assertRaises(ValueError): self.s.save({"name":"x","channel":2,"message":"x","interval":59})
 def test_sent_moves_next_send(self):
  self.s.save({"name":"x","channel":1,"message":"x","interval":60}); r=self.s.schedules()[0]; self.s.sent(r["id"],100,60); self.assertEqual(self.s.get(r["id"])["next_send"],160)
 def test_escape(self): self.assertEqual(esc('<script>'), '&lt;script&gt;')
if __name__=="__main__": unittest.main()
