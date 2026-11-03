import argparse, html, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from .db import Store
from .engine import Scheduler, send_message
CSS="body{font:16px sans-serif;max-width:1000px;margin:2rem auto;padding:0 1rem}input,textarea,button{font:inherit;padding:.4rem;margin:.2rem 0}textarea{width:100%;min-height:4rem}form{border:1px solid #ccc;padding:1rem;margin:1rem 0}table{width:100%;border-collapse:collapse}td,th{padding:.5rem;border-bottom:1px solid #ddd;text-align:left}.error{color:#a00}"
def esc(v): return html.escape(str(v),quote=True)
def duration_parts(seconds): return int(seconds)//3600,(int(seconds)%3600)//60
def duration_label(seconds):
 h,m=duration_parts(seconds); return (f"{h}h " if h else "")+f"{m}m"
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  path=urlparse(self.path).path
  if path=="/": return self.page()
  if path=="/health": return self.respond("OK","text/plain")
  self.send_error(404)
 def do_POST(self):
  form={k:v[0] for k,v in parse_qs(self.rfile.read(int(self.headers.get("Content-Length",0))).decode(),keep_blank_values=True).items()}; path=urlparse(self.path).path
  try:
   if path=="/settings": self.store.set_setting("port",form.get("port","/dev/ttyUSB0")); self.store.set_setting("baud",form.get("baud","115200"))
   elif path=="/channel-save":
    from .radio import Radio
    index=int(form.get("channel_index","-1")); name=form.get("channel_name","").strip(); secret_hex=form.get("channel_secret","").strip()
    if not 0<=index<=255 or not 1<=len(name.encode())<=32: raise ValueError("channel index must be 0-255 and name must be 1-32 bytes")
    if secret_hex:
     if len(secret_hex)!=32: raise ValueError("private channel secret must be 32 hexadecimal characters")
     secret=bytes.fromhex(secret_hex)
    elif name.startswith("#"): secret=None
    else: raise ValueError("use a #name for a public topic channel or provide a 32-character private secret")
    Radio(self.store.setting("port","/dev/ttyUSB0"),self.store.setting("baud","115200")).set_channel(index,name,secret)
   elif path=="/channel-delete":
    from .radio import Radio
    index=int(form.get("channel_index","-1"))
    if not 0<=index<=255: raise ValueError("channel index must be 0-255")
    Radio(self.store.setting("port","/dev/ttyUSB0"),self.store.setting("baud","115200")).delete_channel(index)
   elif path=="/save":
    hours=int(form.get("hours","0")); minutes=int(form.get("minutes","0"))
    if hours<0 or not 0<=minutes<=59: raise ValueError("hours must be non-negative and minutes must be 0-59")
    self.store.save({"name":form.get("name",""),"channel":form.get("channel",""),"message":form.get("message",""),"interval":hours*3600+minutes*60,"enabled":form.get("enabled")=="on"},form.get("id") or None)
   elif path=="/delete": self.store.delete(form["id"])
   elif path=="/toggle": self.store.toggle(form["id"])
   elif path=="/send":
    row=self.store.get(form["id"]); from .radio import Radio
    if not row: raise ValueError("schedule not found")
    send_message(Radio(self.store.setting("port","/dev/ttyUSB0"),self.store.setting("baud","115200")),row["channel"],row["message"])
   else: return self.send_error(404)
   self.send_response(303); self.send_header("Location","/"); self.end_headers()
  except Exception as exc: self.page(str(exc))
 def page(self,error=""):
  rows=self.store.schedules(); q=parse_qs(urlparse(self.path).query); edit=self.store.get(q["edit"][0]) if q.get("edit") else None; e=edit or {"id":"","name":"","channel":0,"message":"","interval":3600,"enabled":1}; hours,minutes=duration_parts(e["interval"])
  channels=[]; channel_error=""
  try:
   from .radio import Radio
   channels=Radio(self.store.setting("port","/dev/ttyUSB0"),self.store.setting("baud","115200")).channels()
  except Exception as exc: channel_error=str(exc)
  out='<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Towncrier</title><style>'+CSS+'</style></head><body><h1>Towncrier</h1><p>Network-accessible scheduler; no authentication. Use only on a trusted network.</p>'
  if error: out+='<p class="error">'+esc(error)+'</p>'
  out+='<h2>Radio channels</h2><p>Channels are stored on the radio itself. Use a name beginning with <code>#</code> for a public topic channel, or enter a 32-character hexadecimal secret for a private channel.</p>'
  if channel_error: out+='<p class="error">Could not read channels: '+esc(channel_error)+'</p>'
  out+='<table><tr><th>Index</th><th>Name</th><th>Action</th></tr>'
  for channel in channels:
   out+='<tr><td>'+str(channel["index"])+'</td><td>'+esc(channel["name"])+'</td><td><form style="display:inline" method="post" action="/channel-delete"><input type="hidden" name="channel_index" value="'+str(channel["index"])+ '"><button>delete</button></form></td></tr>'
  out+='</table><form method="post" action="/channel-save"><h3>Add or replace a channel</h3><label>Index <input required type="number" min="0" max="255" name="channel_index"></label> <label>Name <input required maxlength="32" name="channel_name"></label> <label>Private secret (32 hex characters, blank for #name) <input name="channel_secret" maxlength="32" pattern="[0-9a-fA-F]{32}"></label> <button>Save channel on radio</button></form>'
  out+='<form method="post" action="/settings"><h2>USB companion</h2><label>Serial port <input name="port" value="'+esc(self.store.setting("port","/dev/ttyUSB0"))+'"></label> <label>Baud <input name="baud" type="number" value="'+esc(self.store.setting("baud","115200"))+'"></label> <button>Save settings</button></form><h2>Schedules</h2><table><tr><th>Name</th><th>Channel</th><th>Interval</th><th>Status</th><th>Actions</th></tr>'
  for r in rows:
   out+='<tr><td>'+esc(r["name"])+'</td><td>'+str(r["channel"])+'</td><td>'+esc(duration_label(r["interval"]))+'</td><td>'+('enabled' if r["enabled"] else 'disabled')+' / '+esc(r["status"] or 'waiting')+' '+esc(r["last_error"] or '')+'</td><td><a href="/?edit='+str(r["id"])+ '">edit</a> <form style="display:inline" method="post" action="/toggle"><input type="hidden" name="id" value="'+str(r["id"])+ '"><button>toggle</button></form> <form style="display:inline" method="post" action="/send"><input type="hidden" name="id" value="'+str(r["id"])+ '"><button>send now</button></form> <form style="display:inline" method="post" action="/delete"><input type="hidden" name="id" value="'+str(r["id"])+ '"><button>delete</button></form></td></tr>'
  out+='</table><form method="post" action="/save"><h2>'+('Edit' if e["id"] else 'Add')+' schedule</h2><input type="hidden" name="id" value="'+esc(e["id"])+ '"><label>Name <input required name="name" value="'+esc(e["name"])+ '"></label> <label>Channel index <input required type="number" min="0" max="255" name="channel" value="'+str(e["channel"])+ '"></label> <label>Hours <input required type="number" min="0" name="hours" value="'+str(hours)+ '"></label> <label>Minutes <input required type="number" min="0" max="59" name="minutes" value="'+str(minutes)+ '"></label><br><small>Minimum schedule interval: 30 minutes.</small><br><label>Message <textarea required maxlength="500" name="message">'+esc(e["message"])+ '</textarea></label><br><small>Messages are sent as channel chunks of about 120 characters, never more than 130.</small><br><label><input type="checkbox" name="enabled" '+('checked' if e["enabled"] else '')+ '> enabled</label> <button>Save schedule</button></form></body></html>'
  self.respond(out)
 def respond(self,body,typ="text/html"):
  data=body.encode(); self.send_response(200); self.send_header("Content-Type",typ+"; charset=utf-8"); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data)
 def log_message(self,*args): pass
def main(argv=None):
 p=argparse.ArgumentParser(); p.add_argument("--db",default="towncrier.db"); p.add_argument("--host",default="0.0.0.0"); p.add_argument("--port",type=int,default=8080); a=p.parse_args(argv); store=Store(a.db); Handler.store=store; stop=threading.Event(); Scheduler(store,stop).start(); server=ThreadingHTTPServer((a.host,a.port),Handler)
 try: server.serve_forever()
 except KeyboardInterrupt: pass
 finally: stop.set(); server.server_close()
if __name__=="__main__": main()
