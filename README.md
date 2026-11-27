# Amazon Photos API

An unofficial Python client for uploading, downloading, and organizing photos and videos in Amazon Photos.

## Installation

Requires Python 3.11 or newer. Run this in the repository root.

```sh
python -m pip install .
```

For the optional [image-labeling tools](#custom-image-labeling-optional), use `python -m pip install '.[extras]'`.

## Authentication

Sign in to [Amazon Photos](https://www.amazon.co.uk/photos) for your region. Open your browser’s Developer Tools, go to **Network**, and look at the `Cookie` header on a successful Photos request. Save your cookie values in a private JSON file outside the repository. For a UK account, the file looks like this.

```json
{
  "session-id": "SESSION_VALUE",
  "ubid-acbuk": "UK_UBID_VALUE",
  "at-acbuk": "UK_AT_VALUE"
}
```

Give the file user-only access. On macOS or Linux, use `chmod 600 /path/to/uk.cookies.json`. Set `AP_COOKIES_FILE` to the path for the examples and live tests.

| Region | `tld` | Cookie pair alongside `session-id` |
| --- | --- | --- |
| UK | `co.uk` | `ubid-acbuk`, `at-acbuk` |
| US | `com` | `ubid-main`, `at-main` |
| Canada | `ca` | `ubid-acbca`, `at-acbca` |
| Germany | `de` | `ubid-acbde`, `at-acbde` |
| Japan | `co.jp` | `ubid-acbjp`, `at-acbjp` |

The client can infer the region from the cookie names. Legacy cookie names with underscores also work.

## Usage

```python
import json
import os
from pathlib import Path

from amazon_photos import AmazonPhotos

cookies = json.loads(Path(os.environ["AP_COOKIES_FILE"]).expanduser().read_text())

with AmazonPhotos(cookies, tld="co.uk", init_db=False, init_folders=False) as photos:
    photos.upload("photo.jpg", refresh=False)

    recent = photos.photos(limit=5)
    if not recent.empty:
        photos.download(recent["id"], out="downloads")
```

A single file uploads to the root unless you pass `parent_id`. A directory keeps its folder structure. The client skips a file if Amazon already has a copy with the same checksum. The upload returns HTTP responses for newly uploaded files. Pass `verify_checksum=True` to `download()` to check downloaded files against Amazon’s checksums.

The client can skip its initial folder and cache scans with `init_folders=False` and `init_db=False`, as shown above. Use a separate Parquet cache file for each account. For custom endpoints, use `drive_override` or `upload_override` with hosts you trust to receive your cookies. Set `upload_api="v2"` to try the alternative upload format.

For scripts or scheduled jobs, use the [single-file upload example](examples/upload_one.py).

```sh
AP_COOKIES_FILE=/path/to/uk.cookies.json python examples/upload_one.py /path/to/photo.jpg
```

### Handling errors

- `AuthenticationError` means Amazon rejected the session or asked for verification. Pause uploads, open Photos in your browser, sign in again if prompted, and start a new client with fresh cookies.
- `UploadUncertainError` means Amazon may have received the file. Keep the original and verify whether it exists remotely before retrying.
- `APIError` reports the HTTP status. `TransportError` reports network failures, and `ProtocolError` reports unexpected responses.

The upload example exits with 0 on success, 2 for authentication errors, 3 for uncertain uploads, and 1 for other errors.

## Tests

CI tests Python 3.11–3.14. The default suite runs offline.

```sh
python -m unittest discover -s tests -v
```

To run the live tests, set `AP_COOKIES_FILE` and, for accounts outside the UK, `AP_TLD`. The first command checks authentication and reads. The second also uploads one generated PNG and leaves it in your library.

```sh
AP_LIVE=1 python -m unittest discover -s tests/live -v
AP_LIVE=1 AP_LIVE_UPLOAD=1 python -m unittest discover -s tests/live -v
```

Set `AP_EXPIRED_COOKIES_FILE` to a separate file with known-expired cookies to test session rejection.

## Limitations

Amazon can change these private endpoints. Uploads, downloads, and duplicate handling were verified on one UK account. Automatic session renewal and Echo Show display remain unverified. Node and trash listings have a 9,999-item window. Use narrower filters for larger libraries.

The public API is synchronous. If your application uses asyncio, call it from a worker thread. Use the `amazon_photos` logger for diagnostics, and keep raw HTTP tracing off when using real credentials.

Licensed under the [MIT licence](LICENSE).

## Search

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
