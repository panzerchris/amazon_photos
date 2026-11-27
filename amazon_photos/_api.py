import asyncio
import math
import os
import re
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from email.message import Message
from functools import partial
from hashlib import md5
from logging import getLogger
from mimetypes import guess_type
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Generator

import aiofiles
import httpx
import orjson
import pandas as pd
from httpx import AsyncClient, Client, Limits, Response
from tqdm import tqdm

from ._constants import (
    DEFAULT_USER_AGENT, EUROPEAN_TLD_MAP, MAX_LIMIT, MAX_NODES, MAX_PURGE_BATCH,
    MAX_TRASH_BATCH, NORTH_AMERICA_TLD_MAP, Red, Reset, TLD_EXCEPTIONS,
)
from ._helpers import format_nodes
from ._http import (
    APIError, AuthenticationError, ProtocolError, TransportError, UploadUncertainError,
    check_response, cookie_marketplace, is_retryable, normalize_tld, response_json, retry_delay,
)

logger = getLogger(__name__)


class AmazonPhotos:
    def __init__(
        self, cookies: Mapping[str, str], db_path: str | Path | None = 'ap.parquet', tmp: str = '', *,
        tld: str | None = None, init_db: bool = True, init_folders: bool = True,
        timeout: float | httpx.Timeout = 60, user_agent: str | None = None,
        upload_api: str = 'cdproxy', upload_override: str | None = None, trust_env: bool = False,
        request_headers: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        async_transport: httpx.AsyncBaseTransport | None = None, **kwargs,
    ):
        if not isinstance(cookies, Mapping) or any(
            not isinstance(name, str) or not isinstance(value, str) for name, value in cookies.items()
        ):
            raise AuthenticationError("Supply browser cookies as a mapping of names to string values.")
        self.n_threads = os.cpu_count() or 1
        self.tld = normalize_tld(tld) if tld else self.determine_tld(cookies)
        domain = TLD_EXCEPTIONS.get(self.tld, self.tld)
        self.drive_url = self.determine_drive(kwargs.pop('drive_override', None)).rstrip('/')
        self.cdproxy_url = self.determine_cdproxy(kwargs.pop('cdproxy_override', None))
        if upload_api not in {'cdproxy', 'v2'}:
            raise ValueError("upload_api must be cdproxy or v2.")
        self.upload_api = upload_api
        default_upload = self.cdproxy_url if upload_api == 'cdproxy' else f'https://upload-photos.amazon.{domain}/v2/upload'
        self.upload_url = upload_override or default_upload
        self.thumb_url = f'https://thumbnails-photos.amazon.{domain}/v1/thumbnail'
        self.base_params = {
            'asset': 'ALL', 'tempLink': 'false', 'resourceVersion': 'V2', 'ContentType': 'JSON',
        }
        self.limits = kwargs.pop('limits', Limits(max_connections=16, max_keepalive_connections=8))
        self._timeout = timeout
        self._trust_env = trust_env
        self._async_transport = async_transport
        self._origins = set()
        jar = httpx.Cookies()
        for endpoint in (self.drive_url, self.upload_url):
            if endpoint is None:
                continue
            url = httpx.URL(endpoint)
            if url.scheme != 'https' or not url.host or url.userinfo or url.query or url.fragment:
                raise ValueError("Amazon Photos endpoints must be HTTPS URLs without credentials, queries or fragments.")
            self._origins.add((url.host, url.port))
            for name, value in cookies.items():
                region = cookie_marketplace(name)
                if value and (region is None or region == self.tld):
                    if region or name == 'session_id':
                        name = name.replace('_', '-')
                    # Prefer an actual hyphenated cookie over a legacy alias.
                    jar.set(name, cookies.get(name) or value, domain=url.host)
        if not (cookies.get('session-id') or cookies.get('session_id')):
            raise AuthenticationError("Missing session-id. Sign in to Amazon Photos and supply browser cookies.")

        self.tmp = Path(tmp).expanduser() if tmp else None
        if self.tmp is not None:
            self.tmp.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db_path = Path(db_path).expanduser() if db_path is not None else None
        headers = httpx.Headers(request_headers)
        allowed = {'accept', 'accept-language', 'origin', 'referer',
                   'x-amzn-csrf', 'x-amz-csrf-token', 'csrf-token'}
        if set(headers) - allowed:
            raise ValueError("request_headers accepts browser origin, language and CSRF headers only.")
        for name in {'origin', 'referer'} & set(headers):
            source = httpx.URL(headers[name])
            if source.scheme != 'https' or source.host != f'www.amazon.{domain}' or source.port or source.userinfo:
                raise ValueError("Browser Origin and Referer must match the selected marketplace.")
        headers['user-agent'] = user_agent or DEFAULT_USER_AGENT
        self._cache_lock = RLock()
        self._upload_lock = RLock()
        self.client = Client(
            timeout=timeout, limits=self.limits, transport=transport, trust_env=trust_env,
            headers=headers, cookies=jar.jar,
            event_hooks={'request': [self._prepare_request], 'response': [self._check_response]},
        )
        try:
            self.root = self.get_root()
            self.folders = self.get_folders() if init_folders else []
            self.db = self.load_db(**kwargs) if init_db else pd.DataFrame()
            self.tree = self.build_tree()
            self._folders_loaded = init_folders
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def close(self):
        """Close the synchronous HTTP client."""
        self.client.close()

    def determine_tld(self, cookies: Mapping[str, str]) -> str:
        """Infer the marketplace from recognized authentication cookies."""
        markets = {cookie_marketplace(name) for name, value in cookies.items() if value} - {None}
        if len(markets) == 1:
            return markets.pop()
        if markets:
            raise ValueError("Cookies contain multiple Amazon marketplaces. Specify tld explicitly.")
        raise AuthenticationError("Could not infer the Amazon marketplace. Specify tld and supply browser cookies.")

    def determine_drive(self, override: str | None = None) -> str:
        tld = TLD_EXCEPTIONS.get(self.tld, self.tld)
        return override or f'https://www.amazon.{tld}/drive/v1'

    def determine_cdproxy(self, override: str | None = None) -> str | None:
        if override:
            return override
        if self.tld in NORTH_AMERICA_TLD_MAP:
            region = 'na'
        elif self.tld in EUROPEAN_TLD_MAP:
            region = 'eu'
        elif self.tld == 'jp':
            region = 'jp'
        else:
            return None
        return f'https://content-{region}.drive.amazonaws.com/cdproxy/nodes'

    def _prepare_request(self, request: httpx.Request) -> None:
        if request.url.scheme != 'https' or request.url.userinfo:
            raise ProtocolError("Amazon Photos requests and redirects require HTTPS without URL credentials.")
        if (request.url.host, request.url.port) not in self._origins:
            if not request.extensions.get('photos_download') or request.method != 'GET':
                raise ProtocolError("Amazon Photos refused to send an API request to an unconfigured endpoint.")
            # Redirected content may be signed for an external CDN. Do not forward account headers.
            request.extensions['photos_untrusted'] = True
            for name in list(request.headers):
                if name not in {'host', 'accept', 'accept-encoding', 'connection', 'user-agent'}:
                    del request.headers[name]
            return
        request.extensions.pop('photos_untrusted', None)
        for cookie in request.headers.get('cookie', '').split('; '):
            if cookie.startswith('session-id='):
                request.headers['x-amzn-sessionid'] = cookie.partition('=')[2]
                break
        else:
            request.headers.pop('x-amzn-sessionid', None)

    @staticmethod
    def _check_response(response):
        if response.status_code == 403 or (
            response.is_success and 'text/html' in response.headers.get('content-type', '').lower()
            and not response.request.extensions.get('photos_download')
        ):
            response.read()
        check_response(response)

    def _async_client(self, **kwargs):
        async def prepare(request):
            self._prepare_request(request)

        async def check(response):
            if response.status_code == 403 or (
                response.is_success and 'text/html' in response.headers.get('content-type', '').lower()
                and not response.request.extensions.get('photos_download')
            ):
                await response.aread()
            check_response(response)

        headers = httpx.Headers(self.client.headers)
        headers.update(kwargs.pop('headers', {}))
        options = {
            'timeout': self._timeout, 'limits': self.limits, 'trust_env': self._trust_env,
            'transport': self._async_transport, 'headers': headers,
        } | kwargs
        return AsyncClient(
            cookies=self.client.cookies.jar,
            event_hooks={'request': [prepare], 'response': [check]}, **options,
        )

    async def process(self, fns: Generator, max_connections: int | None = None, **kwargs):
        """Run async request functions with bounded concurrency, preserving result order."""
        desc = kwargs.pop('desc', None)
        progress = kwargs.pop('progress', None)
        if max_connections is None:
            max_connections = self.limits.max_connections or 16
        if max_connections < 1:
            raise ValueError("max_connections must be positive.")
        sem = asyncio.Semaphore(max_connections)
        pending = enumerate(fns)
        results = []
        async with self._async_client(**kwargs) as client:
            with tqdm(desc=desc, disable=not progress if progress is not None else None) as bar:
                async def worker():
                    for index, fn in pending:
                        results.append(None)
                        results[index] = await fn(client=client, sem=sem)
                        bar.update()
                tasks = [asyncio.create_task(worker()) for _ in range(max_connections)]
                try:
                    await asyncio.gather(*tasks)
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        return results

    def _save_snapshot(self, response):
        if self.tmp is not None and response.is_success:
            with NamedTemporaryFile(dir=self.tmp, delete=False) as snapshot:
                snapshot.write(response.content)

    async def async_backoff(
        self, fn, sem: asyncio.Semaphore, *args, m: int = 60, b: int = 2,
        max_retries: int = 3, read: bool | None = None, snapshot: bool = True, **kwargs,
    ) -> Response:
        """Retry transient reads, rate limits, and failures before a request can be sent."""
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative.")
        if read is None:
            read = getattr(fn, '__name__', '') in {'get', 'head'}
        for attempt in range(max_retries + 1):
            try:
                async with sem:
                    response = await fn(*args, **kwargs)
                    check_response(response)
            except (APIError, httpx.RequestError) as error:
                if attempt == max_retries or not is_retryable(error, read):
                    if isinstance(error, httpx.RequestError):
                        raise TransportError(error) from None
                    raise
                delay = retry_delay(error, attempt, m, b)
                logger.debug("Retrying Amazon Photos request in %.2fs", delay)
                await asyncio.sleep(delay)
            else:
                if snapshot and self.tmp is not None:
                    await asyncio.to_thread(self._save_snapshot, response)
                return response

    def backoff(
        self, fn, *args, m: int = 60, b: int = 2, max_retries: int = 3,
        read: bool | None = None, **kwargs,
    ) -> Response:
        """Synchronous counterpart of async_backoff."""
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative.")
        if read is None:
            read = getattr(fn, '__name__', '') in {'get', 'head'}
        for attempt in range(max_retries + 1):
            try:
                response = fn(*args, **kwargs)
                check_response(response)
            except (APIError, httpx.RequestError) as error:
                if attempt == max_retries or not is_retryable(error, read):
                    if isinstance(error, httpx.RequestError):
                        raise TransportError(error) from None
                    raise
                delay = retry_delay(error, attempt, m, b)
                logger.debug("Retrying Amazon Photos request in %.2fs", delay)
                time.sleep(delay)
            else:
                self._save_snapshot(response)
                return response

    def usage(self) -> dict | pd.DataFrame:
        """
        Get Amazon Photos current memory usage stats

        @param as_df: return as pandas DataFrame
        @return: dict or pd.DataFrame
        """
        r = self.backoff(
            self.client.get,
            f'{self.drive_url}/account/usage',
            params=self.base_params,
        )
        data = response_json(r)
        data.pop('lastCalculated', None)
        return pd.DataFrame(
            {
                'type': k,
                'billable_bytes': int(v['billable']['bytes']),
                'billable_count': int(v['billable']['count']),
                'total_bytes': int(v['total']['bytes']),
                'total_count': int(v['total']['count']),
            } for k, v in data.items()
        )

    async def _page(self, client, sem, endpoint, params):
        response = await self.async_backoff(client.get, sem, endpoint, params=params)
        return response_json(response)

    @staticmethod
    def _page_data(payload):
        data = payload.get('data')
        if not isinstance(data, list) or any(not isinstance(node, dict) for node in data):
            raise ProtocolError("Expected an Amazon Photos list of nodes.")
        return data

    def _paged_nodes(self, endpoint, params, offset, limit, *, window=None, **kwargs):
        if type(offset) is not int or offset < 0 or (
            limit != math.inf and (type(limit) is not int or limit < 0)
        ):
            raise ValueError("offset and limit must be nonnegative integers.")
        available = limit if window is None else min(limit, max(0, window - offset))
        if available == 0:
            return pd.DataFrame()

        def request_params(start, size):
            return self.base_params | params | {'offset': start, 'limit': size}

        size = min(MAX_LIMIT, available)
        payload = response_json(self.backoff(
            self.client.get, endpoint, params=request_params(offset, size),
        ))
        rows = self._page_data(payload)
        count = payload.get('count')
        if not isinstance(count, int) or count < 0:
            raise ProtocolError("Expected an Amazon Photos node count.")
        end = min(count, offset + available)
        if window is not None and count > window and limit > window - offset:
            logger.warning("Amazon Photos caps this listing at %d nodes. Use narrower filters for complete results.", window)
        if offset + size < end:
            fns = (
                partial(self._page, endpoint=endpoint, params=request_params(start, min(MAX_LIMIT, end - start)))
                for start in range(offset + size, end, MAX_LIMIT)
            )
            for page in asyncio.run(self.process(fns, desc='Listing nodes', **kwargs)):
                rows.extend(self._page_data(page))
        return format_nodes(pd.json_normalize(rows if available == math.inf else rows[:available]))

    async def q(self, client: AsyncClient, sem: asyncio.Semaphore, filters: str, offset: int, limit: int = MAX_LIMIT) -> dict:
        """Fetch a single search page."""
        return await self._page(client, sem, f'{self.drive_url}/search', self.base_params | {
            'limit': limit, 'offset': offset, 'filters': filters,
            'lowResThumbnail': 'true', 'searchContext': 'customer', 'sort': "['createdDate DESC']",
        })

    def query(self, filters: str = "type:(PHOTOS OR VIDEOS)", offset: int = 0, limit: int = math.inf, **kwargs) -> pd.DataFrame:
        """Search media using offset pagination."""
        return self._paged_nodes(f'{self.drive_url}/search', {
            'filters': filters, 'lowResThumbnail': 'true', 'searchContext': 'customer',
            'sort': "['createdDate DESC']",
        }, offset, limit, **kwargs)

    def photos(self, **kwargs) -> list[dict] | pd.DataFrame:
        """Convenience method to get all photos"""
        return self.query('type:(PHOTOS)', **kwargs)

    def videos(self, **kwargs) -> list[dict] | pd.DataFrame:
        """Convenience method to get all videos"""
        return self.query('type:(VIDEOS)', **kwargs)

    @staticmethod
    def _md5(path):
        digest = md5(usedforsecurity=False)
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
        return path, digest.hexdigest()

    @staticmethod
    def _files(path):
        if path.is_file():
            return [path]
        if not path.is_dir():
            raise FileNotFoundError(path)
        return sorted(p for p in path.rglob('*') if p.is_file() and not p.is_symlink())

    def dedup_files(self, path: str | Path, md5s: set[str], max_workers: int | None = None) -> list[Path]:
        """Return files whose MD5 is absent from the provided set."""
        files = self._files(Path(path))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            return [file for file, checksum in pool.map(self._md5, files) if checksum not in md5s]

    def upload(
        self, path: str | Path, md5s: set[str] | None = None, refresh: bool = True,
        chunk_size: int = 64 * 1024, *, parent_id: str | None = None, **kwargs,
    ) -> list[Response]:
        """Upload a file or directory and return the successful HTTP responses.

        A single file goes to the Photos root unless parent_id is supplied. A
        directory keeps its name and structure. Content already present is skipped.
        Directory uploads can partially succeed before an exception is raised.
        """
        path = Path(os.path.abspath(Path(path).expanduser()))
        if parent_id is not None and not path.is_file():
            raise ValueError("parent_id requires a single file.")
        if not self.upload_url:
            raise ValueError("No CDProxy endpoint for this marketplace. Set an upload_override or cdproxy_override.")
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive.")
        max_workers = kwargs.pop('max_workers', min(8, self.n_threads))
        skip_dedup = kwargs.pop('skip_dedup', False)
        with self._upload_lock:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                files = list(pool.map(self._md5, self._files(path)))
            selected = []
            seen = set(md5s or ())
            for file, checksum in files:
                if not skip_dedup and (checksum in seen or (md5s is None and self._find_by_md5(checksum))):
                    continue
                selected.append((file, checksum))
                seen.add(checksum)
            if not selected:
                return []

            if path.is_file():
                folder_map = {path.parent: parent_id or self.root['id']}
            else:
                remote_folders, _ = self.create_folders(path)
                folder_map = {
                    path.parent / Path(relative): folder_id for relative, folder_id in remote_folders.items()
                }

            async def post(client, sem, file, checksum, destination):
                size = file.stat().st_size

                headers = {'Content-Length': str(size)}
                params = {'name': file.name, 'parentNodeId': destination}
                if self.upload_api == 'v2':
                    headers['x-amzn-file-md5'] = checksum
                    params.update(fileSize=size, conflictResolution='RENAME')
                else:
                    params['kind'] = 'FILE'
                    headers['content-type'] = guess_type(file.name)[0] or 'application/octet-stream'

                async def send():
                    async with aiofiles.open(file, 'rb') as source:
                        async def body():
                            digest = md5(usedforsecurity=False)
                            while chunk := await source.read(chunk_size):
                                digest.update(chunk)
                                yield chunk
                            if digest.hexdigest() != checksum:
                                raise UploadUncertainError(f"File changed while uploading {file.name}. Check Amazon Photos before retrying.")

                        return await client.post(
                            self.upload_url,
                            content=body(), headers=headers, params=params,
                            extensions={'photos_allow_conflict': True},
                            follow_redirects=False,
                        )

                try:
                    response = await self.async_backoff(send, sem, read=False, snapshot=False)
                    if response.status_code == 409:
                        if await asyncio.to_thread(self._verify_conflict, response, checksum):
                            return None
                        raise APIError(409, 'POST')
                    node = response_json(response)
                    if not isinstance(node.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]+', node['id']):
                        raise ProtocolError("Amazon Photos did not return an uploaded node ID.")
                    remote_md5 = (node.get('contentProperties') or {}).get('md5')
                    if remote_md5 and (not isinstance(remote_md5, str) or remote_md5.lower() != checksum):
                        raise ProtocolError("Amazon Photos reported a different checksum for the uploaded node.")
                except (TransportError, ProtocolError, APIError) as error:
                    if isinstance(error, APIError) and error.status_code < 500 and error.status_code != 408:
                        raise
                    if isinstance(error, TransportError) and error.before_request:
                        raise
                    raise UploadUncertainError(
                        f"Amazon may have received {file.name}. Verify the remote copy by checksum before retrying."
                    ) from error
                return response

            fns = (
                partial(post, file=file, checksum=checksum, destination=folder_map[file.parent])
                for file, checksum in selected
            )
            responses = asyncio.run(self.process(fns, desc='Uploading files', **kwargs))
            responses = [response for response in responses if response is not None]
            if refresh and responses:
                self._merge_db(pd.json_normalize([response_json(response) for response in responses]))
            return responses

    def _verify_conflict(self, response, checksum):
        try:
            info = response_json(response).get('info') or {}
        except ProtocolError:
            info = {}
        if isinstance(info, dict):
            candidates = info.get('nodeIds') or [info.get('nodeId')]
            if isinstance(candidates, list):
                for node_id in candidates:
                    if isinstance(node_id, str) and re.fullmatch(r'[A-Za-z0-9_-]+', node_id):
                        try:
                            node = response_json(self.backoff(
                                self.client.get, f'{self.drive_url}/nodes/{node_id}', params=self.base_params,
                            ))
                        except APIError as error:
                            if error.status_code != 404:
                                raise
                            continue
                        if (node.get('status', 'AVAILABLE') == 'AVAILABLE'
                                and str((node.get('contentProperties') or {}).get('md5')).lower() == checksum):
                            return True
        return self._find_by_md5(checksum)

    def _find_by_md5(self, checksum: str) -> bool:
        matches = self.nodes(filters=[f'contentProperties.md5:{checksum}', 'status:AVAILABLE'])
        if matches is None or not {'id', 'md5'}.issubset(matches.columns):
            return False
        valid = matches['id'].notna() & matches['md5'].astype('string').str.lower().eq(checksum)
        if 'status' in matches:
            valid &= matches['status'].eq('AVAILABLE')
        return bool(valid.any())

    def download(
        self, node_ids: list[str] | pd.Series, out: str = 'media',
        chunk_size: int | None = None, *, verify_checksum: bool = False, **kwargs,
    ) -> dict:
        """Download nodes atomically, raising on any failed transfer."""
        node_ids = list(node_ids)
        if any(not re.fullmatch(r'[A-Za-z0-9_-]+', node) for node in node_ids):
            raise ValueError("Download requires valid Amazon Photos node IDs.")
        if chunk_size is not None and chunk_size <= 0:
            raise ValueError("chunk_size must be positive.")
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        params = {'querySuffix': '?download=true', 'ownerId': self.root['ownerId']}

        async def get(client, sem, node):
            expected = None
            if verify_checksum:
                metadata = response_json(await self.async_backoff(
                    client.get, sem, f'{self.drive_url}/nodes/{node}', params=self.base_params,
                ))
                expected = (metadata.get('contentProperties') or {}).get('md5')
                if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-fA-F]{32}', expected):
                    raise ProtocolError("Amazon Photos did not supply a checksum for the requested node.")

            async def transfer():
                temporary = None
                try:
                    url = f'{self.drive_url}/nodes/{node}/contentRedirection'
                    async with client.stream(
                        'GET', url, params=params, follow_redirects=True,
                        extensions={'photos_download': True},
                    ) as response:
                        if 'text/html' in response.headers.get('content-type', '').lower():
                            raise ProtocolError("Expected media from the content endpoint. Check your browser session.")
                        message = Message()
                        message['Content-Disposition'] = response.headers.get('content-disposition', '')
                        name = message.get_filename() or node
                        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).rstrip('. ') or node
                        target = out / f'{node}_{name}'
                        with NamedTemporaryFile(dir=out, prefix='.amazon-photos-', delete=False) as file:
                            temporary = Path(file.name)
                        digest = md5(usedforsecurity=False) if expected else None
                        async with aiofiles.open(temporary, 'wb') as file:
                            async for chunk in response.aiter_bytes(chunk_size):
                                if digest is not None:
                                    digest.update(chunk)
                                await file.write(chunk)
                        if digest is not None and digest.hexdigest() != expected.lower():
                            raise ProtocolError("Downloaded bytes do not match the Amazon Photos checksum.")
                        if not response.headers.get('content-encoding'):
                            length = response.headers.get('content-length')
                            if length and length.isdecimal() and temporary.stat().st_size != int(length):
                                raise httpx.ReadError("Incomplete media response.")
                        temporary.replace(target)
                    return response
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)

            await self.async_backoff(transfer, sem, read=True, snapshot=False)

        fns = (partial(get, node=node) for node in dict.fromkeys(node_ids))
        asyncio.run(self.process(fns, desc='Downloading media', **kwargs))
        return {'timestamp': time.time_ns(), 'nodes': node_ids}

    def trashed(self, filters: str = '', offset: int = 0, limit: int = MAX_LIMIT, sort: str = "['modifiedDate DESC']", **kwargs) -> pd.DataFrame:
        """List trashed nodes, up to the requested total limit."""
        return self._paged_nodes(f'{self.drive_url}/trash', {
            'sort': sort, 'filters': filters or 'kind:(FILE* OR FOLDER*) AND status:(TRASH*)',
        }, offset, limit, window=MAX_NODES, **kwargs)

    def trash(self, node_ids: list[str] | pd.Series, filters: str = '', **kwargs) -> list[dict]:
        """
        Move nodes or entire folders to trash bin

        @param node_ids: list of node ids to trash
        @return: the trash response
        """

        if isinstance(node_ids, pd.Series):
            node_ids = node_ids.tolist()

        async def patch(client: AsyncClient, sem: asyncio.Semaphore, ids: list[str]) -> Response:
            return await self.async_backoff(client.patch, sem,
                f'{self.drive_url}/trash',
                json={
                    'recurse': 'true',
                    'op': 'add',
                    'filters': filters,
                    'conflictResolution': 'RENAME',
                    'value': ids,
                    'resourceVersion': 'V2',
                    'ContentType': 'JSON',
                }
            )

        id_batches = [node_ids[i:i + MAX_TRASH_BATCH] for i in range(0, len(node_ids), MAX_TRASH_BATCH)]
        fns = (partial(patch, ids=ids) for ids in id_batches)
        return asyncio.run(self.process(fns, desc='Trashing files', **kwargs))

    def restore(self, node_ids: list[str] | pd.Series) -> Response:
        """
        Restore nodes from trash bin

        @param node_ids: list of node ids to restore
        @return: the restore response
        """

        if isinstance(node_ids, pd.Series):
            node_ids = node_ids.tolist()

        return self.backoff(self.client.patch,
            f'{self.drive_url}/trash',
            json={
                'recurse': 'true',
                'op': 'remove',
                'conflictResolution': 'RENAME',
                'value': node_ids,
                'resourceVersion': 'V2',
                'ContentType': 'JSON',
            }
        )

    def delete(self, node_ids: list[str] | pd.Series, **kwargs) -> list[dict]:
        """
        Permanently delete nodes from Amazon Photos

        @param node_ids: list of node ids to delete
        @return: the delete response
        """

        if isinstance(node_ids, pd.Series):
            node_ids = node_ids.tolist()

        async def post(client: AsyncClient, sem: asyncio.Semaphore, ids: list[str]) -> Response:
            return await self.async_backoff(client.post, sem,
                f'{self.drive_url}/bulk/nodes/purge',
                json={
                    'recurse': 'false',
                    'nodeIds': ids,
                    'resourceVersion': 'V2',
                    'ContentType': 'JSON',
                }
            )

        id_batches = [node_ids[i:i + MAX_PURGE_BATCH] for i in range(0, len(node_ids), MAX_PURGE_BATCH)]
        fns = (partial(post, ids=ids) for ids in id_batches)
        return asyncio.run(self.process(fns, desc='Permanently deleting files', **kwargs))

    def aggregations(self, category: str, out: str = 'aggregations') -> dict:
        """
        Get Amazon Photos aggregations. E.g. see all identified people, locations, things, etc.

        @param category: category to get aggregations for. See readme for list of categories.
        @param out: path to save results
        @return: aggregations as a dict
        """
        if category == 'all':
            r = self.backoff(
                self.client.get,
                f'{self.drive_url}/search',
                params=self.base_params | {
                    'limit': 1,  # don't care about node info, just want aggregations
                    'lowResThumbnail': 'true',
                    'searchContext': 'all',
                    'groupByForTime': 'year',
                })
            data = response_json(r)['aggregations']
            if out:
                _out = Path(out)
                _out.mkdir(parents=True, exist_ok=True)
                [(_out / f'{k}.json').write_bytes(
                    orjson.dumps(data[k], option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)) for k in data]
            return data

        categories = {'allPeople', 'clusterId', 'familyMembers', 'favorite', 'location', 'people', 'things', 'time',
                      'type'}
        if category not in categories:
            raise ValueError(f'category must be one of {categories}')

        r = self.backoff(
            self.client.get,
            f'{self.drive_url}/search/aggregation',
            params={
                'aggregationContext': 'all',
                'category': category,
                'resourceVersion': 'V2',
                'ContentType': 'JSON',
            }
        )
        data = response_json(r)['aggregations']
        if out:
            directory = Path(out)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f'{category}.json').write_bytes(
                orjson.dumps(data, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
            )
        return data

    def favorite(self, node_ids: list[str] | pd.Series, **kwargs) -> list[dict]:
        """
        Add media to favorites

        @param node_ids: media node ids to add to favorites
        @return: operation response
        """

        if isinstance(node_ids, pd.Series):
            node_ids = node_ids.tolist()

        async def patch(client: AsyncClient, sem: asyncio.Semaphore, node_id: str) -> dict:
            r = await self.async_backoff(client.patch, sem, f'{self.drive_url}/nodes/{node_id}', json={
                'settings': {
                    'favorite': True,
                },
                'resourceVersion': 'V2',
                'ContentType': 'JSON',
            })
            return response_json(r)

        fns = (partial(patch, node_id=node_id) for node_id in node_ids)
        return asyncio.run(self.process(fns, desc='adding media to favorites', **kwargs))

    def unfavorite(self, node_ids: list[str] | pd.Series, **kwargs) -> list[dict]:
        """
        Remove media from favorites

        @param node_ids: media node ids to remove from favorites
        @return: operation response
        """

        if isinstance(node_ids, pd.Series):
            node_ids = node_ids.tolist()

        async def patch(client: AsyncClient, sem: asyncio.Semaphore, node_id: str) -> dict:
            r = await self.async_backoff(client.patch, sem, f'{self.drive_url}/nodes/{node_id}', json={
                'settings': {
                    'favorite': False,
                },
                'resourceVersion': 'V2',
                'ContentType': 'JSON',
            })
            return response_json(r)

        fns = (partial(patch, node_id=node_id) for node_id in node_ids)
        return asyncio.run(self.process(fns, desc='removing media from favorites', **kwargs))

    def create_album(self, album_name: str, node_ids: list[str] | pd.Series):
        """
        Create album

        @param album_name: name of album to create
        @param node_ids: media node ids to add to album
        @return: operation response
        """

        if isinstance(node_ids, pd.Series):
            node_ids = node_ids.tolist()

        r = self.backoff(self.client.post, f'{self.drive_url}/nodes', json={
            'kind': 'VISUAL_COLLECTION',
            'name': album_name,
            'resourceVersion': 'V2',
            'ContentType': 'JSON',
        })
        created_album = response_json(r)
        album_id = created_album['id']
        self.backoff(self.client.patch,
            f'{self.drive_url}/nodes/{album_id}/children',
            json={
                'op': 'add',
                'value': node_ids,
                'resourceVersion': 'V2',
                'ContentType': 'JSON',
            }
        )
        return created_album

    def rename_album(self, album_id: str, name: str) -> dict:
        """
        Rename album

        @param album_id: album node id
        @param name: new name
        @return: operation response
        """
        return response_json(self.backoff(self.client.patch,
            f'{self.drive_url}/nodes/{album_id}',
            json={
                'name': name,
                'resourceVersion': 'V2',
                'ContentType': 'JSON',
            }
        ))

    def add_to_album(self, album_id: str, node_ids: list[str] | pd.Series) -> dict:
        """
        Add media to album

        @param album_id: album node id
        @param node_ids: media node ids to add to album
        @return: operation response
        """

        if isinstance(node_ids, pd.Series):
            node_ids = node_ids.tolist()

        return response_json(self.backoff(self.client.patch,
            f'{self.drive_url}/nodes/{album_id}/children',
            json={
                'op': 'add',
                'value': node_ids,
                'resourceVersion': 'V2',
                'ContentType': 'JSON',
            }
        ))

    def remove_from_album(self, album_id: str, node_ids: list[str] | pd.Series):
        """
        Remove media from album

        @param album_id: album node id
        @param node_ids: media node ids to remove from album
        @return: operation response
        """

        if isinstance(node_ids, pd.Series):
            node_ids = node_ids.tolist()

        return response_json(self.backoff(self.client.patch,
            f'{self.drive_url}/nodes/{album_id}/children',
            json={
                'op': 'remove',
                'value': node_ids,
                'resourceVersion': 'V2',
                'ContentType': 'JSON',
            }
        ))

    def hide(self, node_ids: list[str] | pd.Series, **kwargs) -> list[dict]:
        """
        Hide media

        @param node_ids: node ids to hide
        @return: operation response
        """

        if isinstance(node_ids, pd.Series):
            node_ids = node_ids.tolist()

        async def patch(client: AsyncClient, sem: asyncio.Semaphore, node_id: str) -> dict:
            r = await self.async_backoff(client.patch, sem,
                f'{self.drive_url}/nodes/{node_id}',
                json={
                    'settings': {
                        'hidden': True,
                    },
                    'resourceVersion': 'V2',
                    'ContentType': 'JSON',
                }
            )
            return response_json(r)

        fns = (partial(patch, node_id=node_id) for node_id in node_ids)
        return asyncio.run(self.process(fns, desc='hiding media', **kwargs))

    def unhide(self, node_ids: list[str] | pd.Series, **kwargs) -> list[dict]:
        """
        Unhide media

        @param node_ids: node ids to unhide
        @return: operation response
        """

        if isinstance(node_ids, pd.Series):
            node_ids = node_ids.tolist()

        async def patch(client: AsyncClient, sem: asyncio.Semaphore, node_id: str) -> dict:
            r = await self.async_backoff(client.patch, sem,
                f'{self.drive_url}/nodes/{node_id}',
                json={
                    'settings': {
                        'hidden': False,
                    },
                    'resourceVersion': 'V2',
                    'ContentType': 'JSON',
                }
            )
            return response_json(r)

        fns = (partial(patch, node_id=node_id) for node_id in node_ids)
        return asyncio.run(self.process(fns, desc='unhiding media', **kwargs))

    def rename_cluster(self, cluster_id: str, name: str) -> dict:
        """
        Rename a cluster

        E.g. rename a person cluster from a default hash to a readable name

        @param cluster_id: target cluster id
        @param name: new name
        @return: operation response
        """
        return response_json(self.backoff(self.client.put, f'{self.drive_url}/cluster/name', json={
            'sourceCluster': cluster_id,
            'newName': name,
            'context': 'customer',
            'resourceVersion': 'V2',
            'ContentType': 'JSON',
        }))

    def combine_clusters(self, cluster_ids: list[str] | pd.Series, name: str) -> dict:
        """
        Combine clusters

        E.g. combine multiple person clusters into a single cluster

        @param cluster_ids: target cluster ids
        @param name: name of new combined cluster
        @return: operation response
        """

        if isinstance(cluster_ids, pd.Series):
            cluster_ids = cluster_ids.tolist()

        return response_json(self.backoff(self.client.post, f'{self.drive_url}/cluster', json={
            'clusterIds': cluster_ids,
            'newName': name,
            'context': 'customer',
            'resourceVersion': 'V2',
            'ContentType': 'JSON',
        }))

    def update_cluster_thumbnail(self, cluster_id: str, node_id: str) -> dict:
        """
        Update cluster thumbnail

        @param cluster_id: target cluster id
        @param node_id: node id to set as thumbnail
        @return: operation response
        """
        return response_json(self.backoff(self.client.put,
            f'{self.drive_url}/cluster/hero/{cluster_id}',
            json={
                'clusterId': cluster_id,
                'nodeId': node_id,
                'resourceVersion': 'V2',
                'ContentType': 'JSON',
            }))

    def add_to_family_vault(self, family_id: str, node_ids: list[str] | pd.Series) -> dict:
        """
        Add media to family vault

        @param family_id: family vault id
        @param node_ids: node ids to add to family vault
        @return: operation response
        """

        if isinstance(node_ids, pd.Series):
            node_ids = node_ids.tolist()

        return response_json(self.backoff(self.client.post,
            f'{self.drive_url}/familyArchive/', json={
                'nodesToAdd': node_ids,
                'nodesToRemove': [],
                'familyId': family_id,
                'resourceVersion': 'V2',
                'ContentType': 'JSON',
            }))

    def remove_from_family_vault(self, family_id: str, node_ids: list[str] | pd.Series) -> dict:
        """
        Remove media from family vault

        @param family_id: family vault id
        @param node_ids: node ids to remove from family vault
        @return: operation response
        """

        if isinstance(node_ids, pd.Series):
            node_ids = node_ids.tolist()

        return response_json(self.backoff(self.client.post,
            f'{self.drive_url}/familyArchive/', json={
                'nodesToAdd': [],
                'nodesToRemove': node_ids,
                'familyId': family_id,
                'resourceVersion': 'V2',
                'ContentType': 'JSON',
            }))

    def get_root(self) -> dict:
        """Read the account's root node and authenticate the session."""
        response = self.backoff(
            self.client.get, f'{self.drive_url}/nodes',
            params=self.base_params | {'filters': 'isRoot:true'},
        )
        roots = self._page_data(response_json(response))
        if not roots or not roots[0].get('id') or not roots[0].get('ownerId'):
            raise ProtocolError("Amazon Photos did not return an account root. Check the marketplace and browser session.")
        return roots[0]

    async def _folder_children(self, client, sem, node_id):
        endpoint = f'{self.drive_url}/nodes/{node_id}/children'
        params = self.base_params | {'filters': 'kind:FOLDER', 'limit': MAX_LIMIT}
        folders = []
        tokens = set()
        offset = 0
        token = None
        while True:
            page = await self._page(client, sem, endpoint, params | (
                {'startToken': token} if token else {'offset': offset}
            ))
            rows = self._page_data(page)
            folders.extend(rows)
            token = page.get('nextToken')
            if token:
                if not isinstance(token, str) or token in tokens:
                    raise ProtocolError("Amazon Photos repeated a folder pagination token.")
                tokens.add(token)
                continue
            offset += len(rows)
            if tokens or offset >= page.get('count', offset):
                return folders
            if not rows or offset >= MAX_NODES:
                raise ProtocolError("Amazon Photos folder listing ended before all folders were read.")
            params['limit'] = min(MAX_LIMIT, MAX_NODES - offset)

    def get_folders(self) -> list[dict]:
        """Traverse folders by breadth without swallowing failed requests."""
        async def traverse():
            visited = {self.root['id']}
            parents = [self.root['id']]
            folders = []
            while parents:
                fns = (partial(self._folder_children, node_id=node_id) for node_id in parents)
                parents = []
                for children in await self.process(fns, progress=False):
                    for child in children:
                        child_id = child.get('id')
                        if not child_id or 'name' not in child:
                            raise ProtocolError("Amazon Photos returned an incomplete folder node.")
                        if child_id not in visited:
                            visited.add(child_id)
                            parents.append(child_id)
                            folders.append(child)
            return folders

        return asyncio.run(traverse())

    def _load_folders(self):
        if not self._folders_loaded:
            self.folders = self.get_folders()
            self.tree = self.build_tree()
            self._folders_loaded = True

    def find_path(self, target: str, root: dict | None = None):
        """Find a slash-delimited path relative to the given node or the Photos root."""
        if root is None:
            self._load_folders()
            root = self.tree
        parts = [part for part in target.split('/') if part]
        root_path = list(filter(None, root['path']))
        if root_path and parts[:len(root_path)] == root_path:
            parts = parts[len(root_path):]
        for part in parts:
            root = next((child for child in root['children'] if child['name'] == part), None)
            if root is None:
                return None
        return root

    def build_tree(self, folders: list[dict] | None = None) -> dict:
        """Build a folder tree without mutating the source list."""
        folders = self.folders if folders is None else folders
        root = {'id': self.root['id'], 'name': '', 'children': [], 'path': {'': self.root['id']}}
        nodes = {root['id']: root}
        for item in folders:
            if item['id'] != root['id']:
                nodes[item['id']] = {'id': item['id'], 'name': item['name'], 'children': [], 'path': {}}
        for item in folders:
            parents = item.get('parents') or [root['id']]
            if item['id'] != root['id'] and parents[0] in nodes:
                nodes[parents[0]]['children'].append(nodes[item['id']])
        pending = [root]
        visited = {root['id']}
        while pending:
            parent = pending.pop()
            for child in parent['children']:
                if child['id'] in visited:
                    raise ProtocolError("Amazon Photos returned a cyclic or duplicated folder tree.")
                child['path'] = parent['path'] | {child['name']: child['id']}
                visited.add(child['id'])
                pending.append(child)
        if len(nodes) != len(visited):
            raise ProtocolError("The Amazon Photos folder tree has unreachable folders.")
        return root

    def print_tree(self, node: dict = None, show_id: bool = True, color: bool = True, indent: int = 4, prefix='', last=True):
        """
        Visualize your Amazon Photos folder structure

        @param node: optional root node to start from (default is root)
        @param show_id: optionally show node id in output
        @param color: optionally colorize output
        @param indent: optional indentation level
        @param prefix: optional prefix to add to output
        """
        if node is None:
            self._load_folders()
            node = self.tree
        conn = ('└── ' if last else '├── ') if node['name'] else ''
        name = node['name'] or '~'
        print(f"{prefix}{conn}{Red if color else ''}{name}{Reset if color else ''} {node['id'] if show_id else ''}")
        if node['name']:
            prefix += (indent * ' ') if last else '│' + (' ' * (indent - 1))
        else:
            prefix = ''
        for i, child in enumerate(node['children']):
            last_child = i == len(node['children']) - 1
            self.print_tree(child, show_id, color, indent, prefix, last_child)

    def create_folders(self, path: str | Path) -> tuple[dict, list[dict]]:
        """Preserve the local directory structure and return remote parent IDs."""
        path = Path(os.path.abspath(Path(path).expanduser()))
        if not path.is_dir() or not path.name:
            raise NotADirectoryError(path)
        folder_map = {}
        created = []

        def mkdir(parent_id, directory):
            response = self.backoff(
                self.client.post, f'{self.drive_url}/nodes',
                json=self.base_params | {'kind': 'FOLDER', 'name': directory.name, 'parents': [parent_id]},
                extensions={'photos_allow_conflict': True},
            )
            node = response_json(response)
            if response.status_code == 409:
                conflict_id = (node.get('info') or {}).get('nodeId')
                if isinstance(conflict_id, str) and re.fullmatch(r'[A-Za-z0-9_-]+', conflict_id):
                    node = response_json(self.backoff(
                        self.client.get, f'{self.drive_url}/nodes/{conflict_id}', params=self.base_params,
                    ))
                    if (node.get('kind') == 'FOLDER' and node.get('name') == directory.name
                            and parent_id in node.get('parents', [])):
                        return node
                raise APIError(409, 'POST')
            if not node.get('id'):
                raise ProtocolError("Amazon Photos did not return a created folder ID.")
            created.append(node)
            return node

        with self._upload_lock:
            pending = [(self.root['id'], path)]
            # A subsequent tree lookup must reload even if only part of this operation succeeds.
            self._folders_loaded = False
            while pending:
                parent, directory = pending.pop()
                node = mkdir(parent, directory)
                folder_map[str(directory.relative_to(path.parent))] = node['id']
                pending.extend(
                    (node['id'], child) for child in sorted(directory.iterdir(), reverse=True)
                    if child.is_dir() and not child.is_symlink()
                )
        return folder_map, created

    def _write_db(self, frame):
        if self.db_path is not None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(dir=self.db_path.parent, suffix='.parquet', delete=False) as file:
                temporary = Path(file.name)
            try:
                frame.to_parquet(temporary)
                temporary.replace(self.db_path)
            finally:
                temporary.unlink(missing_ok=True)

    def _merge_db(self, current):
        with self._cache_lock:
            # Load existing snapshots when the constructor skipped the cache.
            previous = self.db
            if previous.empty and self.db_path is not None and self.db_path.exists():
                previous = pd.read_parquet(self.db_path)
            frames = [format_nodes(frame) for frame in (current, previous) if frame is not None and not frame.empty]
            merged = format_nodes(pd.concat(frames, ignore_index=True)) if frames else pd.DataFrame()
            self._write_db(merged)
            self.db = merged
            return merged

    def refresh_db_aggressive(self, **kwargs) -> pd.DataFrame:
        """Merge search results for yesterday, today and tomorrow into the cache."""
        today = date.today()
        frames = []
        for delta in (-1, 0, 1):
            day = today + timedelta(days=delta)
            frames.append(self.query(
                f'type:(PHOTOS OR VIDEOS) AND timeYear:({day.year}) '
                f'AND timeMonth:({day.month}) AND timeDay:({day.day})', **kwargs,
            ))
        return self._merge_db(pd.concat(frames, ignore_index=True))

    def refresh_db(self, filters: list[str] | None = None, **kwargs) -> pd.DataFrame:
        """Merge an incremental snapshot into the local cache."""
        if filters is None:
            return self.refresh_db_aggressive(**kwargs)
        return self._merge_db(self.nodes(filters=filters, **kwargs))

    def load_db(self, **kwargs) -> pd.DataFrame:
        """Read an existing Parquet snapshot or populate one from search."""
        if self.db_path is not None and self.db_path.exists():
            return format_nodes(pd.read_parquet(self.db_path, **kwargs))
        frame = self.query()
        self._write_db(frame)
        return frame

    def nodes(
        self, filters: list[str] | None = None, sort: list[str] | None = None,
        limit: int | float = MAX_LIMIT, offset: int = 0, **kwargs,
    ) -> pd.DataFrame | None:
        """List matching Drive nodes, up to the requested total limit."""
        params = {
            'filters': ' AND '.join(f'({expression})' for expression in filters) if filters else '',
            'sort': str(sort) if sort else '',
        }
        result = self._paged_nodes(f'{self.drive_url}/nodes', params, offset, limit, window=MAX_NODES, **kwargs)
        return None if result.empty else result

    def nodes_by_name(self, names: list[str] | pd.Series, **kwargs) -> pd.DataFrame:
        fns = (
            partial(self._get_nodes, offset=0, filters=f'name:{name}', sort='', limit=1)
            for name in names
        )
        pages = asyncio.run(self.process(fns, desc='Finding nodes by name', **kwargs))
        return format_nodes(pd.json_normalize(node for page in pages for node in self._page_data(page)))

    async def _get_nodes(self, client: AsyncClient, sem: asyncio.Semaphore, filters: str, sort: str, limit: int, offset: int) -> dict:
        return await self._page(client, sem, f'{self.drive_url}/nodes', self.base_params | {
            'filters': filters, 'sort': sort, 'limit': limit, 'offset': offset,
        })
