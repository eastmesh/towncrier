import threading,time
from .radio import Radio
TARGET_CHARS=120
MAX_CHUNK=130
MESSAGE_DELAY=3

def split_message(message):
 if "\n" not in message and "\r" not in message:
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
 chunks=[]; current=""
 for line in message.splitlines(keepends=True):
  while line:
   room=MAX_CHUNK-len(current)
   if len(line)<=room:
    current+=line; line=""
   else:
    if current: chunks.append(current); current=""; room=MAX_CHUNK
    chunks.append(line[:room]); line=line[room:]
 if current: chunks.append(current)
 return chunks

def send_message(radio,channel,message,delay=MESSAGE_DELAY):
 channels=[channel] if isinstance(channel,int) else channel
 messages=[message] if isinstance(message,str) else message
 for target in channels:
  for index,text in enumerate(messages):
   for chunk in split_message(text): radio.send(target,chunk)
   if index+1<len(messages): time.sleep(delay)

class Scheduler(threading.Thread):
 daemon=True
 def __init__(self,store,stop_event): super().__init__(); self.store,self.stop_event=store,stop_event
 def run(self):
  while not self.stop_event.is_set():
   rows=self.store.due()
   for row in rows:
    try:
     send_message(Radio.from_store(self.store),row["channels"],row["messages"],row["message_delay"]); self.store.sent(row["id"],time.time(),row["interval"])
    except Exception as exc: self.store.failed(row["id"],exc)
   self.stop_event.wait(1 if rows else 10)
