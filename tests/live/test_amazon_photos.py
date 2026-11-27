"""Opt-in real API checks. Uploading is a separate opt-in and no test deletes remote data."""

import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
import zlib
from pathlib import Path

from amazon_photos import AmazonPhotos, APIError, AuthenticationError


def live_client(secret_var='AP_COOKIES_FILE'):
    return AmazonPhotos(
        json.loads(Path(os.environ[secret_var]).expanduser().read_text(encoding='utf-8')),
        tld=os.getenv('AP_TLD', 'co.uk'),
        upload_api=os.getenv('AP_UPLOAD_API', 'cdproxy'),
        init_db=False, init_folders=False, db_path=None, timeout=25,
        user_agent=os.getenv('AP_USER_AGENT'),
    )


def synthetic_png(file, nonce):
    def chunk(kind, contents):
        data = kind + contents
        return struct.pack('>I', len(contents)) + data + struct.pack('>I', zlib.crc32(data))

    size = 64
    color = nonce.bytes
    pixels = bytearray()
    for y in range(size):
        pixels.append(0)  # PNG filter type for this scanline
        for x in range(size):
            start = (x // 8 + y // 8) % 12
            pixels.extend(color[start:start + 3])
    file.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(
        b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 2, 0, 0, 0),
    ) + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b''))


@unittest.skipUnless(os.getenv('AP_LIVE') == '1', 'set AP_LIVE=1 to use a real Amazon Photos account')
class AmazonPhotosLiveTests(unittest.TestCase):
    def test_authentication_and_photo_listing(self):
        with live_client() as ap:
            self.assertTrue(ap.root.get('id'))
            photos = ap.photos(limit=1)
            self.assertLessEqual(len(photos), 1)
            if not photos.empty:
                self.assertIn('id', photos.columns)

    def test_same_credentials_in_fresh_python_process(self):
        with live_client() as ap:
            self.assertTrue(ap.root.get('id'))
        subprocess.run([sys.executable, __file__, '--probe'], check=True, timeout=120)

    @unittest.skipUnless(os.getenv('AP_LIVE_UPLOAD') == '1', 'set AP_LIVE_UPLOAD=1 to add one synthetic image')
    def test_upload_verify_and_duplicate(self):
        with tempfile.TemporaryDirectory() as directory, live_client() as ap:
            nonce = uuid.uuid4()
            image = Path(directory) / f'amazon-photos-live-{nonce.hex}.png'
            synthetic_png(image, nonce)
            checksum = hashlib.md5(image.read_bytes(), usedforsecurity=False).hexdigest()
            print(f'\nSynthetic image name for manual Echo Show verification: {image.name}', flush=True)
            receipts = ap.upload(image, refresh=False, max_connections=1)
            self.assertEqual(len(receipts), 1)
            node_id = receipts[0].json()['id']
            self.assertRegex(node_id, r'^[A-Za-z0-9_-]+$')
            deadline = time.monotonic() + 60
            verified = False
            while time.monotonic() < deadline:
                try:
                    node = ap.backoff(ap.client.get, f'{ap.drive_url}/nodes/{node_id}', params=ap.base_params).json()
                except APIError as error:
                    if error.status_code != 404:
                        raise
                else:
                    actual = (node.get('contentProperties') or {}).get('md5')
                    if actual:
                        self.assertEqual(node['id'], node_id)
                        self.assertEqual(actual.lower(), checksum)
                        matches = ap.nodes(filters=[f'contentProperties.md5:{checksum}', 'status:AVAILABLE'])
                        if matches is not None and node_id in matches['id'].values:
                            verified = True
                            break
                time.sleep(3)
            self.assertTrue(verified, 'Uploaded node checksum and searchable ID did not become visible')
            # Use a fresh HTTP client without a cache. The same bytes must produce no second receipt.
            with live_client() as fresh:
                self.assertEqual(fresh.upload(image, refresh=False, max_connections=1), [])
                matches = fresh.nodes(filters=[f'contentProperties.md5:{checksum}', 'status:AVAILABLE'])
                self.assertEqual(matches['id'].tolist(), [node_id])

    @unittest.skipUnless(os.getenv('AP_EXPIRED_COOKIES_FILE'), 'supply a known-expired capture to check rejection')
    def test_reject_known_expired_session(self):
        self.assertNotEqual(
            Path(os.environ['AP_EXPIRED_COOKIES_FILE']).expanduser().resolve(),
            Path(os.environ['AP_COOKIES_FILE']).expanduser().resolve(),
        )
        with self.assertRaises(AuthenticationError):
            with live_client('AP_EXPIRED_COOKIES_FILE'):
                pass


if __name__ == '__main__':
    if sys.argv[1:] == ['--probe'] and os.getenv('AP_LIVE') == '1':
        with live_client() as client:
            if not re.fullmatch(r'[A-Za-z0-9_-]+', client.root['id']):
                raise RuntimeError('Invalid account root')
    else:
        unittest.main()
