import argparse, html, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from .db import Store
from .engine import Scheduler
CSS="body{font:16px sans-serif;max-width:1000px;margin:2rem auto;padding:0 1rem}input,textarea,button{font:inherit;padding:.4rem;margin:.2rem 0}textarea{width:100%;min-height:4rem}form{border:1px solid #ccc;padding:1rem;margin:1rem 0}table{width:100%;border-collapse:collapse}td,th{padding:.5rem;border-bottom:1px solid #ddd;text-align:left}.error{color:#a00}"
def esc(v): return html.escape(str(v),quote=True)
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
   elif path=="/save": self.store.save({"name":form.get("name",""),"channel":form.get("channel",""),"message":form.get("message",""),"interval":form.get("interval",""),"enabled":form.get("enabled")=="on"},form.get("id") or None)
   elif path=="/delete": self.store.delete(form["id"])
   elif path=="/toggle": self.store.toggle(form["id"])
   elif path=="/send":
    row=self.store.get(form["id"]); from .radio import Radio
    if not row: raise ValueError("schedule not found")
    Radio(self.store.setting("port","/dev/ttyUSB0"),self.store.setting("baud","115200")).send(row["channel"],row["message"])
   else: return self.send_error(404)
   self.send_response(303); self.send_header("Location","/"); self.end_headers()
  except Exception as exc: self.page(str(exc))
 def page(self,error=""):
  rows=self.store.schedules(); q=parse_qs(urlparse(self.path).query); edit=self.store.get(q["edit"][0]) if q.get("edit") else None; e=edit or {"id":"","name":"","channel":0,"message":"","interval":3600,"enabled":1}
  out='<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Towncrier</title><style>'+CSS+'</style></head><body><h1>Towncrier</h1><p>Local-only scheduler; no authentication. Keep this port on loopback.</p>'
  if error: out+='<p class="error">'+esc(error)+'</p>'
  out+='<form method="post" action="/settings"><h2>USB companion</h2><label>Serial port <input name="port" value="'+esc(self.store.setting("port","/dev/ttyUSB0"))+'"></label> <label>Baud <input name="baud" type="number" value="'+esc(self.store.setting("baud","115200"))+'"></label> <button>Save settings</button></form><h2>Schedules</h2><table><tr><th>Name</th><th>Channel</th><th>Interval</th><th>Status</th><th>Actions</th></tr>'
  for r in rows:
   out+='<tr><td>'+esc(r["name"])+'</td><td>'+str(r["channel"])+'</td><td>'+str(r["interval"])+'s</td><td>'+('enabled' if r["enabled"] else 'disabled')+' / '+esc(r["status"] or 'waiting')+' '+esc(r["last_error"] or '')+'</td><td><a href="/?edit='+str(r["id"])+ '">edit</a> <form style="display:inline" method="post" action="/toggle"><input type="hidden" name="id" value="'+str(r["id"])+ '"><button>toggle</button></form> <form style="display:inline" method="post" action="/send"><input type="hidden" name="id" value="'+str(r["id"])+ '"><button>send now</button></form> <form style="display:inline" method="post" action="/delete"><input type="hidden" name="id" value="'+str(r["id"])+ '"><button>delete</button></form></td></tr>'
  out+='</table><form method="post" action="/save"><h2>'+('Edit' if e["id"] else 'Add')+' schedule</h2><input type="hidden" name="id" value="'+esc(e["id"])+ '"><label>Name <input required name="name" value="'+esc(e["name"])+ '"></label> <label>Channel index <input required type="number" min="0" max="255" name="channel" value="'+str(e["channel"])+ '"></label> <label>Interval seconds <input required type="number" min="60" name="interval" value="'+str(e["interval"])+ '"></label><br><label>Message <textarea required maxlength="500" name="message">'+esc(e["message"])+ '</textarea></label><br><label><input type="checkbox" name="enabled" '+('checked' if e["enabled"] else '')+ '> enabled</label> <button>Save schedule</button></form></body></html>'
  self.respond(out)
 def respond(self,body,typ="text/html"):
  data=body.encode(); self.send_response(200); self.send_header("Content-Type",typ+"; charset=utf-8"); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data)
 def log_message(self,*args): pass
def main(argv=None):
 p=argparse.ArgumentParser(); p.add_argument("--db",default="towncrier.db"); p.add_argument("--host",default="127.0.0.1"); p.add_argument("--port",type=int,default=8080); a=p.parse_args(argv); store=Store(a.db); Handler.store=store; stop=threading.Event(); Scheduler(store,stop).start(); server=ThreadingHTTPServer((a.host,a.port),Handler)
 try: server.serve_forever()
 except KeyboardInterrupt: pass
 finally: stop.set(); server.server_close()
if __name__=="__main__": main()
