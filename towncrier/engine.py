import asyncio,threading,time
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

class BotListener(threading.Thread):
 daemon=True
 def __init__(self,store,stop_event): super().__init__(); self.store,self.stop_event=store,stop_event
 def _config(self):
  enabled=self.store.setting("bot_enabled","0")=="1"; trigger=self.store.setting("bot_trigger","").strip(); page_name=self.store.setting("bot_page_name","").strip()
  try: channel=int(self.store.setting("bot_channel","-1"))
  except ValueError: channel=-1
  return enabled,channel,trigger,page_name
 async def _run_async(self):
  from meshcore import EventType
  radio=Radio.from_store(self.store); mc=await radio._connect(); subscription=None
  enabled,channel,trigger,page_name=self._config()
  if not enabled or channel<0 or not trigger or not page_name:
   await mc.disconnect(); return
  async def on_message(event):
   payload=getattr(event,"payload",None) or {}; text=str(payload.get("text", "")); incoming_channel=payload.get("channel_idx")
   if incoming_channel!=channel or not text.lower().startswith(trigger.lower()): return
   page=next((item for item in self.store.saved_lists() if item["name"]==page_name),None)
   if not page: return
   for message in page["messages"]:
    for chunk in split_message(message):
     result=await mc.commands.send_chan_msg(channel,chunk)
     if getattr(result,"type",None)==EventType.ERROR: raise RuntimeError(result.payload)
  try:
   subscription=mc.dispatcher.subscribe(EventType.CHANNEL_MSG_RECV,on_message,attribute_filters={"channel_idx":channel})
   await mc.start_auto_message_fetching()
   while not self.stop_event.is_set(): await asyncio.sleep(1)
  finally:
   if subscription is not None:
    try: mc.unsubscribe(subscription)
    except Exception: pass
   try: await mc.stop_auto_message_fetching()
   except Exception: pass
   await mc.disconnect()
 def run(self):
  while not self.stop_event.is_set():
   enabled,channel,trigger,page_name=self._config()
   if not enabled or channel<0 or not trigger or not page_name:
    self.stop_event.wait(5); continue
   try: asyncio.run(self._run_async())
   except Exception: self.stop_event.wait(10)
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
