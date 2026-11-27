import asyncio
import hashlib
import json
import math
import tempfile
import unittest
from collections import Counter
from datetime import date
from pathlib import Path
from unittest.mock import patch

import httpx
import pandas as pd

from amazon_photos import (
    AmazonPhotos, APIError, AuthenticationError, ProtocolError, TransportError, UploadUncertainError,
)


UK_COOKIES = {'session-id': 'offline-session', 'ubid-acbuk': 'offline-ubid', 'at-acbuk': 'offline-token'}
ROOT = {'id': 'root', 'ownerId': 'owner'}
MEDIA = b'example-image-bytes'
CHECKSUM = hashlib.md5(MEDIA, usedforsecurity=False).hexdigest()


def root_request(request):
    return request.url.params.get('filters') == 'isRoot:true'


def page(nodes, count=None):
    return httpx.Response(200, json={'data': nodes, 'count': len(nodes) if count is None else count})


class ClientTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        for transport, method in (
            (httpx.HTTPTransport, 'handle_request'),
            (httpx.AsyncHTTPTransport, 'handle_async_request'),
        ):
            guard = patch.object(transport, method, side_effect=AssertionError('Ordinary tests forbid external HTTP.'))
            guard.start()
            self.addCleanup(guard.stop)


    def client(self, handler, *, cookies=None, async_handler=None, **options):
        def route(request):
            if root_request(request):
                return page([ROOT])
            return handler(request)

        async def async_route(request):
            if async_handler:
                return await async_handler(request)
            return route(request)

        settings = {
            'init_db': False, 'init_folders': False, 'db_path': None,
            'transport': httpx.MockTransport(route), 'async_transport': httpx.MockTransport(async_route),
        } | options
        client = AmazonPhotos(UK_COOKIES if cookies is None else cookies, **settings)
        self.addCleanup(client.close)
        return client

    def image(self, filename='test.jpg'):
        file = self.work / filename
        file.write_bytes(MEDIA)
        return file

    def no_request(self, request):
        self.fail(f'Unexpected HTTP {request.method} to {request.url.path}')

    def test_legacy_and_regional_cookies_and_endpoint_overrides(self):
        cases = [
            (UK_COOKIES, 'co.uk', 'eu'),
            ({'session_id': 'offline-session', 'ubid_acbuk': 'offline-ubid', 'at_acbuk': 'offline-token'}, 'co.uk', 'eu'),
            ({'session-id': 'offline-session', 'at_main': 'offline-token'}, 'com', 'na'),
            ({'session-id': 'offline-session', 'at-main': 'offline-token'}, 'com', 'na'),
            ({'session-id': 'offline-session', 'at-acbjp': 'offline-token'}, 'co.jp', 'jp'),
            ({'session-id': 'offline-session', 'ubid-acbde': 'offline-ubid'}, 'de', 'eu'),
        ]
        for cookies, suffix, zone in cases:
            with self.subTest(suffix=suffix, names=list(cookies)):
                def handler(request):
                    self.assertEqual(request.headers['x-amzn-sessionid'], 'offline-session')
                    self.assertIn('session-id=offline-session', request.headers['cookie'])
                    if 'offline-token' in cookies.values():
                        self.assertIn('at-' + ('main' if suffix == 'com' else 'acb' + suffix[-2:]), request.headers['cookie'])
                    return page([])

                ap = self.client(handler, cookies=cookies)
                self.assertEqual(ap.drive_url, f'https://www.amazon.{suffix}/drive/v1')
                self.assertEqual(ap.thumb_url, f'https://thumbnails-photos.amazon.{suffix}/v1/thumbnail')
                self.assertEqual(ap.cdproxy_url, f'https://content-{zone}.drive.amazonaws.com/cdproxy/nodes')
                self.assertTrue(ap.photos(limit=1).empty)

        mixed = UK_COOKIES | {'at-main': 'other-market-token'}
        for cookies in (mixed, dict(reversed(list(mixed.items())))):
            with self.assertRaises(ValueError):
                self.client(self.no_request, cookies=cookies)

        def overridden(request):
            self.assertEqual(request.url.host, 'drive.example.test')
            self.assertNotIn('other-market-token', request.headers['cookie'])
            return page([])

        ap = self.client(overridden, cookies=mixed, tld='amazon.co.uk',
                         drive_override='https://drive.example.test/v1/',
                         cdproxy_override='https://content.example.test/upload')
        self.assertEqual(ap.drive_url, 'https://drive.example.test/v1')
        self.assertEqual(ap.upload_url, 'https://content.example.test/upload')
        ap.photos(limit=1)

    def test_missing_credentials_and_insecure_endpoint(self):
        with self.assertRaises(AuthenticationError):
            self.client(self.no_request, cookies={'session-id': 'value', 'anything-main': 'value'})
        with self.assertRaises(AuthenticationError):
            self.client(self.no_request, cookies={'at-main': 'fake'})
        with self.assertRaises(ValueError):
            self.client(self.no_request, drive_override='http://example.test/v1')

    def test_login_redirect_and_401_are_actionable_and_do_not_retry(self):
        statuses = [
            (401, {}),
            (302, {'Location': '/ap/signin?opaque=private'}),
            (302, {'Location': '/ap/cvf'}),
            (403, {}),
            (200, {'Content-Type': 'text/html'}),
        ]
        for status, headers in statuses:
            with self.subTest(status=status):
                calls = []

                def handler(request):
                    calls.append(request)
                    return httpx.Response(status, headers=headers, content=b'<form id="ap_signin_form">')

                ap = self.client(handler)
                with self.assertRaises(AuthenticationError) as error:
                    ap.photos(limit=1)
                self.assertIsInstance(error.exception, PermissionError)
                self.assertNotIn('offline-session', str(error.exception))
                self.assertEqual(len(calls), 1)

    def test_access_denied_and_invalid_requests_are_not_retried(self):
        for status in (400, 403, 404):
            calls = []

            def handler(request):
                calls.append(request)
                return httpx.Response(status, json={'message': 'private response'})

            ap = self.client(handler)
            with self.assertRaises(APIError) as error:
                ap.photos(limit=1)
            self.assertEqual(error.exception.status_code, status)
            self.assertNotIn('private response', str(error.exception))
            self.assertEqual(len(calls), 1)

    def test_sync_retry_after_and_retry_exhaustion(self):
        counts = Counter()

        def handler(request):
            counts['GET'] += 1
            if counts['GET'] == 1:
                return httpx.Response(429, headers={'Retry-After': '2'})
            return httpx.Response(503)

        ap = self.client(handler)
        with patch('amazon_photos._api.time.sleep') as sleep:
            with self.assertRaises(APIError) as error:
                ap.photos(limit=1)
            self.assertEqual(sleep.call_args_list[0].args, (2.0,))
            self.assertEqual(sleep.call_count, 3)
            self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(counts['GET'], 4)

        ap = self.client(lambda request: httpx.Response(429, headers={'Retry-After': '100'}))
        with patch('amazon_photos._api.time.sleep') as sleep:
            with self.assertRaises(APIError) as error:
                ap.photos(limit=1)
            self.assertEqual(error.exception.status_code, 429)
            sleep.assert_not_called()

    def test_session_cookie_rotation_used_on_next_request(self):
        calls = []

        def handler(request):
            calls.append(request.headers.get('x-amzn-sessionid'))
            return httpx.Response(200, headers={'Set-Cookie': 'session-id=rotated-offline; Path=/; Secure'},
                                  json={'data': [], 'count': 0})

        ap = self.client(handler)
        ap.photos(limit=1)
        ap.photos(limit=1)
        self.assertEqual(calls, ['offline-session', 'rotated-offline'])

    def test_pagination_honors_offset_limit_and_deduplicates(self):
        requested = []

        def handler(request):
            args = request.url.params
            offset, limit = int(args['offset']), int(args['limit'])
            requested.append((request.url.path, offset, limit))
            nodes = [{'id': str(i)} for i in range(offset, offset + limit)]
            if offset == 205:
                nodes[0]['id'] = '204'
            return page(nodes, count=1000)

        ap = self.client(handler)
        for method, endpoint in ((ap.photos, 'search'), (ap.nodes, 'nodes'), (ap.trashed, 'trash')):
            with self.subTest(endpoint=endpoint):
                requested.clear()
                frame = method(offset=5, limit=202)
                self.assertEqual(requested, [(f'/drive/v1/{endpoint}', 5, 200), (f'/drive/v1/{endpoint}', 205, 2)])
                self.assertEqual(len(frame), 201)
                self.assertEqual(frame.iloc[-1]['id'], '206')
        self.assertTrue(ap.photos(limit=0).empty)

    def test_pagination_caps_final_request_and_handles_empty_nodes(self):
        requested = []

        def handler(request):
            args = request.url.params
            offset, limit = int(args['offset']), int(args['limit'])
            requested.append((offset, limit))
            return page([{'id': str(i)} for i in range(offset, offset + limit)], 12000)

        ap = self.client(handler)
        self.assertEqual(len(ap.nodes(limit=math.inf, offset=9798)), 201)
        self.assertEqual(requested, [(9798, 200), (9998, 1)])
        self.assertTrue(ap.nodes_by_name([]).empty)
        empty = self.client(lambda request: page([]))
        self.assertIsNone(empty.nodes())

    def test_pagination_raises_on_unexpected_empty_page(self):
        def handler(request):
            offset = int(request.url.params['offset'])
            return page([] if offset else [{'id': 'first'}], 202)

        ap = self.client(handler)
        with self.assertRaises(ProtocolError):
            ap.photos(limit=202)

    def test_media_search_retains_pagination_past_drive_window(self):
        requests = []

        def handler(request):
            offset = int(request.url.params['offset'])
            limit = int(request.url.params['limit'])
            requests.append((offset, limit))
            return page([{'id': str(i)} for i in range(offset, offset + limit)], 10201)

        ap = self.client(handler)
        photos = ap.photos(offset=9999, limit=201)
        self.assertEqual(requests, [(9999, 200), (10199, 1)])
        self.assertEqual(len(photos), 201)

    def test_browser_headers_must_match_marketplace(self):
        with self.assertRaises(ValueError):
            self.client(self.no_request, request_headers={'origin': 'https://www.amazon.com'})

        def handler(request):
            self.assertEqual(request.headers['origin'], 'https://www.amazon.co.uk')
            self.assertEqual(request.headers['x-amzn-csrf'], 'offline-csrf')
            return page([])

        ap = self.client(handler, request_headers={
            'Origin': 'https://www.amazon.co.uk', 'X-Amzn-CSRF': 'offline-csrf',
        })
        ap.photos(limit=1)

    def test_folders_paginate_and_tree_handles_repeated_names(self):
        def handler(request):
            if request.url.path.endswith('/root/children'):
                if 'startToken' in request.url.params:
                    return page([{'id': 'other', 'name': 'Other', 'parents': ['root']}])
                return httpx.Response(200, json={
                    'data': [{'id': 'outer', 'name': 'Same', 'parents': ['root']}],
                    'nextToken': 'next',
                })
            if request.url.path.endswith('/outer/children'):
                return page([{'id': 'inner', 'name': 'Same', 'parents': ['outer']}])
            return page([])

        ap = self.client(handler)
        self.assertEqual(ap.find_path('Same/Same')['id'], 'inner')
        self.assertEqual(ap.find_path('Other')['id'], 'other')
        self.assertEqual(len(ap.folders), 3)
        self.assertIsNot(ap.build_tree([]), ap.tree)
        self.assertEqual(ap.build_tree([])['children'], [])

    def test_folder_failure_propagates_from_constructor_and_closes_transport(self):
        class CountingTransport(httpx.MockTransport):
            closed = False

            def close(self):
                self.closed = True
                super().close()

        sync = CountingTransport(lambda request: page([ROOT]))
        async_ = httpx.MockTransport(lambda request: httpx.Response(401))
        with self.assertRaises(AuthenticationError):
            AmazonPhotos(UK_COOKIES, init_db=False, transport=sync, async_transport=async_)
        self.assertTrue(sync.closed)

    def test_directory_upload_keeps_exact_relative_parent(self):
        base = self.work / 'same'
        nested = base / 'same'
        nested.mkdir(parents=True)
        (nested / 'one.jpg').write_bytes(MEDIA)
        put = []

        def handler(request):
            if request.url.host.startswith('content-'):
                put.append(request.url.params['parentNodeId'])
                return httpx.Response(201, json={'id': 'image'})
            if request.method == 'POST':
                parent = json.loads(request.content)['parents'][0]
                return httpx.Response(201, json={'id': f'{parent}-folder'})
            return page([])

        ap = self.client(handler)
        results = ap.upload(base, refresh=False)
        self.assertEqual(len(results), 1)
        self.assertEqual(put, ['root-folder-folder'])

    def test_folder_conflict_requires_matching_remote_folder(self):
        requests = []

        def handler(request):
            requests.append(request)
            if request.method == 'POST':
                return httpx.Response(409, json={'info': {'nodeId': 'duplicate'}})
            return httpx.Response(200, json={
                'id': 'duplicate', 'name': 'wrong-name', 'kind': 'FILE', 'parents': ['root'],
            })

        ap = self.client(handler)
        folder = self.work / 'folder'
        folder.mkdir()
        with self.assertRaises(APIError) as error:
            ap.create_folders(folder)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual([request.method for request in requests], ['POST', 'GET'])

    def test_upload_request_and_duplicate_on_second_client(self):
        file = self.image('bytes & name.jpg')
        for mode in ('cdproxy', 'v2'):
            with self.subTest(mode=mode):
                sent = []

                def handler(request):
                    if request.method == 'POST':
                        sent.append(request)
                        self.assertEqual(request.content, MEDIA)
                        self.assertEqual(request.url.params['name'], file.name)
                        self.assertEqual(request.url.params['parentNodeId'], 'root')
                        self.assertEqual(request.headers['content-length'], str(len(MEDIA)))
                        self.assertEqual(request.headers['x-amzn-sessionid'], 'offline-session')
                        self.assertIn('at-acbuk=offline-token', request.headers['cookie'])
                        if mode == 'cdproxy':
                            self.assertEqual(request.url.host, 'content-eu.drive.amazonaws.com')
                            self.assertEqual(request.url.params['kind'], 'FILE')
                        else:
                            self.assertEqual(request.url.host, 'upload-photos.amazon.co.uk')
                            self.assertEqual(request.headers['x-amzn-file-md5'], CHECKSUM)
                            self.assertEqual(request.url.params['fileSize'], str(len(MEDIA)))
                        return httpx.Response(201, json={
                            'id': 'created', 'contentProperties': {'md5': CHECKSUM},
                        })
                    self.assertIn(CHECKSUM, request.url.params['filters'])
                    self.assertIn('status:AVAILABLE', request.url.params['filters'])
                    return page([{'id': 'created', 'contentProperties': {'md5': CHECKSUM}}] if sent else [])

                with self.client(handler, upload_api=mode) as ap:
                    result = ap.upload(file, refresh=False, chunk_size=3)
                    self.assertEqual(result[0].json()['id'], 'created')
                with self.client(handler, upload_api=mode) as ap:
                    self.assertEqual(ap.upload(file, refresh=False), [])
                self.assertEqual(len(sent), 1)

    def test_upload_preserves_node_id_variant_and_normalizes_cache(self):
        ap = self.client(lambda request: httpx.Response(201, json={'nodeId': 'created'}))
        results = ap.upload(self.image(), md5s=set())
        self.assertEqual(results[0].json(), {'nodeId': 'created'})
        self.assertEqual(ap.db.id.tolist(), ['created'])

    def test_upload_429_reopens_stream(self):
        sent = []

        def handler(request):
            sent.append(request.content)
            if len(sent) == 1:
                return httpx.Response(429, headers={'Retry-After': '0'})
            return httpx.Response(201, json={'id': 'uploaded'})

        ap = self.client(handler)
        with patch('amazon_photos._http.random.random', return_value=0):
            ap.upload(self.image(), md5s=set(), refresh=False)
        self.assertEqual(sent, [MEDIA, MEDIA])

    def test_upload_errors_and_ambiguous_results_never_silently_succeed(self):
        cases = [
            (lambda: httpx.Response(401), AuthenticationError),
            (lambda: httpx.Response(400), APIError),
            (lambda: httpx.Response(403), APIError),
            (lambda: httpx.Response(503), UploadUncertainError),
            (lambda: httpx.Response(201, json={}), UploadUncertainError),
        ]
        for response, expected in cases:
            with self.subTest(expected=expected):
                posts = []

                def handler(request):
                    posts.append(request)
                    return response()

                ap = self.client(handler)
                with self.assertRaises(expected):
                    ap.upload(self.image(), md5s=set(), refresh=False)
                self.assertEqual(len(posts), 1)

    def test_upload_409_requires_remote_checksum(self):
        for checksum in (CHECKSUM, 'different'):
            sent = []

            def handler(request):
                sent.append(request.method)
                if request.method == 'POST':
                    return httpx.Response(409, json={'info': {'nodeId': 'existing'}})
                if request.url.path.endswith('/existing'):
                    return httpx.Response(200, json={
                        'id': 'existing', 'contentProperties': {'md5': checksum},
                    })
                return page([])

            ap = self.client(handler)
            if checksum == CHECKSUM:
                self.assertEqual(ap.upload(self.image(), md5s=set(), refresh=False), [])
            else:
                with self.assertRaises(APIError) as error:
                    ap.upload(self.image(), md5s=set(), refresh=False)
                self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(sent.count('POST'), 1)

    def test_upload_does_not_replay_after_read_timeout(self):
        attempts = []

        def handler(request):
            attempts.append(request.content)
            raise httpx.ReadTimeout('private signed request URL')

        ap = self.client(handler)
        with self.assertRaises(UploadUncertainError) as caught:
            ap.upload(self.image(), md5s=set(), refresh=False)
        self.assertEqual(attempts, [MEDIA])
        self.assertIsInstance(caught.exception.__cause__, TransportError)
        self.assertNotIn('private signed request URL', str(caught.exception.__cause__))

    def test_download_redirect_strips_headers_and_sanitizes_filename(self):
        def handler(request):
            if request.url.host == 'www.amazon.co.uk':
                self.assertIn('cookie', request.headers)
                return httpx.Response(302, headers={'Location': 'https://cdn.example.test/file?signature=opaque'})
            self.assertNotIn('cookie', request.headers)
            self.assertNotIn('x-amzn-sessionid', request.headers)
            self.assertNotIn('x-custom-secret', request.headers)
            return httpx.Response(200, content=MEDIA, headers={
                'Content-Disposition': "attachment; filename*=UTF-8''..%2Ffile%3D1.jpg",
            })

        ap = self.client(handler)
        result = ap.download(['node'], out=self.work, headers={'x-custom-secret': 'offline'})
        self.assertEqual(result['nodes'], ['node'])
        self.assertEqual((self.work / 'node_.._file=1.jpg').read_bytes(), MEDIA)

    def test_download_failure_keeps_previous_file_and_cleans_temporary_file(self):
        target = self.work / 'node_file.jpg'
        target.write_bytes(b'old download')

        def handler(request):
            return httpx.Response(200, content=b'short', headers={
                'content-disposition': 'attachment; filename="file.jpg"', 'content-length': '100',
            })

        ap = self.client(handler)
        with patch('amazon_photos._http.random.random', return_value=0):
            with self.assertRaises(TransportError):
                ap.download(['node'], out=self.work)
        self.assertEqual(target.read_bytes(), b'old download')
        self.assertEqual(list(self.work.iterdir()), [target])

    def test_download_optional_checksum_prevents_publishing_wrong_bytes(self):
        def handler(request):
            if request.url.path.endswith('/contentRedirection'):
                return httpx.Response(200, content=b'corrupt', headers={
                    'Content-Disposition': 'attachment; filename=file.jpg',
                })
            return httpx.Response(200, json={'id': 'node', 'contentProperties': {'md5': CHECKSUM}})

        ap = self.client(handler)
        with self.assertRaises(ProtocolError):
            ap.download(['node'], out=self.work, verify_checksum=True)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_failed_cdn_signature_is_not_classified_as_expired_browser_auth(self):
        def handler(request):
            if request.url.host == 'www.amazon.co.uk':
                return httpx.Response(302, headers={'Location': 'https://cdn.example.test/file'})
            return httpx.Response(401)

        ap = self.client(handler)
        with self.assertRaises(APIError) as caught:
            ap.download(['node'], out=self.work)
        self.assertEqual(caught.exception.status_code, 401)

    def test_mutations_propagate_errors_and_bind_single_node_arguments(self):
        settings = []

        def handler(request):
            if request.url.path.endswith('/trash'):
                return httpx.Response(401)
            body = json.loads(request.content)
            settings.append(body['settings'])
            self.assertEqual(request.url.path, '/drive/v1/nodes/photo')
            return httpx.Response(200, json={'id': 'photo'})

        ap = self.client(handler)
        for method in (ap.favorite, ap.unfavorite, ap.hide, ap.unhide):
            self.assertEqual(method(['photo'])[0]['id'], 'photo')
        self.assertEqual(settings, [{'favorite': True}, {'favorite': False}, {'hidden': True}, {'hidden': False}])
        with self.assertRaises(AuthenticationError):
            ap.restore(['photo'])

    def test_aggregation_output_respects_directory(self):
        ap = self.client(lambda request: httpx.Response(200, json={'aggregations': {'test': []}}))
        self.assertEqual(ap.aggregations('things', out=self.work), {'test': []})
        self.assertEqual(json.loads((self.work / 'things.json').read_bytes()), {'test': []})

    def test_cache_merges_legacy_schema_and_failed_write_is_atomic(self):
        path = self.work / 'old.parquet'
        pd.DataFrame([{'id': 'old', 'md5': 'old-md5'}]).to_parquet(path)
        ap = self.client(self.no_request, db_path=path)
        frame = ap._merge_db(pd.DataFrame([{'id': 'new', 'contentProperties.md5': CHECKSUM}]))
        self.assertEqual(dict(zip(frame.id, frame.md5)), {'new': CHECKSUM, 'old': 'old-md5'})
        before = path.read_bytes()
        with patch.object(pd.DataFrame, 'to_parquet', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                ap._merge_db(pd.DataFrame([{'id': 'other'}]))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.work.iterdir()), [path])

    def test_cache_refresh_uses_real_calendar_boundaries(self):
        ap = self.client(self.no_request)
        ap.query = unittest.mock.Mock(side_effect=[pd.DataFrame([{'id': 'yesterday'}]), pd.DataFrame(), pd.DataFrame()])
        with patch('amazon_photos._api.date') as clock:
            clock.today.return_value = date(2026, 1, 1)
            frame = ap.refresh_db()
        self.assertEqual(frame.id.tolist(), ['yesterday'])
        filters = [item.args[0] for item in ap.query.call_args_list]
        self.assertIn('timeYear:(2025) AND timeMonth:(12) AND timeDay:(31)', filters[0])
        self.assertIn('timeYear:(2026) AND timeMonth:(1) AND timeDay:(2)', filters[2])

    def test_process_joins_failed_siblings_and_honors_concurrency(self):
        ap = self.client(self.no_request)

        async def exercise():
            started = asyncio.Event()
            stopped = asyncio.Event()
            active = 0
            peak = 0

            async def work(client, sem):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                if active == 2:
                    started.set()
                try:
                    await asyncio.Future()
                finally:
                    active -= 1
                    stopped.set()

            async def fail(client, sem):
                nonlocal active
                active += 1
                if active == 2:
                    started.set()
                await started.wait()
                raise ProtocolError('test failure')

            with self.assertRaises(ProtocolError):
                await asyncio.wait_for(ap.process(iter([work, fail, work]), max_connections=2), 1)
            self.assertTrue(stopped.is_set())
            self.assertLessEqual(peak, 2)

        asyncio.run(exercise())


if __name__ == '__main__':
    unittest.main()
