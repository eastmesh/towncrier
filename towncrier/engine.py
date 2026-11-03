import threading,time
from .radio import Radio
TARGET_CHARS=120
MAX_CHUNK=130

def split_message(message):
 words=message.split(); chunks=[]; current=""
 for word in words:
  while len(word)>MAX_CHUNK:
   if current: chunks.append(current); current=""
   chunks.append(word[:MAX_CHUNK]); word=word[MAX_CHUNK:]
  candidate=word if not current else current+" "+word
  if current and (len(candidate)>MAX_CHUNK or len(current)>=TARGET_CHARS):
   chunks.append(current); current=word
  else: current=candidate
 if current: chunks.append(current)
 return chunks

def send_message(radio,channel,message):
 for chunk in split_message(message): radio.send(channel,chunk)

class Scheduler(threading.Thread):
 daemon=True
 def __init__(self,store,stop_event): super().__init__(); self.store,self.stop_event=store,stop_event
 def run(self):
  while not self.stop_event.is_set():
   rows=self.store.due()
   for row in rows:
    try:
     send_message(Radio(self.store.setting("port","/dev/ttyUSB0"),self.store.setting("baud","115200")),row["channel"],row["message"]); self.store.sent(row["id"],time.time(),row["interval"])
    except Exception as exc: self.store.failed(row["id"],exc)
   self.stop_event.wait(1 if rows else 10)
