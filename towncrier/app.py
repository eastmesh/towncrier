import argparse, html, json, secrets, threading, time
from http.cookies import SimpleCookie
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from .db import Store
from .radio import Radio, PublisherSession
from .engine import Scheduler, BotListener, send_message, recent_bot_messages, bot_listener_status
CSS=""":root{--ink:#222;--surface:#303030;--soft:#343434;--text:#e6eaf0;--muted:#9aa4b2;--green:#36a167;--green-dark:#267342;--deep:#224c36;--blue:#1e3a5f;--border:rgba(255,255,255,.08);--shadow:0 10px 30px rgba(0,0,0,.45)}*{box-sizing:border-box}body{margin:0;background:var(--ink);color:var(--text);font:16px/1.6 Inter,system-ui,-apple-system,"Segoe UI",sans-serif}main{max-width:1200px;margin:0 auto;padding:36px 24px 48px}header{border-bottom:1px solid var(--border);margin-bottom:24px;padding-bottom:20px}h1{font-size:2.4rem;line-height:1.15;margin:0;color:#fff}h1:before{content:'◈';color:var(--green);margin-right:12px}h2{font-size:1.5rem;line-height:1.25;margin-top:0}h3{margin-bottom:8px}.eyebrow{color:var(--green);font-size:.75rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase}.muted,small{color:var(--muted)}.card{background:var(--surface);border:1px solid var(--border);border-radius:14px;box-shadow:var(--shadow);padding:24px;margin:24px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px}label{display:flex;flex-direction:column;gap:4px;font-weight:600}input,textarea,select,button{font:inherit}input,textarea,select{background:var(--soft);border:1px solid var(--border);border-radius:10px;color:var(--text);padding:9px 11px}textarea{width:100%;min-height:110px}input:focus,textarea:focus,select:focus{outline:2px solid var(--green);outline-offset:2px}button{background:var(--green-dark);border:0;border-radius:999px;color:#fff;cursor:pointer;padding:9px 15px;font-weight:700}button:hover{background:#287a43}.secondary{background:var(--deep)}.danger{background:#7f2933}.toggle-enabled{background:#267342}.toggle-disabled{background:#7f2933}.message-delete{padding:4px 9px;font-size:.8rem}.message-row{cursor:grab}.message-row:active{cursor:grabbing}.edit-button{display:inline-block;background:var(--blue);border-radius:999px;color:#fff;padding:4px 10px;text-decoration:none;font-weight:700}.edit-button:hover{background:#2b5685}.actions{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.inline{display:inline;margin:0;padding:0;border:0;box-shadow:none;background:none}table{width:100%;border-collapse:collapse}th{color:var(--muted);font-size:.75rem;letter-spacing:.1em;text-align:left;text-transform:uppercase}td,th{border-bottom:1px solid var(--border);padding:12px 8px}code{background:var(--soft);border-radius:10px;padding:2px 6px}@media(max-width:700px){main{padding:20px 12px}.card{padding:16px}table{display:block;overflow-x:auto;white-space:nowrap}h1{font-size:2rem}button{min-height:44px}.grid{grid-template-columns:minmax(0,1fr)}input,select,textarea{width:100%;min-width:0}}"""
RADIO_PRESETS={"Australia":(915.800,250,10,5),"Australia (Narrow)":(916.575,62.5,7,7),"Australia (Mid)":(915.075,125,9,5),"Australia: SA, WA":(923.125,62.5,8,8),"Australia: QLD":(923.125,62.5,8,5)}
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
document.getElementById("schedule-form").addEventListener("submit",async function(event){event.preventDefault();const status=document.getElementById("save-status");status.textContent="Saving…";const response=await fetch(this.action,{method:"POST",body:new URLSearchParams(new FormData(this)),headers:{"X-Requested-With":"XMLHttpRequest"}});if(!response.ok){status.textContent="Save failed: "+await response.text();return}const page=new DOMParser().parseFromString(await response.text(),"text/html"),updatedList=page.getElementById("schedule-page");if(updatedList)document.getElementById("schedule-page").replaceWith(updatedList);status.textContent="Saved"});
</script>"""
class Handler(BaseHTTPRequestHandler):
 publisher_sessions={}
 publisher_sessions_lock=threading.Lock()
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
    _,channels=Radio.from_store(self.store).snapshot()
    return self.respond(json.dumps({"channels":channels}),"application/json")
   except Exception as exc: return self.respond(json.dumps({"error":str(exc)}),"application/json",502)
  if path=="/publisher-api/bot-status": return self.respond(json.dumps(bot_listener_status()),"application/json")
  if path=="/publisher-api/bot-messages":
   query=parse_qs(urlparse(self.path).query)
   try: channel=int(query.get("channel",[self.store.setting("bot_channel","-1")])[0])
   except ValueError: channel=-1
   return self.respond(json.dumps({"messages":self.store.bot_messages(channel)}),"application/json")
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
    radio=Radio.from_store(self.store)
    if path.endswith("/login"):
     key=str(payload.get("public_key","" )).strip().lower()
     room_session=PublisherSession(radio,key,payload.get("password",""))
     result=room_session.result
     token=secrets.token_urlsafe(32)
     with self.publisher_sessions_lock:
      now=time.monotonic()
      for stale,session in list(self.publisher_sessions.items()):
       if session[2]<=now:
        try: session[3].close()
        except Exception: pass
        self.publisher_sessions.pop(stale,None)
      self.publisher_sessions[token]=(self.client_address[0],key,now+28800,room_session)
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
      try: session[3].close()
      except Exception: pass
      return self.respond(json.dumps({"error":"Publisher session expired; log in again."}),"application/json",401)
     if session[0]!=self.client_address[0] or session[1]!=key:
      return self.respond(json.dumps({"error":"Publisher session is not authorized for this room server."}),"application/json",401)
     room_session=session[3]
    result=room_session.send(payload.get("text",""))
    return self.respond(json.dumps(result),"application/json")
   except (ValueError,UnicodeDecodeError) as exc:
    return self.respond(json.dumps({"error":str(exc)}),"application/json",400)
   except Exception as exc:
    return self.respond(json.dumps({"error":str(exc)}),"application/json",502)
  parsed=parse_qs(body.decode(),keep_blank_values=True); form={k:v[0] for k,v in parsed.items()}; ajax=self.headers.get("X-Requested-With")=="XMLHttpRequest"
  if path=="/bot":
   self.store.set_setting("bot_channel",form.get("channel","")); self.store.set_setting("bot_trigger",form.get("trigger","")); self.store.set_setting("bot_page_name",form.get("page_name","")); self.store.set_setting("bot_enabled","1" if form.get("enabled")=="on" else "0"); return self.bot_page()
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
    self.store.set_setting("connection_type",connection_type); self.store.set_setting("port",serial_port); self.store.set_setting("baud",baud); self.store.set_setting("tcp_host",tcp_host); self.store.set_setting("tcp_port",tcp_port)
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
    self.store.save({"name":form.get("name",""),"channels":parsed.get("channel",[]),"messages":messages,"interval":int(form.get("interval","1800")),"message_delay":int(form.get("message_delay","3")),"post_times":post_times,"enabled":form.get("enabled")=="on"},form.get("id") or None)
   elif path=="/delete": self.store.delete(form["id"])
   elif path=="/toggle": self.store.toggle(form["id"])
   elif path=="/send":
    row=self.store.get(form["id"]);
    if not row: raise ValueError("schedule not found")
    send_message(Radio.from_store(self.store),row["channels"],row["messages"],row["message_delay"])
   else: return self.send_error(404)
   if path=="/save" and ajax: return self.page()
   self.send_response(303); self.send_header("Location","/radio" if path in {"/settings","/radio-settings","/channel-save","/channel-delete"} else "/"); self.end_headers()
  except Exception as exc:
   if path=="/save" and ajax: return self.respond(str(exc),"text/plain",400)
   return self.radio_page(str(exc)) if path in {"/settings","/radio-settings","/channel-save","/channel-delete"} else self.page(str(exc))
 def schedules_page(self):
  return self.page()
  out='<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MeshPage Publisher · Schedules</title><link rel="stylesheet" href="/publisher.css"><link rel="stylesheet" href="/theme.css"></head><body><main class="shell towncrier-scheduler"><header class="masthead towncrier-header"><img class="brand-logo" src="/assets/logo.png" alt="EastMesh"><div class="brand-title"><p class="eyebrow">eastmesh.au</p><h1>MeshPage Publisher</h1></div><span class="radio-dot connected" title="Radio connected" aria-label="Radio connected"></span></header><nav class="towncrier-tabs" aria-label="MeshPage Publisher sections"><a href="/">Composer</a><a href="/publish">Publish</a><a class="active" href="/schedules">Schedules</a><a href="/bot">Bot</a><a href="/radio">Radio Settings</a><button id="themeToggle" class="theme-toggle" type="button" aria-label="Switch to light mode">☀ Light mode</button></nav><section class="card panel"><p class="eyebrow">SCHEDULES</p><h2>Saved schedules</h2><p><a class="edit-button" href="/?mode=schedule">Create schedule in the composer</a></p>'
  if not rows: out+='<p class="muted">No saved schedules yet. Create one from the shared composer.</p>'
  for row in rows:
   next_send=datetime.fromtimestamp(row["next_send"]).astimezone().strftime("%b %-d, %Y at %H:%M %Z") if row["enabled"] else "Paused"
   edit_url='/?schedule='+str(row["id"])
   out+='<article class="saved-schedule"><div class="saved-schedule-heading"><strong>'+esc(row["name"])+'</strong><span class="'+('toggle-enabled' if row["enabled"] else 'toggle-disabled')+'">'+('enabled' if row["enabled"] else 'disabled')+'</span></div><p class="muted">Channels '+', '.join(str(channel) for channel in row["channels"])+' · '+esc(duration_label(row["interval"]))+'</p><small>Next send: '+html.escape(next_send)+'</small> '+esc(row["last_error"] or '')+'<p class="muted">'+str(len(row["messages"]))+' message(s)</p><div class="saved-schedule-actions"><a class="edit-button" href="'+edit_url+'">Edit in composer</a><form class="inline" method="post" action="/toggle"><input type="hidden" name="id" value="'+str(row["id"])+ '"><button class="'+('toggle-enabled' if row["enabled"] else 'toggle-disabled')+'">'+('Pause' if row["enabled"] else 'Enable')+'</button></form><form class="inline" method="post" action="/send"><input type="hidden" name="id" value="'+str(row["id"])+ '"><button>Send now</button></form><form class="inline" method="post" action="/delete"><input type="hidden" name="id" value="'+str(row["id"])+ '"><button class="danger">Delete</button></form></div></article>'
  self.respond(out+'</section><script defer src="/theme.js"></script></main></body></html>')
 def page(self,error=""):
  rows=self.store.schedules(); q=parse_qs(urlparse(self.path).query); edit=self.store.get(q["edit"][0]) if q.get("edit") else None; e=edit or {"id":"","name":"","channel":0,"channels":[0],"messages":[""],"interval":3600,"message_delay":3,"send_at":"","enabled":1}; hours,minutes=duration_parts(e["interval"])
  channels=[]; channel_error=""; radio_settings={}; radio_error=""
  try:

   radio_settings,channels=Radio.from_store(self.store).snapshot()
   channels=channels[:5]
  except Exception as exc: radio_error=channel_error=str(exc)
  channel_options=channels or [{"index":index,"name":""} for index in range(5)]
  radio_dot='connected' if not radio_error else 'disconnected'
  out='<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>MeshPage Publisher · Scheduler</title><link rel="stylesheet" href="/publisher.css"><link rel="stylesheet" href="/theme.css"></head><body><main class="shell towncrier-scheduler"><header class="masthead towncrier-header"><img class="brand-logo" src="/assets/logo.png" alt="EastMesh"><div class="brand-title"><p class="eyebrow">eastmesh.au</p><h1>MeshPage Publisher</h1></div><span class="radio-dot '+radio_dot+'" title="Radio '+radio_dot+'" aria-label="Radio '+radio_dot+'"></span></header><nav class="towncrier-tabs" aria-label="MeshPage Publisher sections"><a href="/">Composer</a><a href="/publish">Publish</a><a class="active" href="/schedules">Schedules</a><a href="/bot">Bot</a><a href="/radio">Radio Settings</a><button id="themeToggle" class="theme-toggle" type="button" aria-label="Switch to light mode">☀ Light mode</button></nav>'
  if error: out+='<p class="error-message" role="alert">'+esc(error)+'</p>'
  schedule_items='<section id="schedule-page" class="card panel schedule-page-panel"><p class="eyebrow">SAVED SCHEDULES</p><h2>Load a schedule</h2>'
  if not rows: schedule_items+='<p class="muted">No saved schedules yet. Saved schedules will appear here.</p>'
  for r in rows:
   next_send=datetime.fromtimestamp(r["next_send"]).astimezone().strftime("%b %-d, %Y at %H:%M %Z") if r["enabled"] else "Paused"
   schedule_items+='<article class="saved-schedule"><div class="saved-schedule-heading"><strong>'+esc(r["name"])+'</strong><span class="'+('toggle-enabled' if r["enabled"] else 'toggle-disabled')+'">'+('enabled' if r["enabled"] else 'disabled')+'</span></div><p class="muted">Channels '+', '.join(str(channel) for channel in r["channels"])+' · '+esc(', '.join(r["post_times"]) or duration_label(r["interval"]))+'</p><small>Next send: '+html.escape(next_send)+'</small> '+esc(r["last_error"] or '')+'<div class="saved-schedule-actions"><a class="edit-button" href="/?edit='+str(r["id"])+ '">Load</a><form class="inline" method="post" action="/toggle"><input type="hidden" name="id" value="'+str(r["id"])+ '"><button class="'+('toggle-enabled' if r["enabled"] else 'toggle-disabled')+'">'+('Pause' if r["enabled"] else 'Enable')+'</button></form><form class="inline" method="post" action="/send"><input type="hidden" name="id" value="'+str(r["id"])+ '"><button>Send now</button></form><form class="inline" method="post" action="/delete"><input type="hidden" name="id" value="'+str(r["id"])+ '"><button class="danger">Delete</button></form></div></article>'
  schedule_items+='</section>'
  send_time=e.get("post_times") or ([e["send_at"]] if e.get("send_at") else ["09:00"])
  post_time_fields=''.join('<label>Daily post time (local) <input form="schedule-form" required type="time" name="post_time" value="'+esc(value)+'"></label>' for value in send_time)
  out+='<form id="schedule-form" method="post" action="/save"></form><div class="towncrier-workspace schedule-stack"><aside class="schedule-settings-rail"><section class="card panel schedule-setup-card"><p class="eyebrow">SCHEDULE SETUP</p><h2>'+('Edit' if e["id"] else 'Create a schedule')+'</h2><input type="hidden" form="schedule-form" name="id" value="'+esc(e["id"])+ '"><label>Name <input form="schedule-form" required name="name" value="'+esc(e["name"])+ '"></label><label>Channels <select form="schedule-form" required multiple size="5" name="channel">'+''.join('<option value="'+str(channel["index"])+'"'+(' selected' if channel["index"] in e["channels"] else '')+'>'+str(channel["index"])+ ' · '+esc(channel["name"] or 'Channel '+str(channel["index"]))+'</option>' for channel in channel_options)+'</select></label><fieldset><legend>Daily post times</legend><div id="daily-post-times">'+post_time_fields+'</div><button type="button" id="add-daily-post-time">Add another time</button></fieldset><label>Delay between messages (seconds) <input form="schedule-form" required type="number" min="0" max="3600" name="message_delay" value="'+str(e["message_delay"])+ '"></label><small>Times use this computer’s local timezone. MeshPage Publisher posts once at each selected time every day. Editing retains the current pending post time.</small></section></aside><section class="card panel schedule-page-source"><p class="eyebrow">MESSAGE PAGE</p><h2>Choose a saved page</h2><p class="muted">Schedules publish the messages from a saved Composer page.</p><label>Saved page<select form="schedule-form" required name="page_name"><option value="">Choose a saved page…</option>'+''.join('<option value="'+esc(item["name"])+('" selected' if item["messages"]==e["messages"] else '"')+'>'+esc(item["name"])+" ("+str(len(item["messages"]))+" messages)</option>" for item in self.store.saved_lists())+'</select></label></section><aside class="schedule-settings-rail schedule-actions-rail"><section class="card panel schedule-actions-card"><p class="eyebrow">SAVE SCHEDULE</p><label class="enabled-field"><input form="schedule-form" type="checkbox" name="enabled" '+('checked' if e["enabled"] else '')+ '> Schedule active</label><button class="primary" type="submit" form="schedule-form">Save schedule</button><span id="save-status" class="muted" aria-live="polite"></span></section>'+schedule_items+'</aside></div>'
  self.respond(out+'<script>document.getElementById("add-daily-post-time").onclick=()=>{const wrap=document.getElementById("daily-post-times"),label=document.createElement("label");label.textContent="Daily post time (local) ";const input=document.createElement("input");input.type="time";input.name="post_time";input.required=true;label.append(input);wrap.append(label)}</script><script defer src="/theme.js"></script></main></body></html>')
 def bot_page(self):
  try: _,channels=Radio.from_store(self.store).snapshot()
  except Exception: channels=[]
  pages=self.store.saved_lists()
  bot_channel=self.store.setting("bot_channel","")
  bot_trigger=self.store.setting("bot_trigger","")
  bot_page_name=self.store.setting("bot_page_name","")
  bot_enabled=self.store.setting("bot_enabled","0") == "1"
  page_options="".join('<option value="'+esc(item["name"])+('" selected' if item["name"]==bot_page_name else '"')+'>'+esc(item["name"])+" ("+str(len(item["messages"]))+" messages)</option>" for item in pages)
  channel_options="".join('<option value="'+str(row["index"])+('" selected' if str(row["index"])==bot_channel else '"')+'>'+str(row["index"])+" · "+esc(row.get("name") or ("Channel "+str(row["index"])))+'</option>' for row in channels if row.get("configured"))
  checked=" checked" if bot_enabled else ""
  bot_script="""<script>
const botMessages=document.getElementById("bot-messages"),botChannel=document.getElementById("bot-channel"),botStatus=document.getElementById("bot-status");
async function refreshBotStatus(){try{const status=await (await fetch("/publisher-api/bot-status")).json();botStatus.textContent=`${status.state} · selected channel ${status.channel??"-"}${status.error?" · "+status.error:""}`}catch(error){botStatus.textContent="Listener status unavailable"}}
async function refreshBotMessages(){try{const response=await fetch("/publisher-api/bot-messages?channel="+encodeURIComponent(botChannel.value));const data=await response.json();botMessages.innerHTML=data.messages.length?data.messages.slice().reverse().map(item=>`<article class="card"><small>${new Date(item.time*1000).toLocaleTimeString()} · Channel ${item.channel}${item.triggered?" · trigger matched":""}</small><div>${String(item.text).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]))}</div></article>`).join(""):'<p class="muted">No messages seen yet on this channel.</p>';}catch(error){botMessages.innerHTML='<p class="muted">Could not load listener status.</p>'}}
refreshBotStatus();refreshBotMessages();setInterval(refreshBotStatus,3000);setInterval(refreshBotMessages,3000);botChannel.addEventListener("change",refreshBotMessages);
</script>"""
  out='<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>MeshPage Publisher · Bot</title><link rel="stylesheet" href="/publisher.css"><link rel="stylesheet" href="/theme.css"></head><body><main class="shell towncrier-scheduler"><header class="masthead towncrier-header"><img class="brand-logo" src="/assets/logo.png" alt="EastMesh"><div class="brand-title"><p class="eyebrow">eastmesh.au</p><h1>MeshPage Publisher</h1></div><span class="radio-dot connected" title="Radio connected" aria-label="Radio connected"></span></header><nav class="towncrier-tabs"><a href="/">Composer</a><a href="/publish">Publish</a><a href="/schedules">Schedules</a><a class="active" href="/bot">Bot</a><a href="/radio">Radio Settings</a><button id="themeToggle" class="theme-toggle">☀ Light mode</button></nav><section class="card"><h2>Channel trigger</h2><p class="muted">When a message starts with the trigger word on the selected channel, MeshPage Publisher posts the selected saved page to that channel.</p><form method="post" action="/bot"><label>Channel<select id="bot-channel" required name="channel">'+channel_options+'</select></label><label>Trigger word<input required name="trigger" maxlength="32" value="'+esc(bot_trigger)+'"></label><label>Saved page<select required name="page_name"><option value="">Choose a saved page…</option>'+page_options+'</select></label><label class="enabled-field"><input type="checkbox" name="enabled"'+checked+'> Bot active</label><button class="primary">Save bot settings</button></form></section><section class="card"><h2>Listener status</h2><p id="bot-status" class="muted">Checking listener…</p></section><section class="card"><h2>Messages seen on this channel</h2><p class="muted">Recent messages received by the bot listener. Trigger matches are marked.</p><div id="bot-messages"><p class="muted">Waiting for messages…</p></div></section></main>'+bot_script+'<script defer src="/theme.js"></script></body></html>'
  return self.respond(out)
 def radio_page(self,error=""):
  radio_settings={}; channels=[]
  try:

   radio_settings,channels=Radio.from_store(self.store).snapshot()
   channels=channels[:40]
  except Exception as exc:
   error=error or str(exc)
  slots=channels or [{"index":index,"name":"","configured":False} for index in range(5)]
  connection_type=self.store.setting("connection_type","serial")
  serial_hidden=" hidden" if connection_type=="tcp" else ""
  tcp_hidden=" hidden" if connection_type!="tcp" else ""
  out='<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>MeshPage Publisher · Radio Settings</title><link rel="stylesheet" href="/publisher.css"><link rel="stylesheet" href="/theme.css"></head><body><main class="shell towncrier-scheduler"><header class="masthead towncrier-header"><img class="brand-logo" src="/assets/logo.png" alt="EastMesh"><div class="brand-title"><p class="eyebrow">eastmesh.au</p><h1>MeshPage Publisher</h1></div><span class="radio-dot connected" title="Radio connected" aria-label="Radio connected"></span></header><nav class="towncrier-tabs" aria-label="MeshPage Publisher sections"><a href="/">Composer</a><a href="/publish">Publish</a><a href="/schedules">Schedules</a><a href="/bot">Bot</a><a class="active" href="/radio">Radio Settings</a><button id="themeToggle" class="theme-toggle" type="button" aria-label="Switch to light mode">☀ Light mode</button></nav><p class="muted">Radio configuration is shared by the scheduler and MeshPage Publisher.</p>'
  if error: out+='<p class="error-message" role="alert">Radio status: '+esc(error)+'</p>'
  out+='<section class="card"><p class="eyebrow">RADIO PRESET</p><h2>Regional radio settings</h2><p class="muted">Select an Australian MeshCore preset to configure the radio frequency and modulation.</p><form method="post" action="/radio-settings"><label>Preset<select required name="preset">'+''.join('<option value="'+esc(name)+'">'+esc(name)+' — '+str(values[0])+' MHz / BW '+str(values[1])+' / SF '+str(values[2])+' / CR '+str(values[3])+'</option>' for name,values in RADIO_PRESETS.items())+'</select></label><p class="muted">Current radio settings: '+esc(radio_settings or 'unavailable')+'</p><button class="primary">Apply preset to radio</button></form></section>'
  out+='<section class="card"><p class="eyebrow">CHANNELS</p><h2>Radio channels</h2><p class="muted">Channels are stored on the radio itself. Use a name starting with <code>#</code> for a public topic or a 32-character hexadecimal secret for a private channel.</p><div class="table-scroll"><table><thead><tr><th>Index</th><th>Name</th><th>State</th><th>Action</th></tr></thead><tbody>'
  if channels:
   for channel in channels:
    out+='<tr><td>'+str(channel["index"])+'</td><td>'+esc(channel["name"] or '—')+'</td><td>'+('Configured' if channel["configured"] else 'Empty')+'</td><td>'+('<form class="inline" method="post" action="/channel-delete"><input type="hidden" name="channel_index" value="'+str(channel["index"])+ '"><button class="danger">Delete</button></form>' if channel["configured"] else '<span class="muted">Available</span>')+'</td></tr>'
  else: out+='<tr><td class="muted" colspan="4">No radio channel data is available yet.</td></tr>'
  out+='</tbody></table></div><form class="channel-form" method="post" action="/channel-save"><h3>Add or replace a channel</h3><div class="grid"><label>Slot<select required name="channel_index">'+''.join('<option value="'+str(channel["index"])+ '">'+str(channel["index"])+(' · '+esc(channel["name"]) if channel["configured"] else ' · empty')+'</option>' for channel in slots)+'</select></label><label>Channel name<input required maxlength="32" name="channel_name"></label><label>Private secret<input name="channel_secret" maxlength="32" pattern="[0-9a-fA-F]{32}" placeholder="32 hex characters, blank for #name"></label></div><button class="secondary">Save channel on radio</button></form></section>'
  out+='<section class="card"><p class="eyebrow">DEVICE</p><h2>MeshPage Publisher radio connection</h2><p class="muted">These connection settings are used by the Scheduler and Publisher.</p><form method="post" action="/settings" id="connection-form"><label>Connection type<select id="connection-type" name="connection_type"><option value="serial"'+(' selected' if connection_type=="serial" else '')+'>Serial / USB companion</option><option value="tcp"'+(' selected' if connection_type=="tcp" else '')+'>TCP companion</option></select></label><div id="serial-fields" class="grid radio-transport-fields"'+serial_hidden+'><label>Serial device<input name="port" value="'+esc(self.store.setting("port","/dev/ttyUSB0"))+'"></label><label>Baud rate<input name="baud" type="number" min="1" value="'+esc(self.store.setting("baud","115200"))+'"></label></div><div id="tcp-fields" class="grid radio-transport-fields"'+tcp_hidden+'><label>Companion host or IP<input name="tcp_host" placeholder="192.168.1.50 or mesh.local" value="'+esc(self.store.setting("tcp_host",""))+'"></label><label>TCP port<input name="tcp_port" type="number" min="1" max="65535" value="'+esc(self.store.setting("tcp_port","4000"))+'"></label></div><button class="secondary">Save connection settings</button></form><script>(()=>{const mode=document.getElementById("connection-type"),groups={serial:document.getElementById("serial-fields"),tcp:document.getElementById("tcp-fields")};function sync(){for(const [name,group] of Object.entries(groups)){const active=mode.value===name;group.hidden=!active;for(const field of group.querySelectorAll("input")){field.disabled=!active;field.required=active}}}mode.addEventListener("change",sync);sync()})();</script></section>'
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
  return self.respond(target.read_bytes(),types.get(target.suffix,"application/octet-stream"))
 def respond(self,body,typ="text/html",code=200,headers=None):
  data=body if isinstance(body,bytes) else body.encode(); self.send_response(code); self.send_header("Content-Type",typ+"; charset=utf-8"); self.send_header("Content-Length",str(len(data)))
  for name,value in (headers or {}).items(): self.send_header(name,value)
  self.end_headers(); self.wfile.write(data)
 def log_message(self,*args): pass
def main(argv=None):
 p=argparse.ArgumentParser(); p.add_argument("--db",default="towncrier.db"); p.add_argument("--host",default="0.0.0.0"); p.add_argument("--port",type=int,default=8080); a=p.parse_args(argv); store=Store(a.db); Handler.store=store; stop=threading.Event(); Scheduler(store,stop).start(); BotListener(store,stop).start(); server=ThreadingHTTPServer((a.host,a.port),Handler)
 try: server.serve_forever()
 except KeyboardInterrupt: pass
 finally: stop.set(); server.server_close()
if __name__=="__main__": main()
