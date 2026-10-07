#!/usr/bin/env python3
"""
Trace Evidence - lab server (runs ONLY on lab-server, never shipped)

Serves two fake sites on the host-only network, chosen by Host header:

  mail.halcyon-dynamics.in   a tiny webmail: inbox, read, compose + attachment
  files.quietdrop.net        an anonymous file drop that redirects to /f/<id>
                             after an upload (that redirect is the Chrome
                             history entry players need for finding 6)

Standard library only. Run as root for port 80:
    sudo python3 lab_server.py --port 80
Data is kept in ./lab_data next to this script. Delete that folder to reset.

This server is scenery for building the evidence. It is not hardened and must
never touch a real network.
"""

import argparse
import cgi
import html
import json
import os
import re
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lab_data")
MAILBOX = os.path.join(DATA, "mailbox.json")
UPLOADS = os.path.join(DATA, "uploads")

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>{title}</title><style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#f3f4f6;color:#1f2937}}
.bar{{background:{brand};color:#fff;padding:14px 22px;font-size:20px;font-weight:600}}
.wrap{{max-width:820px;margin:24px auto;background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:22px}}
a{{color:#1d4ed8;text-decoration:none}} a:hover{{text-decoration:underline}}
.msg{{padding:12px;border-bottom:1px solid #eee}} .msg b{{display:block}}
input,button,textarea{{font:inherit;padding:8px;margin:4px 0;width:100%;box-sizing:border-box;border:1px solid #cbd5e1;border-radius:6px}}
button{{width:auto;background:{brand};color:#fff;border:0;padding:9px 18px;cursor:pointer}}
label{{font-size:13px;color:#475569}} .drop{{border:2px dashed #cbd5e1;border-radius:10px;padding:34px;text-align:center}}
</style></head><body><div class="bar">{bar}</div><div class="wrap">{body}</div></body></html>"""


def read_mailbox():
    try:
        with open(MAILBOX, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return []


def write_mailbox(msgs):
    os.makedirs(DATA, exist_ok=True)
    with open(MAILBOX, "w", encoding="utf-8") as fh:
        json.dump(msgs, fh, indent=2)


class Handler(BaseHTTPRequestHandler):
    server_version = "quietdrop/1.0"

    def site(self):
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return "mail" if host.startswith("mail.") else "files"

    def send_page(self, title, bar, brand, body, code=200):
        page = PAGE.format(title=title, bar=bar, brand=brand, body=body)
        self.send_html(page, code)

    def send_html(self, text, code=200):
        raw = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -------------------------------------------------------------- GET
    def do_GET(self):
        path = self.path.split("?")[0]
        if self.site() == "mail":
            return self.mail_get(path)
        return self.files_get(path)

    def mail_get(self, path):
        brand, bar = "#0f766e", "Halcyon Mail"
        msgs = read_mailbox()
        if path in ("/", "/inbox"):
            rows = "".join(
                '<div class="msg"><b><a href="/view?id=%d">%s</a></b>'
                '<span>from %s &middot; %s</span></div>'
                % (i, html.escape(m["subject"]), html.escape(m["frm"]), html.escape(m["date"]))
                for i, m in enumerate(msgs)) or "<p>Inbox empty.</p>"
            body = "<p><a href='/compose'>Compose</a></p>" + rows
            return self.send_page("Inbox", bar, brand, body)
        if path == "/view":
            qs = dict(re.findall(r"(\w+)=([^&]*)", self.path.split("?", 1)[-1]))
            i = int(qs.get("id", -1))
            if 0 <= i < len(msgs):
                m = msgs[i]
                att = ("<p>Attachment: <a href='/attachment?id=%d'>%s</a></p>"
                       % (i, html.escape(m["att_name"]))) if m.get("att_name") else ""
                body = ("<p><a href='/'>&larr; Inbox</a></p><h2>%s</h2>"
                        "<p>from %s &middot; %s</p><hr>%s%s"
                        % (html.escape(m["subject"]), html.escape(m["frm"]), html.escape(m["date"]),
                           html.escape(m["body"]).replace("\n", "<br>"), att))
                return self.send_page(m["subject"], bar, brand, body)
            return self.send_page("Not found", bar, brand, "<p>No such message.</p>", 404)
        if path == "/attachment":
            qs = dict(re.findall(r"(\w+)=([^&]*)", self.path.split("?", 1)[-1]))
            i = int(qs.get("id", -1))
            if 0 <= i < len(msgs) and msgs[i].get("att_path") and os.path.exists(msgs[i]["att_path"]):
                return self.send_file(msgs[i]["att_path"], msgs[i]["att_name"])
            return self.send_page("Not found", bar, brand, "<p>No attachment.</p>", 404)
        if path == "/compose":
            body = ("<h2>New message</h2><form method='POST' action='/send' enctype='multipart/form-data'>"
                    "<label>From</label><input name='frm' value='vikram.nair@halcyon-dynamics.in'>"
                    "<label>To</label><input name='to' value='meera.iyer@halcyon-dynamics.in'>"
                    "<label>Subject</label><input name='subject' value='Kestrel doc for review'>"
                    "<label>Body</label><textarea name='body' rows='6'>"
                    "Meera, draft attached for your review ahead of the board update. - V</textarea>"
                    "<label>Attachment</label><input type='file' name='att'>"
                    "<button type='submit'>Send</button></form>")
            return self.send_page("Compose", bar, brand, body)
        return self.send_page("Not found", bar, brand, "<p>Not found.</p>", 404)

    def files_get(self, path):
        brand, bar = "#4338ca", "QuietDrop"
        if path == "/":
            body = ("<h2>Anonymous file drop</h2>"
                    "<form method='POST' action='/upload' enctype='multipart/form-data'>"
                    "<div class='drop'>Choose a file, then Upload."
                    "<input type='file' name='file'></div>"
                    "<button type='submit'>Upload</button></form>"
                    "<p style='color:#64748b;font-size:13px'>No account needed. Link is public.</p>")
            return self.send_page("QuietDrop", bar, brand, body)
        m = re.fullmatch(r"/f/([0-9a-f]{12})", path)
        if m:
            fid = m.group(1)
            meta = os.path.join(UPLOADS, fid + ".json")
            if os.path.exists(meta):
                with open(meta, encoding="utf-8") as fh:
                    info = json.load(fh)
                body = ("<h2>File ready</h2><p><b>%s</b> &middot; %d bytes</p>"
                        "<p>Share this link: <code>http://files.quietdrop.net/f/%s</code></p>"
                        "<p><a href='/d/%s'>Download</a></p>"
                        % (html.escape(info["name"]), info["size"], fid, fid))
                return self.send_page("File ready", bar, brand, body)
            return self.send_page("Not found", bar, brand, "<p>Link expired.</p>", 404)
        m = re.fullmatch(r"/d/([0-9a-f]{12})", path)
        if m:
            fid = m.group(1)
            meta = os.path.join(UPLOADS, fid + ".json")
            if os.path.exists(meta):
                with open(meta, encoding="utf-8") as fh:
                    info = json.load(fh)
                return self.send_file(os.path.join(UPLOADS, fid + ".bin"), info["name"])
            return self.send_page("Not found", bar, brand, "<p>Gone.</p>", 404)
        return self.send_page("Not found", bar, brand, "<p>Not found.</p>", 404)

    # -------------------------------------------------------------- POST
    def do_POST(self):
        if self.site() == "mail" and self.path == "/send":
            return self.mail_send()
        if self.site() == "files" and self.path == "/upload":
            return self.files_upload()
        self.send_html("<p>Not found.</p>", 404)

    def parse_multipart(self):
        ctype, _ = cgi.parse_header(self.headers.get("Content-Type", ""))
        if ctype != "multipart/form-data":
            return None
        return cgi.FieldStorage(fp=self.rfile, headers=self.headers,
                                environ={"REQUEST_METHOD": "POST",
                                         "CONTENT_TYPE": self.headers["Content-Type"]})

    def mail_send(self):
        form = self.parse_multipart()
        msgs = read_mailbox()
        att_name = att_path = None
        if form is not None and "att" in form and getattr(form["att"], "filename", None):
            os.makedirs(UPLOADS, exist_ok=True)
            att_name = os.path.basename(form["att"].filename)
            att_path = os.path.join(UPLOADS, "mail_" + uuid.uuid4().hex[:8] + "_" + att_name)
            with open(att_path, "wb") as fh:
                fh.write(form["att"].file.read())
        msgs.insert(0, {
            "frm": form.getvalue("frm", "") if form else "",
            "to": form.getvalue("to", "") if form else "",
            "subject": form.getvalue("subject", "(no subject)") if form else "",
            "body": form.getvalue("body", "") if form else "",
            "date": time.strftime("%a, %d %b %Y %H:%M:%S", time.localtime()),
            "att_name": att_name, "att_path": att_path})
        write_mailbox(msgs)
        self.redirect("/")

    def files_upload(self):
        form = self.parse_multipart()
        if form is None or "file" not in form or not getattr(form["file"], "filename", None):
            return self.send_html("<p>No file.</p>", 400)
        os.makedirs(UPLOADS, exist_ok=True)
        fid = uuid.uuid4().hex[:12]
        blob = form["file"].file.read()
        with open(os.path.join(UPLOADS, fid + ".bin"), "wb") as fh:
            fh.write(blob)
        with open(os.path.join(UPLOADS, fid + ".json"), "w", encoding="utf-8") as fh:
            json.dump({"name": os.path.basename(form["file"].filename), "size": len(blob)}, fh)
        # Redirect to the share page: this is the entry that lands in Chrome history.
        self.redirect("/f/" + fid)

    def send_file(self, path, name):
        with open(path, "rb") as fh:
            raw = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % name)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):
        pass  # quiet


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=80)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()
    os.makedirs(UPLOADS, exist_ok=True)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print("lab server on %s:%d  (mail.halcyon-dynamics.in / files.quietdrop.net)" % (args.host, args.port))
    print("data in %s  -  Ctrl+C to stop" % DATA)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
