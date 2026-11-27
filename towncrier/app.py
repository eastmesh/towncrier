import argparse, html, json, secrets, signal, threading, time
from http.cookies import SimpleCookie
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from .db import Store
from .radio import Radio, PublisherSession, PublisherSessionExpired
from .shared_radio import SharedRadio
from .engine import Scheduler, BotListener, send_message, recent_bot_messages, bot_listener_status
from . import theme
RADIO_PRESETS={"Australia":(915.800,250,10,5),"Australia (Narrow)":(916.575,62.5,7,7),"Australia (Mid)":(915.075,125,9,5),"Australia: SA, WA":(923.125,62.5,8,8),"Australia: QLD":(923.125,62.5,8,5)}
def serial_device_paths(dev=Path("/dev")):
 # Prefer persistent USB aliases without listing the same device twice.
 paths=[]; seen=set()
 for pattern in ("serial/by-id/*","serial/by-path/*","ttyACM*","ttyUSB*","tty.usb*","cu.usb*"):
  for path in sorted(dev.glob(pattern)):
   try: target=path.resolve(strict=True)
   except (OSError,RuntimeError): continue
   if target not in seen:
    seen.add(target); paths.append(str(path))
 return paths

def esc(v): return html.escape(str(v),quote=True)
def duration_parts(seconds): return int(seconds)//3600,(int(seconds)%3600)//60
def duration_label(seconds):
 h,m=duration_parts(seconds); return (f"{h}h " if h else "")+f"{m}m"
def schedule_message_row(index,message,total):
 ident=f"message-{index+1}"
 content=esc(message).replace(chr(13),'&#13;').replace(chr(10),'&#10;')
 return '<div class="message-row" draggable="true" aria-posinset="'+str(index+1)+'" aria-setsize="'+str(total)+'"><div class="message-main"><label class="message-label" for="'+ident+'">Message '+str(index+1)+'</label><textarea id="'+ident+'" form="schedule-form" required maxlength="500" name="message" oninput="updateCount(this)" data-value="'+content+'"></textarea><small class="message-count">'+str(len(message))+'/500</small></div><div class="message-controls" role="group" aria-label="Controls for message '+str(index+1)+'"><button type="button" class="message-control" data-action="add" aria-label="Add message below" title="Add message below">+</button><button type="button" class="message-control danger" data-action="remove" aria-label="Remove this message" title="Remove this message">−</button><button type="button" class="message-control" data-action="up" aria-label="Move message up" title="Move message up"'+(' disabled' if index==0 else '')+'>↑</button><button type="button" class="message-control" data-action="down" aria-label="Move message down" title="Move message down"'+(' disabled' if index==total-1 else '')+'>↓</button></div></div>'

MESSAGE_EDITOR_SCRIPT="""<script>
let newMessageId=0;
const messageList=document.getElementById("messages");
function updateCount(box){box.closest(".message-row").querySelector(".message-count").textContent=box.value.length+"/500"}
function refreshMessageControls(){const rows=[...messageList.querySelectorAll(".message-row")];rows.forEach((row,index)=>{row.setAttribute("aria-posinset",index+1);row.setAttribute("aria-setsize",rows.length);row.querySelector(".message-label").textContent="Message "+(index+1);row.querySelector(".message-controls").setAttribute("aria-label","Controls for message "+(index+1));row.querySelector('[data-action="up"]').disabled=index===0;row.querySelector('[data-action="down"]').disabled=index===rows.length-1})}
function createMessageRow(){const row=document.createElement("div");row.className="message-row";row.draggable=true;const id="new-message-"+(++newMessageId);row.innerHTML='<div class="message-main"><label class="message-label" for="'+id+'">Message</label><textarea id="'+id+'" form="schedule-form" required maxlength="500" name="message" oninput="updateCount(this)"></textarea><small class="message-count">0/500</small></div><div class="message-controls" role="group" aria-label="Message controls"><button type="button" class="message-control" data-action="add" aria-label="Add message below" title="Add message below">+</button><button type="button" class="message-control danger" data-action="remove" aria-label="Remove this message" title="Remove this message">−</button><button type="button" class="message-control" data-action="up" aria-label="Move message up" title="Move message up">↑</button><button type="button" class="message-control" data-action="down" aria-label="Move message down" title="Move message down">↓</button></div>';return row}
function addMessage(button){const row=createMessageRow(),after=button&&button.closest(".message-row");if(after)after.after(row);else messageList.append(row);refreshMessageControls();row.querySelector("textarea").focus()}
function removeMessage(button){const row=button.closest(".message-row"),rows=messageList.querySelectorAll(".message-row");if(rows.length===1){const box=row.querySelector("textarea");box.value="";updateCount(box);box.focus();return}const focusTarget=row.nextElementSibling||row.previousElementSibling;row.remove();refreshMessageControls();focusTarget.querySelector("textarea").focus()}
function moveMessage(button,direction){const row=button.closest(".message-row"),adjacent=direction<0?row.previousElementSibling:row.nextElementSibling;if(!adjacent)return;if(direction<0)messageList.insertBefore(row,adjacent);else messageList.insertBefore(adjacent,row);refreshMessageControls();row.querySelector("textarea").focus()}
messageList.addEventListener("click",event=>{const button=event.target.closest("button[data-action]");if(!button)return;switch(button.dataset.action){case"add":addMessage(button);break;case"remove":removeMessage(button);break;case"up":moveMessage(button,-1);break;case"down":moveMessage(button,1);break}});
let draggedMessage=null;messageList.addEventListener("dragstart",event=>{draggedMessage=event.target.closest(".message-row")});messageList.addEventListener("dragover",event=>{event.preventDefault();const row=event.target.closest(".message-row");if(!row||!draggedMessage||row===draggedMessage)return;const before=event.clientY<row.getBoundingClientRect().top+row.offsetHeight/2;messageList.insertBefore(draggedMessage,before?row:row.nextSibling);refreshMessageControls()});messageList.addEventListener("dragend",()=>{draggedMessage=null;refreshMessageControls()});
document.querySelectorAll("#messages textarea").forEach(box=>{box.value=box.dataset.value;updateCount(box)});refreshMessageControls();
document.getElementById("schedule-form").addEventListener("submit",async function(event){event.preventDefault();const status=document.getElementById("save-status");status.textContent="Saving…";const response=await fetch(this.action,{method:"POST",body:new URLSearchParams(new FormData(this)),headers:{"X-Requested-With":"XMLHttpRequest"}});if(!response.ok){status.textContent="Save failed: "+await response.text();return}const page=new DOMParser().parseFromString(await response.text(),"text/html"),updatedList=page.getElementById("schedule-page");if(updatedList)document.getElementById("schedule-page").replaceWith(updatedList);status.textContent="Saved";window.towncrierFlashSaved&&window.towncrierFlashSaved(document.querySelector('[data-save-id="schedule"]'))});
</script>"""
class Handler(BaseHTTPRequestHandler):
 publisher_sessions={}
 publisher_sessions_lock=threading.Lock()
 radio_snapshot_lock=threading.Lock()
 radio_snapshot_cache=None
 radio_snapshot_store_path=None
 radio_snapshot_cached_at=0.0
 radio_snapshot_ttl=15.0
 radio_snapshot_job=None
 radio_snapshot_generation=0
 def cached_radio_snapshot(self):
  cls=type(self); store=self.store; store_path=store.path
  with cls.radio_snapshot_lock:
   cached=cls.radio_snapshot_cache if cls.radio_snapshot_store_path==store_path else None
   if cached is not None and time.monotonic()-cls.radio_snapshot_cached_at<cls.radio_snapshot_ttl: return cached
   if cls.radio_snapshot_job is None:
    ready=threading.Event(); result=[]; generation=cls.radio_snapshot_generation
    cls.radio_snapshot_job=(ready,result,store_path,generation)
    def refresh():
     try: outcome=Radio.from_store(store).snapshot()
     except Exception as exc: outcome=exc
     with cls.radio_snapshot_lock:
      if generation==cls.radio_snapshot_generation and not isinstance(outcome,Exception):
       cls.radio_snapshot_cache=outcome; cls.radio_snapshot_store_path=store_path; cls.radio_snapshot_cached_at=time.monotonic()
      result.append(outcome); cls.radio_snapshot_job=None; ready.set()
    threading.Thread(target=refresh,daemon=True,name="radio-snapshot").start()
   ready,result,job_path,generation=cls.radio_snapshot_job
  # Use the previous channel list immediately; a cold page only waits briefly.
  if cached is not None: return cached
  if job_path!=store_path or not ready.wait(.6): raise RuntimeError("Radio channel refresh in progress. Reload in a moment.")
  with cls.radio_snapshot_lock:
   if generation!=cls.radio_snapshot_generation: raise RuntimeError("Radio settings changed during refresh. Reload in a moment.")
  if isinstance(result[0],Exception): raise result[0]
  return result[0]
 @classmethod
 def invalidate_radio_snapshot(cls):
  with cls.radio_snapshot_lock:
   cls.radio_snapshot_generation+=1
   cls.radio_snapshot_cache=None; cls.radio_snapshot_store_path=None; cls.radio_snapshot_cached_at=0.0
 def do_GET(self):
  path=urlparse(self.path).path
  if path=="/": return self.publisher_page()
  if path=="/schedules": return self.schedules_page()
  if path=="/radio": return self.radio_page()
  if path=="/bot": return self.bot_page()
  if path=="/publish": return self.publish_page()
  if path=="/publisher": return self.publisher_page()
  if path=="/publisher-api/schedule":
   query=parse_qs(urlparse(self.path).query)
   try:
    row=self.store.get(int(query.get("id",[""])[0])) if query.get("id") else None
    if query.get("id") and row is None: return self.respond(json.dumps({"error":"schedule not found"}),"application/json",404)
    return self.respond(json.dumps(row),"application/json")
   except (TypeError,ValueError) as exc: return self.respond(json.dumps({"error":str(exc)}),"application/json",400)
  if path=="/publisher-api/channels":
   try:
    _,channels=self.cached_radio_snapshot()
    return self.respond(json.dumps({"channels":channels}),"application/json")
   except Exception as exc: return self.respond(json.dumps({"error":str(exc)}),"application/json",502)
  if path=="/publisher-api/bot-status": return self.respond(json.dumps(bot_listener_status()),"application/json")
  if path=="/publisher-api/bot-messages":
   query=parse_qs(urlparse(self.path).query)
   try: channel=int(query.get("channel",[self.store.setting("bot_channel","-1")])[0])
   except ValueError: channel=-1
   return self.respond(json.dumps({"messages":self.store.bot_messages(channel,limit=10)}),"application/json")
  if path=="/publisher-api/contacts":
   try:
    rooms=Radio.from_store(self.store).publisher_contacts()
    return self.respond(json.dumps({"rooms":rooms}),"application/json")
   except Exception as exc:
    return self.respond(json.dumps({"error":str(exc)}),"application/json",502)
  if path=="/publisher-api/lists": return self.respond(json.dumps({"lists":self.store.saved_lists()}),"application/json")
  if path in {"/publisher.css","/publisher.bundle.js","/publish.bundle.js","/theme.css","/theme.js"} or path.startswith("/assets/"): return self.static_file(path)
  if path=="/health": return self.respond("OK","text/plain")
  self.send_error(404)
 def do_DELETE(self):
  path=urlparse(self.path).path; body=self.rfile.read(int(self.headers.get("Content-Length",0)))
  if path=="/publisher-api/lists":
   if not self.headers.get("Content-Type","").lower().startswith("application/json"): return self.respond(json.dumps({"error":"Content-Type must be application/json"}),"application/json",415)
   try:
    payload=json.loads(body.decode("utf-8"))
    if not isinstance(payload,dict): raise ValueError("request body must be a JSON object")
    name=str(payload.get("name","")).strip()
    if not name: raise ValueError("page name is required")
    if not any(item["name"]==name for item in self.store.saved_lists()): return self.respond(json.dumps({"error":"saved page not found"}),"application/json",404)
    self.store.delete_list(name)
    return self.respond(json.dumps({"lists":self.store.saved_lists()}),"application/json")
   except (ValueError,TypeError,json.JSONDecodeError) as exc: return self.respond(json.dumps({"error":str(exc)}),"application/json",400)
  self.send_error(404)
 def do_POST(self):
  path=urlparse(self.path).path; body=self.rfile.read(int(self.headers.get("Content-Length",0)))
  if path=="/publisher-api/schedule":
   if not self.headers.get("Content-Type","").lower().startswith("application/json"):
    return self.respond(json.dumps({"error":"Content-Type must be application/json"}),"application/json",415)
   try:
    payload=json.loads(body.decode("utf-8"))
    if not isinstance(payload,dict): raise ValueError("request body must be a JSON object")
    ident=self.store.save(payload,payload.get("id") or None)
    return self.respond(json.dumps({"id":ident}),"application/json")
   except (ValueError,TypeError,KeyError) as exc:
    return self.respond(json.dumps({"error":str(exc)}),"application/json",400)
  if path=="/publisher-api/lists":
   if not self.headers.get("Content-Type","").lower().startswith("application/json"): return self.respond(json.dumps({"error":"Content-Type must be application/json"}),"application/json",415)
   try:
    payload=json.loads(body.decode("utf-8")); self.store.save_list(payload.get("name",""),payload.get("messages",[])); return self.respond(json.dumps({"lists":self.store.saved_lists()}),"application/json")
   except (ValueError,TypeError,KeyError) as exc: return self.respond(json.dumps({"error":str(exc)}),"application/json",400)
  if path in {"/publisher-api/login","/publisher-api/publish"}:
   if not self.headers.get("Content-Type","").lower().startswith("application/json"):
    return self.respond(json.dumps({"error":"Content-Type must be application/json"}),"application/json",415)
   try:
    payload=json.loads(body.decode("utf-8"))
    if not isinstance(payload,dict): raise ValueError("request body must be a JSON object")
    if path.endswith("/login"):
     radio=Radio.from_store(self.store)
     key=str(payload.get("public_key","" )).strip().lower()
     room_session=PublisherSession(radio,key,payload.get("password",""))
     result=room_session.result
     token=secrets.token_urlsafe(32)
     retired=[]
     with self.publisher_sessions_lock:
      now=time.monotonic()
      previous=SimpleCookie(); previous.load(self.headers.get("Cookie",""))
      previous_token=previous.get("towncrier_publisher")
      for stale,session in list(self.publisher_sessions.items()):
       if session[2]<=now or (previous_token and stale==previous_token.value and session[0]==self.client_address[0]):
        retired.append(self.publisher_sessions.pop(stale)[3])
      self.publisher_sessions[token]=(self.client_address[0],key,now+28800,room_session)
     for session in retired:
      session.close()
     return self.respond(json.dumps(result),"application/json",headers={"Set-Cookie":f"towncrier_publisher={token}; Path=/publisher-api; Max-Age=28800; HttpOnly; SameSite=Strict"})
    cookie=SimpleCookie(); cookie.load(self.headers.get("Cookie","")); morsel=cookie.get("towncrier_publisher")
    token=morsel.value if morsel else ""
    key=str(payload.get("public_key","" )).strip().lower()
    with self.publisher_sessions_lock:
     session=self.publisher_sessions.get(token)
     if not session:
      return self.respond(json.dumps({"error":"Log in to this room server again before publishing."}),"application/json",401)
     if session[2]<=time.monotonic():
      self.publisher_sessions.pop(token,None)
      expired=True
     else: expired=False
     if session[0]!=self.client_address[0] or session[1]!=key:
      return self.respond(json.dumps({"error":"Publisher session is not authorized for this room server."}),"application/json",401)
     room_session=session[3]
    if expired:
     room_session.close()
     return self.respond(json.dumps({"error":"Publisher session expired; log in again."}),"application/json",401)
    result=room_session.send(payload.get("text",""))
    return self.respond(json.dumps(result),"application/json")
   except PublisherSessionExpired as exc:
    with self.publisher_sessions_lock: self.publisher_sessions.pop(token,None)
    return self.respond(json.dumps({"error":str(exc)}),"application/json",401)
   except (ValueError,UnicodeDecodeError) as exc:
    return self.respond(json.dumps({"error":str(exc)}),"application/json",400)
   except Exception as exc:
    return self.respond(json.dumps({"error":str(exc)}),"application/json",502)
  parsed=parse_qs(body.decode(),keep_blank_values=True); form={k:v[0] for k,v in parsed.items()}; ajax=self.headers.get("X-Requested-With")=="XMLHttpRequest"
  if path=="/bot":
   # Legacy single-entry settings form; kept for old clients. New installs use /bot-entries.
   try: message_delay=int(form.get("message_delay","3"))
   except ValueError: return self.respond("Bot message delay must be a whole number of seconds.","text/plain",400)
   if not 0<=message_delay<=3600: return self.respond("Bot message delay must be between 0 and 3600 seconds.","text/plain",400)
   self.store.set_setting("bot_channel",form.get("channel","")); self.store.set_setting("bot_trigger",form.get("trigger","")); self.store.set_setting("bot_page_name",form.get("page_name","")); self.store.set_setting("bot_message_delay",message_delay); self.store.set_setting("bot_enabled","1" if form.get("enabled")=="on" else "0")
   self.send_response(303); self.send_header("Location","/bot?saved=bot"); self.end_headers(); return
  try:
   if path=="/settings":
    connection_type=form.get("connection_type","serial").strip().lower()
    if connection_type not in {"serial","tcp"}: raise ValueError("connection type must be serial or tcp")
    serial_port=self.store.setting("port","/dev/ttyUSB0")
    baud=self.store.setting("baud","115200")
    tcp_host=self.store.setting("tcp_host","")
    tcp_port=self.store.setting("tcp_port","4000")
    if connection_type=="serial":
     serial_port=form.get("port",serial_port).strip()
     try: baud=str(int(form.get("baud",baud)))
     except (TypeError,ValueError) as exc: raise ValueError("baud rate must be a positive integer") from exc
     if not serial_port or int(baud)<=0: raise ValueError("select a serial device and a positive baud rate")
    else:
     tcp_host=form.get("tcp_host",tcp_host).strip()
     try: tcp_port=str(int(form.get("tcp_port",tcp_port)))
     except (TypeError,ValueError) as exc: raise ValueError("TCP port must be between 1 and 65535") from exc
     if not tcp_host or len(tcp_host)>253 or any(char.isspace() for char in tcp_host) or any(char in tcp_host for char in "/?#@"):
      raise ValueError("enter a TCP companion hostname or IP address")
     if not 1<=int(tcp_port)<=65535: raise ValueError("TCP port must be between 1 and 65535")
    self.store.set_settings(connection_type=connection_type,port=serial_port,baud=baud,tcp_host=tcp_host,tcp_port=tcp_port)
   elif path=="/radio-settings":
    preset=form.get("preset","")
    if preset not in RADIO_PRESETS: raise ValueError("choose a valid Australian MeshCore preset")
    Radio.from_store(self.store).set_radio(*RADIO_PRESETS[preset])
   elif path=="/channel-save":
    index=int(form.get("channel_index","-1")); name=form.get("channel_name","").strip(); secret_hex=form.get("channel_secret","").strip()
    if not 0<=index<=255 or not 1<=len(name.encode())<=32: raise ValueError("channel index must be 0-255 and name must be 1-32 bytes")
    if secret_hex:
     if len(secret_hex)!=32: raise ValueError("private channel secret must be 32 hexadecimal characters")
     secret=bytes.fromhex(secret_hex)
    elif name.startswith("#"): secret=None
    else: raise ValueError("use a #name for a public topic channel or provide a 32-character private secret")
    Radio.from_store(self.store).set_channel(index,name,secret)
   elif path=="/channel-delete":
    index=int(form.get("channel_index","-1"))
    if not 0<=index<=255: raise ValueError("channel index must be 0-255")
    Radio.from_store(self.store).delete_channel(index)
   elif path=="/save":
    post_times=parsed.get("post_time",[])
    selected_page=form.get("page_name","")
    messages=next((item["messages"] for item in self.store.saved_lists() if item["name"]==selected_page),parsed.get("message",[])) if selected_page else parsed.get("message",[])
    self.store.save({"name":form.get("name",""),"channels":parsed.get("channel",[]),"messages":messages,"page_name":selected_page,"interval":int(form.get("interval","1800")),"message_delay":int(form.get("message_delay","3")),"send_limit":int(form.get("send_limit","0") or 0),"post_times":post_times,"enabled":form.get("enabled")=="on"},form.get("id") or None)
   elif path=="/delete": self.store.delete(form["id"])
   elif path=="/toggle": self.store.toggle(form["id"])
   elif path=="/send":
    row=self.store.get(form["id"]);
    if not row: raise ValueError("schedule not found")
    send_message(Radio.from_store(self.store),row["channels"],self.store.resolve_messages(row),row["message_delay"])
   elif path=="/bot-entries":
    try: message_delay=int(form.get("message_delay","3"))
    except (TypeError,ValueError): raise ValueError("message delay must be a whole number of seconds")
    self.store.save_bot_entry({"channel":form.get("channel","-1"),"trigger":form.get("trigger",""),"page_name":form.get("page_name",""),"message_delay":message_delay,"enabled":form.get("enabled")=="on"},form.get("id") or None)
   elif path=="/bot-entries/toggle": self.store.toggle_bot_entry(form["id"])
   elif path=="/bot-entries/delete": self.store.delete_bot_entry(form["id"])
   else: return self.send_error(404)
   if path in {"/settings","/radio-settings","/channel-save","/channel-delete"}: self.invalidate_radio_snapshot()
   if path=="/save" and ajax: return self.page()
   destination="/radio" if path in {"/settings","/radio-settings","/channel-save","/channel-delete"} else "/bot" if path in {"/bot-entries","/bot-entries/toggle","/bot-entries/delete"} else "/schedules" if path in {"/save","/send","/toggle"} else "/"
   saved_marker={"/save":"schedule","/settings":"connection","/radio-settings":"radio-preset","/channel-save":"channel","/bot-entries":"bot-entry"}.get(path)
   if saved_marker: destination+="?saved="+saved_marker
   self.send_response(303); self.send_header("Location",destination); self.end_headers()
  except Exception as exc:
   if path=="/save" and ajax: return self.respond(str(exc),"text/plain",400)
   if path in {"/settings","/radio-settings","/channel-save","/channel-delete"}: return self.radio_page(str(exc))
   if path in {"/bot-entries","/bot-entries/toggle","/bot-entries/delete"}: return self.bot_page(str(exc))
   return self.page(str(exc))
 def schedules_page(self):
  return self.page()
 def page(self,error=""):
  rows=self.store.schedules(); q=parse_qs(urlparse(self.path).query); edit=self.store.get(q["edit"][0]) if q.get("edit") else None; e=edit or {"id":"","name":"","channel":0,"channels":[0],"messages":[""],"interval":3600,"message_delay":3,"send_limit":0,"send_count":0,"send_at":"","enabled":1}; hours,minutes=duration_parts(e["interval"])
  channels=[]; channel_error=""; radio_settings={}; radio_error=""
  try:

   radio_settings,channels=self.cached_radio_snapshot()
   channels=[channel for channel in channels if str(channel.get("name") or "").strip() not in {"","-"}]
  except Exception as exc: radio_error=channel_error=str(exc)
  channel_options=channels
  channel_names={channel["index"]:str(channel["name"]).strip() for channel in channels}
  channel_picker=''.join('<label><input form="schedule-form" type="checkbox" name="channel" value="'+str(channel["index"])+'"'+(' checked' if channel["index"] in e["channels"] else '')+'> '+str(channel["index"])+ ' · '+esc(channel["name"])+'</label>' for channel in channel_options) or '<p class="muted">No named channels are available. Add a named channel in Radio Settings first.</p>'
  radio_dot='connected' if not radio_error else 'disconnected'
  out='<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>MeshPage Publisher · Scheduler</title><link rel="stylesheet" href="/publisher.css"><link rel="stylesheet" href="/theme.css"></head><body><main class="shell towncrier-scheduler"><header class="masthead towncrier-header"><a class="brand-logo-link" href="https://eastmesh.au" target="_blank" rel="noopener"><img class="brand-logo" src="/assets/logo.png" alt="EastMesh"></a><div class="brand-title"><a class="brand-link" href="https://eastmesh.au" target="_blank" rel="noopener">eastmesh.au</a><h1>MeshPage Publisher</h1></div><span class="radio-dot '+radio_dot+'" title="Radio '+radio_dot+'" aria-label="Radio '+radio_dot+'"></span><button id="themeToggle" class="theme-toggle" type="button" aria-label="Switch to light mode">☀ Light mode</button></header><nav class="towncrier-tabs" aria-label="MeshPage Publisher sections"><a href="/">Composer</a><a href="/publish">Rooms</a><a class="active" href="/schedules">Schedules</a><a href="/bot">Bot</a><a href="/radio">Radio</a></nav>'
  if error: out+='<p class="error-message" role="alert">'+esc(error)+'</p>'
  schedule_items='<section id="schedule-page" class="schedule-list-heading"><p class="eyebrow">SAVED SCHEDULES</p><h2>Load a schedule</h2></section>'
  if not rows: schedule_items+='<section class="card panel saved-schedule-empty"><p class="muted">No saved schedules yet. Saved schedules will appear here.</p></section>'
  for r in rows:
   next_send=datetime.fromtimestamp(r["next_send"]).astimezone().strftime("%b %-d, %Y at %H:%M %Z") if r["enabled"] else "Paused"
   schedule_items+='<article class="card panel saved-schedule"><div class="saved-schedule-heading"><strong>'+esc(r["name"])+'</strong><span class="'+('toggle-enabled' if r["enabled"] else 'toggle-disabled')+'">'+('enabled' if r["enabled"] else 'disabled')+'</span></div><p class="muted">Channels: '+esc(', '.join(channel_names.get(channel,f"Channel {channel}") for channel in r["channels"]))+' · '+esc(', '.join(r["post_times"]) or duration_label(r["interval"]))+'</p><small>Next send: '+html.escape(next_send)+' · '+(str(r.get("send_count",0))+('/'+str(r["send_limit"]) if r.get("send_limit") else ' sent') if r.get("send_count") else ('0/'+str(r["send_limit"]) if r.get("send_limit") else 'Unlimited'))+'</small> '+esc(r["last_error"] or '')+'<div class="saved-schedule-actions"><a class="edit-button" href="/schedules?edit='+str(r["id"])+ '">Load</a><form class="inline" method="post" action="/toggle"><input type="hidden" name="id" value="'+str(r["id"])+ '"><button class="'+('toggle-enabled' if r["enabled"] else 'toggle-disabled')+'">'+('Pause' if r["enabled"] else 'Enable')+'</button></form><form class="inline" method="post" action="/send"><input type="hidden" name="id" value="'+str(r["id"])+ '"><button>Send now</button></form><form class="inline" method="post" action="/delete"><input type="hidden" name="id" value="'+str(r["id"])+ '"><button class="danger">Delete</button></form></div></article>'
  send_time=e.get("post_times") or ([e["send_at"]] if e.get("send_at") else ["09:00"])
  post_time_fields=''.join('<div class="post-time-row"><label>Daily post time (local) <input form="schedule-form" required type="time" name="post_time" value="'+esc(value)+'"></label>'+('' if index==0 else '<button type="button" class="danger remove-post-time" data-action="remove-post-time">Remove</button>')+'</div>' for index,value in enumerate(send_time))
  out+='<form id="schedule-form" method="post" action="/save"></form><div class="towncrier-workspace schedule-stack"><aside class="schedule-settings-rail"><section class="card panel schedule-setup-card"><p class="eyebrow">SCHEDULE SETUP</p><h2>'+('Edit' if e["id"] else 'Create a schedule')+'</h2><input type="hidden" form="schedule-form" name="id" value="'+esc(e["id"])+ '"><label>Name <input form="schedule-form" required name="name" value="'+esc(e["name"])+ '"></label><label>Channels <details class="channel-picker"><summary>Choose named channels</summary><div class="channel-picker-options">'+channel_picker+'</div></details></label><fieldset><legend>Daily post times</legend><div id="daily-post-times">'+post_time_fields+'</div><button type="button" id="add-daily-post-time">Add another time</button></fieldset><label>Delay between messages (seconds) <input form="schedule-form" required type="number" min="0" max="3600" name="message_delay" value="'+str(e["message_delay"])+ '"></label><label>Send this schedule <input form="schedule-form" required type="number" min="0" max="1000000" name="send_limit" value="'+str(e.get("send_limit",0))+ '"><small>0 means repeat forever. It disables automatically after this many successful sends.</small></label><small>Times use this computer’s local timezone. MeshPage Publisher posts once at each selected time every day. Editing retains the current pending post time.</small></section></aside><section class="card panel schedule-page-source"><p class="eyebrow">MESSAGE PAGE</p><h2>Choose a saved page</h2><p class="muted">Schedules publish the messages from a saved Composer page.</p>'+('<p class="error-message" role="alert">This schedule is a legacy snapshot not linked to any saved page. Choose one below to link it.</p>' if e["id"] and not e.get("page_name") else '')+'<label>Saved page<select form="schedule-form" required name="page_name"><option value="">Choose a saved page…</option>'+''.join('<option value="'+esc(item["name"])+('" selected' if item["name"]==e.get("page_name") else '"')+'>'+esc(item["name"])+" ("+str(len(item["messages"]))+" messages)</option>" for item in self.store.saved_lists())+'</select></label></section><aside class="schedule-settings-rail schedule-actions-rail"><section class="card panel schedule-actions-card"><p class="eyebrow">SAVE SCHEDULE</p>'+('<p class="muted">Saving keeps this schedule '+('active' if e["enabled"] else 'paused')+'.</p>' if e["id"] else '<label class="enabled-field"><input form="schedule-form" type="checkbox" name="enabled" '+('checked' if e["enabled"] else '')+ '> Schedule active</label>')+'<button class="primary" type="submit" form="schedule-form" data-save-id="schedule">Save schedule</button><span id="save-status" class="muted" aria-live="polite"></span></section>'+schedule_items+'</aside></div>'
  self.respond(out+'<script>const postTimes=document.getElementById("daily-post-times");function makePostTime(value=""){const row=document.createElement("div");row.className="post-time-row";row.innerHTML=`<label>Daily post time (local) <input form="schedule-form" required type="time" name="post_time" value="${value}"></label><button type="button" class="danger remove-post-time" data-action="remove-post-time">Remove</button>`;return row}function refreshPostTimeControls(){[...postTimes.children].forEach((row,index)=>{const remove=row.querySelector("[data-action=remove-post-time]");if(index===0)remove?.remove();else if(!remove)row.append(makePostTime().querySelector("button"))})}document.getElementById("add-daily-post-time").onclick=()=>{postTimes.append(makePostTime());refreshPostTimeControls()};postTimes.addEventListener("click",event=>{if(event.target.closest("[data-action=remove-post-time]")&&postTimes.children.length>1){event.target.closest(".post-time-row").remove();refreshPostTimeControls()}});refreshPostTimeControls()</script><script defer src="/theme.js"></script></main></body></html>')
 def bot_page(self,error=""):
  # Reuse the shared snapshot so refreshing this page does not reopen the radio.
  try: _,channels=self.cached_radio_snapshot()
  except Exception: channels=[]
  channel_names={row["index"]:str(row.get("name") or "").strip() for row in channels}
  def channel_label(idx):
   name=channel_names.get(idx); return (str(idx)+" · "+esc(name)) if name else "Channel "+str(idx)
  def channel_select_options(selected):
   return "".join('<option value="'+str(row["index"])+('" selected' if str(row["index"])==str(selected) else '"')+'>'+str(row["index"])+" · "+esc(row.get("name") or ("Channel "+str(row["index"])))+'</option>' for row in channels if row.get("configured"))
  pages=self.store.saved_lists()
  entries_for_form=self.store.bot_entries(); real_rows=self.store.real_bot_entries(); active_rows=[row for row in real_rows if row["enabled"]] or [row for row in entries_for_form if row["enabled"]]
  q=parse_qs(urlparse(self.path).query); edit_id=q.get("edit",[None])[0]
  if edit_id is not None: e=next((item for item in entries_for_form if str(item["id"])==edit_id),None)
  elif entries_for_form and entries_for_form[0]["id"]==0: e=entries_for_form[0]
  else: e=None
  e=e or {"id":"","channel":"","trigger":"","page_name":"","message_delay":3,"enabled":True}
  editing_real=bool(e.get("id"))
  page_options="".join('<option value="'+esc(item["name"])+('" selected' if item["name"]==e.get("page_name","") else '"')+'>'+esc(item["name"])+" ("+str(len(item["messages"]))+" messages)</option>" for item in pages)
  entry_channel_options=channel_select_options(e.get("channel",""))
  history_default=active_rows[0]["channel"] if active_rows else ""
  history_channel_options=channel_select_options(history_default)
  enabled_checked=" checked" if e.get("enabled",True) else ""
  submit_label="Save changes" if editing_real else "Add reply entry"
  heading="Edit reply entry" if editing_real else "Add a reply entry"
  status_or_enabled_field=('<p class="muted">Saving keeps this entry '+("active" if e.get("enabled") else "paused")+'. Use Pause/Resume below to change that.</p>') if editing_real else ('<label class="enabled-field"><input type="checkbox" name="enabled"'+enabled_checked+'> Active</label>')
  def entry_actions(row):
   return '<div class="saved-schedule-actions"><a class="edit-button" href="/bot?edit='+str(row["id"])+'">Edit</a><form class="inline" method="post" action="/bot-entries/toggle"><input type="hidden" name="id" value="'+str(row["id"])+'"><button class="'+('toggle-enabled' if row["enabled"] else 'toggle-disabled')+'">'+('Pause' if row["enabled"] else 'Resume')+'</button></form><form class="inline" method="post" action="/bot-entries/delete"><input type="hidden" name="id" value="'+str(row["id"])+'"><button class="danger">Delete</button></form></div>'
  entry_table_rows="".join('<tr><td>'+channel_label(row["channel"])+'</td><td>'+esc(row["trigger"])+'</td><td>'+esc(row["page_name"])+'</td><td class="'+('toggle-enabled' if row["enabled"] else 'toggle-disabled')+'">'+('Active' if row["enabled"] else 'Paused')+'</td><td>'+entry_actions(row)+'</td></tr>' for row in real_rows) or '<tr><td class="muted" colspan="5">No reply entries yet.</td></tr>'
  entries_table='<section id="bot-entries-table" class="card panel"><p class="eyebrow">REPLY ENTRIES</p><h2>All reply entries</h2><div class="table-scroll"><table><thead><tr><th>Channel</th><th>Trigger</th><th>Page</th><th>State</th><th>Actions</th></tr></thead><tbody>'+entry_table_rows+'</tbody></table></div></section>'
  bot_script="""<script>
const botMessages=document.getElementById("bot-messages"),botChannel=document.getElementById("bot-channel"),botStatus=document.getElementById("bot-status");
async function refreshBotStatus(){try{const status=await (await fetch("/publisher-api/bot-status")).json();const dot=document.getElementById("bot-radio-dot"); const listening=status.state==="listening"; dot.classList.toggle("connected",listening); dot.classList.toggle("disconnected",!listening); dot.title=`Bot radio: ${status.state}`; dot.setAttribute("aria-label",dot.title); const channelList=(status.channels&&status.channels.length)?status.channels.join(", "):"-"; botStatus.textContent=`${status.state} · channels ${channelList}${status.error?" · "+status.error:""}`}catch(error){botStatus.textContent="Listener status unavailable"}}
const replyLabels={pending:"Reply pending",sent:"Reply accepted by radio",unconfirmed:"Reply unconfirmed",failed:"Reply failed",interrupted:"Reply interrupted"};
let botMessageRequest=0;
async function refreshBotMessages(){
 if(!botChannel||!botMessages)return;
 const request=++botMessageRequest;
 try{
  const response=await fetch("/publisher-api/bot-messages?channel="+encodeURIComponent(botChannel.value));
  if(!response.ok)throw new Error("Message history unavailable");
  const data=await response.json();
  if(request!==botMessageRequest)return;
  // Render radio messages and error details as text, never as markup.
  const rows=data.messages.slice().reverse().map(item=>{
   const row=document.createElement("article"),meta=document.createElement("small"),text=document.createElement("div");
   row.className="card";
   const reply=item.triggered?` · trigger matched · ${replyLabels[item.reply_status]||"Reply outcome not recorded"}`:"";
   meta.textContent=`${new Date(item.time*1000).toLocaleTimeString()} · Channel ${item.channel}${reply}`;
   text.textContent=item.text;
   row.append(meta,text);
   if(item.error){const detail=document.createElement("small");detail.textContent=item.error;row.append(detail);}
   return row;
  });
  if(rows.length)botMessages.replaceChildren(...rows);
  else botMessages.innerHTML='<p class="muted">No messages seen yet on this channel.</p>';
 }catch(error){if(request===botMessageRequest)botMessages.innerHTML='<p class="muted">Could not load message history.</p>';}
}
refreshBotStatus();refreshBotMessages();setInterval(refreshBotStatus,3000);setInterval(refreshBotMessages,3000);if(botChannel)botChannel.addEventListener("change",refreshBotMessages);
</script>"""
  out='<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>MeshPage Publisher · Bot</title><link rel="stylesheet" href="/publisher.css"><link rel="stylesheet" href="/theme.css"></head><body><main class="shell towncrier-scheduler"><header class="masthead towncrier-header"><a class="brand-logo-link" href="https://eastmesh.au" target="_blank" rel="noopener"><img class="brand-logo" src="/assets/logo.png" alt="EastMesh"></a><div class="brand-title"><a class="brand-link" href="https://eastmesh.au" target="_blank" rel="noopener">eastmesh.au</a><h1>MeshPage Publisher</h1></div><span id="bot-radio-dot" class="radio-dot disconnected" title="Bot radio status unknown" aria-label="Bot radio status unknown"></span><button id="themeToggle" class="theme-toggle" type="button" aria-label="Switch to light mode">☀ Light mode</button></header><nav class="towncrier-tabs" aria-label="MeshPage Publisher sections"><a href="/">Composer</a><a href="/publish">Rooms</a><a href="/schedules">Schedules</a><a class="active" href="/bot">Bot</a><a href="/radio">Radio</a></nav>'
  if error: out+='<p class="error-message" role="alert">'+esc(error)+'</p>'
  out+='<section class="card"><h2>'+heading+'</h2><p class="muted">When the entire message matches the trigger on a channel, MeshPage Publisher posts the selected saved page to that channel. Add one entry per channel/trigger combination.</p><form method="post" action="/bot-entries"><input type="hidden" name="id" value="'+esc(e.get("id") or "")+'"><label>Channel<select required name="channel">'+entry_channel_options+'</select></label><label>Trigger word<input required name="trigger" maxlength="32" value="'+esc(e.get("trigger",""))+'"></label><label>Saved page<select required name="page_name"><option value="">Choose a saved page…</option>'+page_options+'</select></label><label>Delay between reply messages (seconds)<input required name="message_delay" type="number" min="0" max="3600" value="'+str(e.get("message_delay",3))+'"></label>'+status_or_enabled_field+'<button class="primary" data-save-id="bot-entry">'+submit_label+'</button>'+(' <a href="/bot">Cancel</a>' if editing_real else '')+'</form></section>'+entries_table+'<section class="card"><h2>Listener status</h2><p id="bot-status" class="muted">Checking listener…</p></section><section class="card"><h2>Messages seen</h2><p class="muted">Recent messages received by the bot listener on the selected channel. Trigger matches are marked.</p><label>Channel<select id="bot-channel" name="history_channel">'+history_channel_options+'</select></label><div id="bot-messages"><p class="muted">Waiting for messages…</p></div></section></main>'+bot_script+'<script defer src="/theme.js"></script></body></html>'
  return self.respond(out)
 def radio_page(self,error=""):
  radio_settings={}; channels=[]; all_channels=[]
  try:

   radio_settings,all_channels=self.cached_radio_snapshot()
   channels=[channel for channel in all_channels if str(channel.get("name") or "").strip() not in {"","-"}]
  except Exception as exc:
   error=error or str(exc)
  slots=all_channels or [{"index":index,"name":"","configured":False} for index in range(5)]
  connection_type=self.store.setting("connection_type","serial")
  serial_hidden=" hidden" if connection_type=="tcp" else ""
  tcp_hidden=" hidden" if connection_type!="tcp" else ""
  out='<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>MeshPage Publisher · Radio Settings</title><link rel="stylesheet" href="/publisher.css"><link rel="stylesheet" href="/theme.css"></head><body><main class="shell towncrier-scheduler"><header class="masthead towncrier-header"><a class="brand-logo-link" href="https://eastmesh.au" target="_blank" rel="noopener"><img class="brand-logo" src="/assets/logo.png" alt="EastMesh"></a><div class="brand-title"><a class="brand-link" href="https://eastmesh.au" target="_blank" rel="noopener">eastmesh.au</a><h1>MeshPage Publisher</h1></div><span class="radio-dot connected" title="Radio connected" aria-label="Radio connected"></span><button id="themeToggle" class="theme-toggle" type="button" aria-label="Switch to light mode">☀ Light mode</button></header><nav class="towncrier-tabs" aria-label="MeshPage Publisher sections"><a href="/">Composer</a><a href="/publish">Rooms</a><a href="/schedules">Schedules</a><a href="/bot">Bot</a><a class="active" href="/radio">Radio</a></nav><p class="muted">Radio configuration is shared by the scheduler and MeshPage Publisher.</p>'
  if error: out+='<p class="error-message" role="alert">Radio status: '+esc(error)+'</p>'
  out+='<section class="card"><p class="eyebrow">RADIO PRESET</p><h2>Regional radio settings</h2><p class="muted">Select an Australian MeshCore preset to configure the radio frequency and modulation.</p><form method="post" action="/radio-settings"><label>Preset<select required name="preset">'+''.join('<option value="'+esc(name)+'">'+esc(name)+' — '+str(values[0])+' MHz / BW '+str(values[1])+' / SF '+str(values[2])+' / CR '+str(values[3])+'</option>' for name,values in RADIO_PRESETS.items())+'</select></label><p class="muted">Current radio settings: '+esc(radio_settings or 'unavailable')+'</p><button class="primary" data-save-id="radio-preset">Apply preset to radio</button></form></section>'
  out+='<section class="card"><p class="eyebrow">CHANNELS</p><h2>Radio channels</h2><p class="muted">Channels are stored on the radio itself. Use a name starting with <code>#</code> for a public topic or a 32-character hexadecimal secret for a private channel.</p><div class="table-scroll"><table><thead><tr><th>Index</th><th>Name</th><th>State</th><th>Action</th></tr></thead><tbody>'
  if channels:
   for channel in channels:
    out+='<tr><td>'+str(channel["index"])+'</td><td>'+esc(channel["name"] or '—')+'</td><td>'+('Configured' if channel["configured"] else 'Empty')+'</td><td>'+('<form class="inline" method="post" action="/channel-delete"><input type="hidden" name="channel_index" value="'+str(channel["index"])+ '"><button class="danger">Delete</button></form>' if channel["configured"] else '<span class="muted">Available</span>')+'</td></tr>'
  else: out+='<tr><td class="muted" colspan="4">No radio channel data is available yet.</td></tr>'
  out+='</tbody></table></div><form class="channel-form" method="post" action="/channel-save"><h3>Add or replace a channel</h3><div class="grid"><label>Slot<select required name="channel_index">'+''.join('<option value="'+str(channel["index"])+ '">'+str(channel["index"])+(' · '+esc(channel["name"]) if channel["configured"] else ' · empty')+'</option>' for channel in slots)+'</select></label><label>Channel name<input required maxlength="32" name="channel_name"></label><label>Private secret<input name="channel_secret" maxlength="32" pattern="[0-9a-fA-F]{32}" placeholder="32 hex characters, blank for #name"></label></div><button class="secondary" data-save-id="channel">Save channel on radio</button></form></section>'
  out+='<section class="card"><p class="eyebrow">DEVICE</p><h2>MeshPage Publisher radio connection</h2><p class="muted">The server shares this companion connection across Publisher, Bot, Scheduler, and Radio Settings.</p><form method="post" action="/settings" id="connection-form"><label>Connection type<select id="connection-type" name="connection_type"><option value="serial"'+(' selected' if connection_type=="serial" else '')+'>Serial / USB companion</option><option value="tcp"'+(' selected' if connection_type=="tcp" else '')+'>TCP companion</option></select></label><div id="serial-fields" class="grid radio-transport-fields"'+serial_hidden+'><label>Serial device<input name="port" list="serial-devices" value="'+esc(self.store.setting("port","/dev/ttyUSB0"))+'"></label><datalist id="serial-devices">'+''.join('<option value="'+esc(path)+'"></option>' for path in serial_device_paths())+'</datalist><small>Choose a detected device or type a path. Reload this page after plugging in a device.</small><label>Baud rate<input name="baud" type="number" min="1" value="'+esc(self.store.setting("baud","115200"))+'"></label></div><div id="tcp-fields" class="grid radio-transport-fields"'+tcp_hidden+'><label>Companion host or IP<input name="tcp_host" placeholder="192.168.1.50 or mesh.local" value="'+esc(self.store.setting("tcp_host",""))+'"></label><label>TCP port<input name="tcp_port" type="number" min="1" max="65535" value="'+esc(self.store.setting("tcp_port","4000"))+'"></label></div><button class="secondary" data-save-id="connection">Save connection settings</button></form><script>(()=>{const mode=document.getElementById("connection-type"),groups={serial:document.getElementById("serial-fields"),tcp:document.getElementById("tcp-fields")};function sync(){for(const [name,group] of Object.entries(groups)){const active=mode.value===name;group.hidden=!active;for(const field of group.querySelectorAll("input")){field.disabled=!active;field.required=active}}}mode.addEventListener("change",sync);sync()})();</script></section>'
  self.respond(out+'<script defer src="/theme.js"></script></main></body></html>')
 def publish_page(self):
  try: return self.respond((Path(__file__).parent/"static"/"publish.html").read_text(encoding="utf-8"))
  except OSError as exc: return self.respond("Publish UI unavailable: "+esc(exc),"text/plain",500)
 def publisher_page(self):
  from pathlib import Path
  try: return self.respond((Path(__file__).parent/"static"/"publisher.html").read_text(encoding="utf-8"))
  except OSError as exc: return self.respond("Publisher UI unavailable: "+esc(exc),"text/plain",500)
 def static_file(self,path):
  from pathlib import Path
  root=(Path(__file__).parent/"static").resolve()
  name="assets/"+path.removeprefix("/assets/") if path.startswith("/assets/") else path.lstrip("/")
  target=(root/name).resolve()
  if root not in target.parents or not target.is_file(): return self.send_error(404)
  types={".css":"text/css",".js":"text/javascript",".json":"application/json",".png":"image/png"}
  if path=="/publisher.css": data=(theme.root_tokens_css()+target.read_text(encoding="utf-8")).encode("utf-8")
  elif path=="/theme.css": data=(theme.light_theme_css()+target.read_text(encoding="utf-8")).encode("utf-8")
  else: data=target.read_bytes()
  return self.respond(data,types.get(target.suffix,"application/octet-stream"))
 def respond(self,body,typ="text/html",code=200,headers=None):
  data=body if isinstance(body,bytes) else body.encode(); self.send_response(code); self.send_header("Content-Type",typ+"; charset=utf-8"); self.send_header("Content-Length",str(len(data)))
  for name,value in (headers or {}).items(): self.send_header(name,value)
  self.end_headers(); self.wfile.write(data)
 def log_message(self,*args): pass
def main(argv=None):
 p=argparse.ArgumentParser(); p.add_argument("--db",default="towncrier.db"); p.add_argument("--host",default="0.0.0.0"); p.add_argument("--port",type=int,default=8080); a=p.parse_args(argv)
 store=Store(a.db)
 class ServerHandler(Handler):
  publisher_sessions={}
 ServerHandler.store=store
 stop=threading.Event()
 with ThreadingHTTPServer((a.host,a.port),ServerHandler) as server, SharedRadio(store):
  scheduler=Scheduler(store,stop); bot=BotListener(store,stop)
  scheduler.start(); bot.start()
  def terminate(*_): raise KeyboardInterrupt
  previous=signal.signal(signal.SIGTERM,terminate) if threading.current_thread() is threading.main_thread() else None
  try: server.serve_forever()
  except KeyboardInterrupt: pass
  finally:
   stop.set()
   if previous is not None: signal.signal(signal.SIGTERM,previous)
 bot.join(); scheduler.join(timeout=2)
if __name__=="__main__": main()
