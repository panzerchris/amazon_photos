# Amazon Photos API

An unofficial Python library for listing, uploading, downloading and organizing media in Amazon Photos. It uses the web application's private endpoints and credentials from your own browser session.

## Installation

Install from a checkout of this fork on CPython 3.11–3.14.

```sh
python -m pip install .
```

Use `python -m pip install '.[extras]'` to install the optional image-labeling dependencies.

## Authentication and region

Sign in to [Amazon Photos UK](https://www.amazon.co.uk/photos) in your browser and complete any requested verification. In browser Developer Tools, select a successful Photos request in the Network tab. In the request's Cookie header, find `session-id`, `ubid-acbuk` and `at-acbuk`. Network request headers can show HttpOnly cookies that browser JavaScript cannot access.

Save the corresponding values as a plain JSON object in a private file outside the repository. This example contains placeholders.

```json
{
  "session-id": "SESSION_VALUE",
  "ubid-acbuk": "UK_UBID_VALUE",
  "at-acbuk": "UK_AT_VALUE"
}
```

Give the file user-only access, such as `chmod 600 /path/to/uk.cookies.json` on Unix. Set `AP_COOKIES_FILE` to its path for the examples. Pass the mapping directly to `AmazonPhotos` when loading secrets from another source.

| Marketplace | `tld` | Common cookie pair | Default upload zone |
| --- | --- | --- | --- |
| UK | `co.uk` | `ubid-acbuk`, `at-acbuk` | EU |
| US | `com` | `ubid-main`, `at-main` | NA |
| Canada | `ca` | `ubid-acbca`, `at-acbca` | NA |
| Germany | `de` | `ubid-acbde`, `at-acbde` | EU |
| Japan | `co.jp` | `ubid-acbjp`, `at-acbjp` | JP |

One unambiguous marketplace can also be inferred from the cookie names. Legacy underscore aliases are accepted. Override endpoints with `drive_override` and `cdproxy_override` only for endpoints you trust with the session. `upload_api='v2'` selects the optional marketplace v2 endpoint, and `upload_override` selects a known compatible URL. No automatic fallback occurs after a write. Use `user_agent` to pass the user agent from your browser if the default profile does not work.

## Upload and download

```python
import json
import os
from pathlib import Path

from amazon_photos import AmazonPhotos

cookies = json.loads(Path(os.environ["AP_COOKIES_FILE"]).expanduser().read_text())

with AmazonPhotos(
    cookies,
    tld="co.uk",
    init_folders=False,
    init_db=False,
    db_path=None,
) as photos:
    receipts = photos.upload("new-photo.jpg", refresh=False, max_connections=1)
    for receipt in receipts:
        node = receipt.json()
        print(node.get("id") or node["nodeId"])

    matches = photos.photos(limit=5)
    if not matches.empty:
        photos.download(matches["id"], out="downloaded", verify_checksum=True)
```

A single file uploads to the account root unless you supply `parent_id`. A directory copies its local structure. The default upload checks Amazon for available files with the same MD5 and skips matching content account-wide. An empty response list means no new file was uploaded. An explicit `md5s` set substitutes caller-owned deduplication input. `skip_dedup=True` disables preflight deduplication. A filename conflict only counts as an existing copy if remote metadata confirms the checksum. The client streams the source bytes without re-encoding media or altering EXIF.

Downloads write complete files atomically. `verify_checksum=True` requires a remote checksum and verifies it before publishing a local file. `query`, `photos`, `videos` and `trashed` return Pandas DataFrames. `nodes` returns a DataFrame or `None` if no rows match. Use distinct cache files for distinct accounts if you enable Parquet caching. With both initialization flags disabled, startup checks the account root and skips the eager scans.

## Unattended execution and errors

```sh
AP_COOKIES_FILE=/path/to/uk.cookies.json AP_TLD=co.uk python examples/upload_one.py /path/to/image.jpg
```

The example exits with status 0 for a confirmed upload or verified existing copy, 2 when login is needed, 3 when Amazon may have received a write whose result could not be verified, and 1 for other failures.

- `AuthenticationError` indicates that Amazon rejected the session or asked for verification. Pause uploads, sign in again if prompted, export fresh cookies, and start a new client. Automatic renewal of expired UK credentials is unverified.
- `APIError` exposes a status code and `Retry-After`, if available. A generic 403 is distinct from a confirmed rejected session.
- `TransportError` indicates an HTTP transfer failed. Reads use bounded retries and backoff for transient errors.
- `UploadUncertainError` means the upload may have been committed. Keep the source and look up the remote checksum before retrying. In a batch, earlier files may already have succeeded.
- `ProtocolError` indicates an unexpected response or integrity failure. Check the endpoint, credentials and supported request format.

The client does not configure global logging. Use the `amazon_photos` logger for retry diagnostics. Keep HTTP client tracing and the optional `tmp` response snapshots off for routine runs because they can contain signed links or private metadata. TLS verification is enabled. System proxy settings are used only with `trust_env=True`.

## Tests and limits

```sh
python -m unittest discover -s tests -v
AP_LIVE=1 python -m unittest discover -s tests/live -v
AP_LIVE=1 AP_LIVE_UPLOAD=1 python -m unittest discover -s tests/live -v
```

The ordinary suite runs offline. For live tests, set `AP_COOKIES_FILE` to the private cookie file and `AP_TLD` if the account is outside the UK. The write test leaves one generated PNG in the account. Set `AP_EXPIRED_COOKIES_FILE` to a separate, known-expired capture to test session rejection.

A live UK run verified sign-in, photo listing, cookie reuse across processes, and checksum-verified upload, duplicate handling, and download. Session lifetime and Echo Show display remain unverified.

Node and trash listings have an upstream window of 9,999 rows. Use narrower filters for larger inventories. Remote indexing is eventually consistent, and a listing can change between pages. Cross-process uploads do not offer an exactly-once guarantee. Public methods are synchronous, with bounded internal async batches. In an asyncio application, use a worker thread for the synchronous API. This client depends on private endpoints that may change.

Licensed under the [MIT licence](LICENSE).

## Search

*Undocumented API, current endpoints valid Dec 2023.*

For valid **location** and **people** IDs, see the results from the `aggregations()` method.

| name            | type | description                                                                                                                                                                                                                                               |
|:----------------|:-----|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| ContentType     | str  | `"JSON"`                                                                                                                                                                                                                                                  |
| _               | int  | `1690059771064`                                                                                                                                                                                                                                           |
| asset           | str  | `"ALL"`<br/>`"MOBILE"`<br/>`"NONE`<br/>`"DESKTOP"`<br/><br/>default: `"ALL"`                                                                                                                                                                              |
| filters         | str  | `"type:(PHOTOS OR VIDEOS) AND things:(plant AND beach OR moon) AND timeYear:(2019) AND timeMonth:(7) AND location:(CAN#BC#Vancouver) AND people:(CyChdySYdfj7DHsjdSHdy)"`<br/><br/>default: `"type:(PHOTOS OR VIDEOS)"`                                   |
| groupByForTime  | str  | `"day"`<br/>`"month"`<br/>`"year"`                                                                                                                                                                                                                        |
| limit           | int  | `200`                                                                                                                                                                                                                                                     |
| lowResThumbnail | str  | `"true"`<br/>`"false"`<br/><br/>default: `"true"`                                                                                                                                                                                                         |
| resourceVersion | str  | `"V2"`                                                                                                                                                                                                                                                    |
| searchContext   | str  | `"customer"`<br/>`"all"`<br/>`"unknown"`<br/>`"family"`<br/>`"groups"`<br/><br/>default: `"customer"`                                                                                                                                                     |
| sort            | str  | `"['contentProperties.contentDate DESC']"`<br/>`"['contentProperties.contentDate ASC']"`<br/>`"['createdDate DESC']"`<br/>`"['createdDate ASC']"`<br/>`"['name DESC']"`<br/>`"['name ASC']"`<br/><br/>default: `"['contentProperties.contentDate DESC']"` |
| tempLink        | str  | `"false"`<br/>`"true"`<br/><br/>default: `"false"`                                                                                                                                                                                                        |             |

## Nodes

*Docs last updated in 2015*

| FieldName                     | FieldType                | Sort Allowed | Notes                                                                                                                                                                                                                                       |
|-------------------------------|--------------------------|--------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| isRoot                        | Boolean                  |              | Only lower case `"true"` is supported.                                                                                                                                                                                                      |
| name                          | String                   | Yes          | This field does an exact match on the name and prefix query. Consider `node1{ "name" : "sample" }` `node2 { "name" : "sample1" }` Query filter<br>`name:sample` will return node1<br>`name:sample*` will return node1 and node2             |
| kind                          | String                   | Yes          | To search for all the nodes which contains kind as FILE `kind:FILE`                                                                                                                                                                         |
| modifiedDate                  | Date (in ISO8601 Format) | Yes          | To Search for all the nodes which has modified from time `modifiedDate:{"2014-12-31T23:59:59.000Z" TO *]`                                                                                                                                   |
| createdDate                   | Date (in ISO8601 Format) | Yes          | To Search for all the nodes created on  `createdDate:2014-12-31T23:59:59.000Z`                                                                                                                                                              |
| labels                        | String Array             |              | Only Equality can be tested with arrays.<br>if labels contains `["name", "test", "sample"]`.<br>Label can be searched for name or combination of values.<br>To get all the labels which contain name and test<br>`labels: (name AND test)`  |
| description                   | String                   |              | To Search all the nodes for description with value 'test'<br>`description:test`                                                                                                                                                             |
| parents                       | String Array             |              | Only Equality can be tested with arrays.<br>if parents contains `["id1", "id2", "id3"]`.<br>Parent can be searched for name or combination of values.<br>To get all the parents which contains id1 and id2<br>`parents:id1 AND parents:id2` |
| status                        | String                   | Yes          | For searching nodes with AVAILABLE status.<br>`status:AVAILABLE`                                                                                                                                                                            |
| contentProperties.size        | Long                     | Yes          |                                                                                                                                                                                                                                             |
| contentProperties.contentType | String                   | Yes          | If prefix query, only the major content-type (e.g. `image*`, `video*`, etc.) is supported as a prefix.                                                                                                                                      |
| contentProperties.md5         | String                   |              |                                                                                                                                                                                                                                             |
| contentProperties.contentDate | Date (in ISO8601 Format) | Yes          | RangeQueries and equals queries can be used with this field                                                                                                                                                                                 |
| contentProperties.extension   | String                   | Yes          |                                                                                                                                                                                                                                             |

### Restrictions

> Max # of Filter Parameters Allowed is 8

| Filter Type | Filters                                                                               |
|:------------|:--------------------------------------------------------------------------------------|
| Equality    | createdDate, description, isRoot, kind, labels, modifiedDate, name, parentIds, status |
| Range       | contentProperties.contentDate, createdDate, modifiedDate                              |
| Prefix      | contentProperties.contentType, name                                                   |

### Range Queries

| Operation            | Syntax                                                           |
|----------------------|------------------------------------------------------------------|
| GreaterThan          | `{"valueToBeTested" TO *}`                                       |
| GreaterThan or Equal | `["ValueToBeTested" TO *]`                                       |
| LessThan             | `{* TO "ValueToBeTested"}`                                       |
| LessThan or Equal    | `{* TO "ValueToBeTested"]`                                       |
| Between              | `["ValueToBeTested_LowerBound" TO "ValueToBeTested_UpperBound"]` |

## Notes

#### `https://www.amazon.ca/drive/v1/batchLink`

- This endpoint is called when downloading a batch of photos/videos in the web interface. It then returns a URL to
  download a zip file, then makes a request to that url to download the content.
  When making a request to download data for 1200 nodes (max batch size), it turns out to be much slower (~2.5 minutes)
  than asynchronously downloading 1200 photos/videos individually (~1 minute).

### Known File Types

| Extension | Category |
|-----------|----------|
| \.pdf     | pdf      |
| \.doc     | doc      |
| \.docx    | doc      |
| \.docm    | doc      |
| \.dot     | doc      |
| \.dotx    | doc      |
| \.dotm    | doc      |
| \.asd     | doc      |
| \.cnv     | doc      |
| \.mp3     | mp3      |
| \.m4a     | mp3      |
| \.m4b     | mp3      |
| \.m4p     | mp3      |
| \.wav     | mp3      |
| \.aac     | mp3      |
| \.aif     | mp3      |
| \.mpa     | mp3      |
| \.wma     | mp3      |
| \.flac    | mp3      |
| \.mid     | mp3      |
| \.ogg     | mp3      |
| \.xls     | xls      |
| \.xlm     | xls      |
| \.xll     | xls      |
| \.xlc     | xls      |
| \.xar     | xls      |
| \.xla     | xls      |
| \.xlb     | xls      |
| \.xlsb    | xls      |
| \.xlsm    | xls      |
| \.xlsx    | xls      |
| \.xlt     | xls      |
| \.xltm    | xls      |
| \.xltx    | xls      |
| \.xlw     | xls      |
| \.ppt     | ppt      |
| \.pptx    | ppt      |
| \.ppa     | ppt      |
| \.ppam    | ppt      |
| \.pptm    | ppt      |
| \.pps     | ppt      |
| \.ppsm    | ppt      |
| \.ppsx    | ppt      |
| \.pot     | ppt      |
| \.potm    | ppt      |
| \.potx    | ppt      |
| \.sldm    | ppt      |
| \.sldx    | ppt      |
| \.txt     | txt      |
| \.text    | txt      |
| \.rtf     | txt      |
| \.xml     | markup   |
| \.htm     | markup   |
| \.html    | markup   |
| \.zip     | zip      |
| \.rar     | zip      |
| \.7z      | zip      |
| \.jpg     | img      |
| \.jpeg    | img      |
| \.png     | img      |
| \.bmp     | img      |
| \.gif     | img      |
| \.tif     | img      |
| \.svg     | img      |
| \.mp4     | vid      |
| \.m4v     | vid      |
| \.qt      | vid      |
| \.mov     | vid      |
| \.mpg     | vid      |
| \.mpeg    | vid      |
| \.3g2     | vid      |
| \.3gp     | vid      |
| \.flv     | vid      |
| \.f4v     | vid      |
| \.asf     | vid      |
| \.avi     | vid      |
| \.wmv     | vid      |
| \.swf     | exe      |
| \.exe     | exe      |
| \.dll     | exe      |
| \.ax      | exe      |
| \.ocx     | exe      |
| \.rpm     | exe      |

## Custom Image Labeling (Optional)

Categorize your images into folders using computer vision models.

```bash
pip install amazon-photos[extras] -U
```

See the [Model List](https://www.hobenshield.com/stats/bench/index.html) for a list of all available models.

### Sample Models

**Very Large**

```
eva02_base_patch14_448.mim_in22k_ft_in22k_in1k
```

**Large**

```
eva02_large_patch14_448.mim_m38m_ft_in22k_in1k
```

**Medium**

```
eva02_small_patch14_336.mim_in22k_ft_in1k
vit_base_patch16_clip_384.laion2b_ft_in12k_in1k
vit_base_patch16_clip_384.openai_ft_in12k_in1k
caformer_m36.sail_in22k_ft_in1k_384
```

**Small**

```
eva02_tiny_patch14_336.mim_in22k_ft_in1k
tiny_vit_5m_224.dist_in22k_ft_in1k
edgenext_small.usi_in1k
xcit_tiny_12_p8_384.fb_dist_in1k
```

```python
run(
    'eva02_base_patch14_448.mim_in22k_ft_in22k_in1k',
    path_in='images',
    path_out='labeled',
    thresh=0.0,  # threshold for predictions, 0.9 means you want very confident predictions only
    topk=5,
    # window of predictions to check if using exclude or restrict, if set to 1, only the top prediction will be checked
    exclude=lambda x: re.search('boat|ocean', x, flags=re.I),
    # function to exclude classification of these predicted labels
    restrict=lambda x: re.search('sand|beach|sunset', x, flags=re.I),
    # function to restrict classification to only these predicted labels
    dataloader_options={
        'batch_size': 4,  # *** adjust ***
        'shuffle': False,
        'num_workers': psutil.cpu_count(logical=False),  # *** adjust ***
        'pin_memory': True,
    },
    accumulate=False,
    # accumulate results in path_out, if False, everything in path_out will be deleted before running again
    device='cuda',
    naming_style='name',  # use human-readable label names, optionally use the label index or synset
    debug=0,
)
```
