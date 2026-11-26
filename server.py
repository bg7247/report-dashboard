#!/usr/bin/env python3
"""Run the shared dashboard on a loopback port."""
import argparse
import contextlib
import datetime
import html
import json
import mimetypes
import shutil
import sqlite3
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

HERE = Path(__file__).resolve().parent
PAGES = {'reports': '보고서', 'figures': '그림', 'files': '파일', 'memo': '메모', 'history': '기록'}
TYPES = {
    'reports': {'.html', '.pdf', '.md', '.txt'},
    'figures': {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.tif', '.tiff', '.pdf'},
    'files': None,
}
MIB = 1024 * 1024


def visible_name(name):
    return (
        bool(name) and len(name.encode()) <= 255 and not name.startswith('.')
        and not any(c in '/\\' or ord(c) < 32 or ord(c) == 127 for c in name)
    )


class Dashboard(ThreadingHTTPServer):
    def __init__(self, port, root, max_upload, host):
        self.root = root.resolve()
        self.max_upload = max_upload
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        for folder in TYPES:
            path = self.root / folder
            if path.is_symlink():
                raise ValueError(f'Data directory must be a real directory: {path}')
            path.mkdir(mode=0o700, exist_ok=True)
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL,
                title TEXT NOT NULL, body TEXT NOT NULL, date TEXT NOT NULL
            )''')
        super().__init__(('127.0.0.1', port), Handler)
        self.hosts = {f'127.0.0.1:{self.server_port}', f'localhost:{self.server_port}'}
        if host:
            self.hosts.add(host.lower())

    @contextlib.contextmanager
    def db(self):
        with contextlib.closing(sqlite3.connect(self.root / 'entries.sqlite3')) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def headers_for(self, content_type, size, extra=()):
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(size))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        for key, value in extra:
            self.send_header(key, value)
        self.end_headers()

    def send_text(self, content, content_type, extra=()):
        data = content.encode()
        self.headers_for(content_type, len(data), extra)
        self.wfile.write(data)

    def check_host(self):
        if self.headers.get('Host', '').lower() in self.server.hosts:
            return True
        self.send_error(403)
        return False

    def file_path(self, section, name):
        if not visible_name(name):
            raise ValueError('파일 이름을 확인하세요.')
        types = TYPES[section]
        path = self.server.root / section / name
        if types is not None and path.suffix.lower() not in types:
            raise ValueError('지원하는 확장자가 아닙니다.')
        if path.parent.is_symlink() or path.is_symlink():
            raise ValueError('심볼릭 링크는 열 수 없습니다.')
        return path

    def serve_file(self, section, name, download):
        try:
            path = self.file_path(section, name)
            if not path.is_file():
                raise FileNotFoundError
            with path.open('rb') as stream:
                content_type = mimetypes.guess_type(name)[0] or 'application/octet-stream'
                disposition = 'attachment' if download or section == 'files' else 'inline'
                response_headers = [('Content-Disposition', f"{disposition}; filename*=UTF-8''{quote(name)}")]
                if disposition == 'attachment':
                    content_type = 'application/octet-stream'
                elif path.suffix.lower() in {'.html', '.svg'}:
                    response_headers.append(('Content-Security-Policy',
                        "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; "
                        "style-src 'unsafe-inline'; img-src data:; font-src data:; base-uri 'none'"))
                self.headers_for(content_type, path.stat().st_size, response_headers)
                shutil.copyfileobj(stream, self.wfile)
        except (ValueError, FileNotFoundError):
            self.send_error(404)

    def file_panel(self, section):
        rows = []
        folder = self.server.root / section
        if folder.is_symlink():
            return '<p>저장 폴더를 확인하세요.</p>'
        for path in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
            try:
                self.file_path(section, path.name)
            except ValueError:
                continue
            if not path.is_file():
                continue
            url = f'/content/{section}/{quote(path.name)}'
            name = html.escape(path.name)
            preview = f'<a href="{url}" target="_blank" rel="noopener">{name}</a>'
            if section == 'files':
                preview = name
            rows.append(f'<li><span>{preview}</span><a href="{url}?download=1">다운로드</a></li>')
        upload = ''
        if section == 'files':
            upload = ('<form id="upload"><label>파일 선택 <input name="file" type="file" required></label>'
                      '<button>업로드</button></form>')
        listing = '<ul class="files">' + ''.join(rows) + '</ul>' if rows else '<p class="empty">아직 항목이 없습니다.</p>'
        return upload + listing

    def entry_panel(self, section):
        with self.server.db() as db:
            entries = db.execute('SELECT * FROM entries WHERE kind = ? ORDER BY date DESC, id DESC', (section,)).fetchall()
        date_input = ''
        if section == 'history':
            date_input = f'<label>날짜 <input type="date" name="date" value="{datetime.date.today()}" required></label>'
        form = (f'<form class="entry" data-kind="{section}">{date_input}'
                '<input type="hidden" name="id">'
                '<label>제목 <input name="title" maxlength="200" required></label>'
                '<label>내용 <textarea name="body" rows="4" maxlength="40000" required></textarea></label>'
                '<div><button type="submit">추가</button> '
                '<button type="reset" hidden>취소</button></div></form>')
        if not entries:
            return form + '<p class="empty">아직 항목이 없습니다.</p>'
        rows = []
        for entry in entries:
            title = html.escape(entry['title'])
            body = html.escape(entry['body'])
            date = html.escape(entry['date'])
            rows.append(f'<article data-id="{entry["id"]}"><time>{date}</time><h3>{title}</h3><p>{body}</p>'
                        '<button type="button" data-edit>수정</button> '
                        '<button type="button" data-delete>삭제</button></article>')
        return form + ''.join(rows)

    def do_GET(self):
        if not self.check_host():
            return
        request = urlsplit(self.path)
        if request.path in ('/app.js', '/style.css'):
            asset = HERE / request.path[1:]
            self.send_text(asset.read_text(), mimetypes.guess_type(asset.name)[0])
            return
        parts = request.path.strip('/').split('/')
        if len(parts) == 3 and parts[0] == 'content' and parts[1] in TYPES:
            self.serve_file(parts[1], unquote(parts[2]), 'download' in parse_qs(request.query))
            return
        section = request.path.strip('/') or 'reports'
        if section not in PAGES:
            self.send_error(404)
            return
        nav = ''.join(
            f'<a href="/{key}" {"aria-current=page" if key == section else ""}>{label}</a>'
            for key, label in PAGES.items()
        )
        panel = self.file_panel(section) if section in TYPES else self.entry_panel(section)
        self.send_text(
            '<!doctype html><html lang="ko"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>팀 대시보드</title><link rel="stylesheet" href="/style.css">'
            '<script src="/app.js" defer></script>'
            f'<header><h1>팀 대시보드</h1><nav aria-label="대시보드">{nav}</nav></header>'
            f'<main><h2>{PAGES[section]}</h2><p id="message" role="status" hidden></p>{panel}</main></html>',
            'text/html; charset=utf-8',
            [('Content-Security-Policy', "default-src 'self'; frame-ancestors 'none'; base-uri 'none'")],
        )

    def upload(self, name, size):
        path = self.file_path('files', name)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.', delete=False) as stream:
            temp = Path(stream.name)
            try:
                while size:
                    chunk = self.rfile.read(min(size, MIB))
                    if not chunk:
                        raise ValueError('파일 전송이 중단되었습니다.')
                    stream.write(chunk)
                    size -= len(chunk)
                stream.flush()
                path.hardlink_to(temp)
            finally:
                temp.unlink(missing_ok=True)

    def save_entry(self, section, data):
        with self.server.db() as db:
            if 'delete' in data:
                row = db.execute('DELETE FROM entries WHERE kind = ? AND id = ?', (section, int(data['delete'])))
                if not row.rowcount:
                    raise FileNotFoundError
                return
            title, body = (data.get(key) for key in ('title', 'body'))
            if not isinstance(title, str) or not isinstance(body, str):
                raise ValueError('제목과 내용을 입력하세요.')
            title, body = title.strip(), body.strip()
            if not (0 < len(title) <= 200 and 0 < len(body) <= 40000):
                raise ValueError('제목은 200자, 내용은 40,000자까지 입력할 수 있습니다.')
            date = data.get('date') if section == 'history' else datetime.date.today().isoformat()
            date = datetime.date.fromisoformat(date or '').isoformat()
            if data.get('id'):
                row = db.execute(
                    'UPDATE entries SET title = ?, body = ?, date = ? WHERE kind = ? AND id = ?',
                    (title, body, date, section, int(data['id'])),
                )
                if not row.rowcount:
                    raise FileNotFoundError
            else:
                db.execute('INSERT INTO entries (kind, title, body, date) VALUES (?, ?, ?, ?)', (section, title, body, date))

    def do_POST(self):
        if not self.check_host():
            return
        if self.headers.get('X-Dashboard-Request') != '1':
            self.send_error(403)
            return
        request = urlsplit(self.path)
        try:
            size = int(self.headers.get('Content-Length', ''))
            if size < 0:
                raise ValueError('요청 크기를 확인하세요.')
            if request.path == '/api/upload':
                if size > self.server.max_upload:
                    self.send_error(413, 'File too large')
                    return
                name = parse_qs(request.query).get('name', [''])[0]
                self.upload(name, size)
            elif request.path in ('/api/memo', '/api/history'):
                if size > MIB:
                    self.send_error(413)
                    return
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('요청 형식을 확인하세요.')
                self.save_entry(request.path.rsplit('/', 1)[1], data)
            else:
                self.send_error(404)
                return
        except FileExistsError:
            self.send_error(409, 'A file with this name already exists')
            return
        except FileNotFoundError:
            self.send_error(404)
            return
        except (ValueError, TypeError, OverflowError):
            self.send_error(400, 'Invalid request')
            return
        self.send_text('{"ok":true}', 'application/json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8810)
    parser.add_argument('--data', type=Path, default=HERE / 'data')
    parser.add_argument('--max-upload-mib', type=int, default=100)
    parser.add_argument('--host', help='Additional allowed Host header for a reverse proxy')
    args = parser.parse_args()
    if args.max_upload_mib < 1:
        parser.error('--max-upload-mib must be at least 1')
    with Dashboard(args.port, args.data, args.max_upload_mib * MIB, args.host) as server:
        print(f'http://localhost:{server.server_port}/', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
