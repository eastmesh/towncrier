import asyncio,threading,time
from collections import deque
from .radio import Radio
TARGET_CHARS=120
MAX_CHUNK=130
MESSAGE_DELAY=3

_bot_messages=deque(maxlen=100)
_bot_messages_lock=threading.Lock()

def recent_bot_messages(channel=None):
 with _bot_messages_lock:
  rows=list(_bot_messages)
 if channel is not None: rows=[row for row in rows if row["channel"]==channel]
 return rows

def record_bot_message(channel,text,triggered=False,error=""):
 with _bot_messages_lock:
  _bot_messages.append({"time":time.time(),"channel":channel,"text":text,"triggered":triggered,"error":error})
 set_bot_status(last_message={"time":time.time(),"channel":channel,"text":text})

_bot_status={"state":"idle","channel":None,"last_message":None,"error":""}
_bot_status_lock=threading.Lock()

def bot_listener_status():
 with _bot_status_lock: return dict(_bot_status)

def set_bot_status(**values):
 with _bot_status_lock: _bot_status.update(values)

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
 def __init__(self,store,stop_event):
  super().__init__(); self.store,self.stop_event=store,stop_event; self.seen_messages=set(); self.initial_drained=False
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
   set_bot_status(state="disabled",channel=channel,error="Bot settings are incomplete or disabled")
   await mc.disconnect(); return
  set_bot_status(state="listening",channel=channel,error="")
  async def post_page():
   try:
    page=next((item for item in self.store.saved_lists() if item["name"]==page_name),None)
    if not page: return
    for message in page["messages"]:
     for chunk in split_message(message):
      result=await mc.commands.send_chan_msg(channel,chunk)
      result_type=getattr(result,"type",None)
      if result_type==EventType.ERROR: raise RuntimeError(result.payload)
      if str(result_type).lower() in {"no_event_received","no_more_msgs"}:
       set_bot_status(state="listening",channel=channel,error="send confirmation unavailable; listener remains active")
      await mc.commands.get_msg()
   except Exception as exc:
    set_bot_status(state="listening",channel=channel,error=f"post failed: {exc}")
  async def on_message(event):
   try:
    payload=getattr(event,"payload",None) or {}; text=str(payload.get("text", "")); incoming_channel=payload.get("channel_idx")
    if incoming_channel!=channel: return
    key=(payload.get("sender_timestamp"),text,incoming_channel)
    if key in self.seen_messages: return
    self.seen_messages.add(key)
    matched_text=text.split(":",1)[1].lstrip() if ":" in text else text
    matched=matched_text.lower().startswith(trigger.lower())
    record_bot_message(channel,text,matched)
    self.store.record_bot_message(channel,text,matched)
    if matched: asyncio.create_task(post_page())
   except Exception as exc:
    set_bot_status(state="listening",channel=channel,error=f"message handling failed: {exc}")
  try:
   subscription=mc.subscribe(EventType.CHANNEL_MSG_RECV,on_message)
   await mc.start_auto_message_fetching()
   await mc.commands.get_msg()
   set_bot_status(state="listening",channel=channel,error="")
   while not self.stop_event.is_set():
    await asyncio.sleep(1)
    current=self._config()
    if current!=(enabled,channel,trigger,page_name):
     break
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
   except Exception as exc:
    set_bot_status(state="error",error=str(exc)); self.stop_event.wait(10)
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
