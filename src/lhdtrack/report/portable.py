"""Bundle a generated static report for review without its machine-local artifacts."""
from __future__ import annotations

import argparse
import base64
import html
from io import BytesIO
import json
from pathlib import Path
import re
import zipfile


def make_portable(source: Path, output: Path) -> None:
    text = source.read_text()
    attachments: dict[Path, str] = {}
    appendix = []

    def replace(match: re.Match) -> str:
        href, label = html.unescape(match[1]), match[2]
        if href.startswith(('#', 'http:', 'https:', 'data:')):
            return match[0]
        path = (source.parent / href).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"report artifact missing: {path}")
        if path.suffix.lower() == '.png':
            data = base64.b64encode(path.read_bytes()).decode()
            filename = path.name
            if path.parent.name == 'physical':
                work = path.parent.parent
                filename = '-'.join((work.parents[2].name, work.parents[1].name,
                                     work.name, path.name))
            return (f'<a data-placement-image href="data:image/png;base64,{data}" '
                    f'download="{html.escape(filename, quote=True)}">{label}</a>')
        if path.name == 'lefs.json':
            # Keep every view next to DEF under its original relative path.
            buffer = BytesIO()
            with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
                for entry in sorted(path.parent.rglob('*')):
                    if entry.is_file():
                        archive.write(entry, entry.relative_to(path.parent))
            data = base64.b64encode(buffer.getvalue()).decode()
            work = path.parent.parent
            filename = '-'.join((work.parents[2].name, work.parents[1].name,
                                 work.name, 'physical.zip'))
            return (f'<a href="data:application/zip;base64,{data}" '
                    f'download="{html.escape(filename, quote=True)}">DEF/LEF bundle</a>')
        if path in attachments:
            return f'<a href="#{attachments[path]}">{label}</a>'
        anchor = f'artifact-{len(attachments)}'
        attachments[path] = anchor
        content = path.read_text(errors='replace')
        if path.suffix == '.json':
            content = json.dumps(json.loads(content), indent=2)
        escaped = html.escape(content)
        # Preserve raw log spacing in <pre> while keeping generated source free
        # of trailing-whitespace errors in review and git diff --check.
        escaped = re.sub(r'(?m)[ \t]+$',
                         lambda m: ''.join('&#32;' if c == ' ' else '&#9;' for c in m[0]),
                         escaped)
        appendix.append(f'<details id="{anchor}"><summary>{html.escape(path.name)} · '
                        f'{html.escape(str(path.parent.name))}</summary>'
                        f'<pre>{escaped}</pre></details>')
        return f'<a href="#{anchor}">{label}</a>'

    text = re.sub(r'<a href="([^"]+)">(.*?)</a>', replace, text)
    note = ('<p class="sub"><strong>Provisional offline snapshot.</strong> '
            'PNG links open embedded post-placement images; no local files are required. '
            'Values come from the retained serial measurements.</p>')
    text = text.replace('</h1>', '</h1>' + note, 1)
    if appendix:
        text = text.replace('</html>', '<style>pre{white-space:pre-wrap;overflow-wrap:anywhere;'
                        'font:12px ui-monospace,monospace}</style>'
                        '<h2>Embedded review artifacts</h2>' + '\n'.join(appendix) + '</html>')
    if 'data-placement-image' in text:
        viewer = '''<style>
#placement-image-viewer{max-width:96vw;max-height:95vh;border:1px solid #aaa;
border-radius:8px;background:var(--bg,#fff);color:var(--fg,#16181d);padding:.75rem}
#placement-image-viewer::backdrop{background:#0009}
#placement-image-viewer form{text-align:right;margin:0}
#placement-image-viewer p{font-size:12px;overflow-wrap:anywhere}
#placement-image-viewer img{display:block;max-width:90vw;max-height:80vh}
</style>
<dialog id="placement-image-viewer" aria-label="Post-placement image">
<form method="dialog"><button autofocus>Close</button></form><p></p>
<img alt="Post-placement image"></dialog>
<script>
(function () {
  var viewer = document.getElementById('placement-image-viewer');
  document.addEventListener('click', function (event) {
    var link = event.target.closest('a[data-placement-image]');
    if (!link) return;
    event.preventDefault();
    viewer.querySelector('img').src = link.href;
    viewer.querySelector('p').textContent = link.getAttribute('download') || 'Post-placement';
    viewer.showModal();
  });
  viewer.addEventListener('click', function (event) {
    if (event.target === viewer) viewer.close();
  });
})();
</script>'''
        closing = '</body>' if '</body>' in text else '</html>'
        text = text.replace(closing, viewer + closing, 1)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    make_portable(args.source.resolve(), args.output.resolve())
