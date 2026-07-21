#!/usr/bin/env python3
"""Tiny read-only HTTP server for fantasy snapshot."""
import http.server, socketserver, os
PUBLIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'public')
os.chdir(PUBLIC)
PORT = 8765

class Handler(http.server.SimpleHTTPRequestHandler):
    def list_directory(self, path):
        self.send_error(403, "Forbidden")
        return None
    def log_message(self, format, *args):
        pass

with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
    print(f"Serving {PUBLIC} on 127.0.0.1:{PORT}")
    httpd.serve_forever()
