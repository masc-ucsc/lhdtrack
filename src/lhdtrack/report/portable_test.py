"""Offline snapshots retain physical views and need no local artifact paths."""
import base64
from io import BytesIO
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest
import zipfile

from .portable import make_portable


class PortableReport(unittest.TestCase):
    def test_text_is_embedded_and_lef_manifest_resolves_inside_download(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            physical = root / 'test/cfg/asap7/flow/physical'
            (physical / 'lef').mkdir(parents=True)
            (physical / 'out.def').write_text('DESIGN test ;\nEND DESIGN\n')
            (physical / 'lef/tech.lef').write_text('VERSION 5.8 ;\n')
            (physical / 'lefs.json').write_text(json.dumps(['lef/tech.lef']))
            (root / 'stats.json').write_text('{"cycles": 123}')
            page, output = root / 'page.html', root / 'offline.html'
            page.write_text('<html><h1>Test</h1><a href="stats.json">perf</a>'
                            '<a href="test/cfg/asap7/flow/physical/lefs.json">LEFs</a></html>')
            make_portable(page, output)
            text = output.read_text()
            self.assertIn('&quot;cycles&quot;: 123', text)
            self.assertNotIn('href="stats.json"', text)
            payload = re.search(r'data:application/zip;base64,([^"]+)', text)[1]
            with zipfile.ZipFile(BytesIO(base64.b64decode(payload))) as archive:
                for lef in json.loads(archive.read('lefs.json')):
                    self.assertIn(lef, archive.namelist())
                self.assertIn('out.def', archive.namelist())

    def test_png_link_embeds_binary_without_text_appendix(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            png = base64.b64decode(
                'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3io'
                'AAAAASUVORK5CYII=')
            (root / 'post-place.png').write_bytes(png)
            page, output = root / 'page.html', root / 'offline.html'
            page.write_text('<html><h1>Test</h1><a href="post-place.png">PNG</a></html>')
            make_portable(page, output)
            text = output.read_text()
            payload = re.search(r'data:image/png;base64,([^"]+)', text)[1]
            self.assertEqual(base64.b64decode(payload), png)
            self.assertNotIn('Embedded review artifacts', text)
            self.assertNotIn('href="post-place.png"', text)

    def test_missing_artifact_cannot_silently_create_an_offline_snapshot(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            page = root / 'page.html'
            page.write_text('<html><a href="missing.json">missing</a></html>')
            with self.assertRaises(FileNotFoundError):
                make_portable(page, root / 'offline.html')
