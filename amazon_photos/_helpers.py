from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

import pandas as pd

from ._constants import AP_DATE_FMT


def convert_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime(AP_DATE_FMT)


def folder_relmap(root: str, files: Generator | list, folder_map: dict) -> list[tuple[str, str]]:
    res = []
    for p in files:
        if root in p.parts:
            idx = p.parts.index(root)
            rel = Path(*p.parts[idx:])
        else:
            rel = p
        fid = folder_map[str(rel.parent)]
        res.append((fid, p))
    return res


def format_nodes(df: pd.DataFrame) -> pd.DataFrame | None:
    if df is None or df.empty:
        return df
    df = df.copy()
    if 'id' in df:
        df = df.loc[~(df['id'].notna() & df['id'].duplicated())]
    # .parquet format does not support cols with empty dicts, drop non-essential cols
    df = df.drop(columns=['xAccntParentMap', 'xAccntParents', 'assets', 'contentProperties.version'], errors='ignore')
    # clean up col names and order important cols for readability
    df.columns = df.columns.str.removeprefix('contentProperties.')
    cols = [
        'createdDate',
        'modifiedDate',
        'id',
        'parents',
        'image.dateTime',
        'name',
        'image.width',
        'image.height',
        'video.width',
        'video.height',
        'size',
        'md5',
        'ownerId',
        'contentType',
        'extension',
    ]
    date_cols = {
        'contentDate',
        'createdDate',
        'creationDate',
        'dateTime',
        'dateTimeDigitized',
        'dateTimeOriginal',
        'modifiedDate',
        'ProcessingTimestamp',
        'VideoMetadataTimestamps',
        'VideoThumbnailTimestamps',
        'VideoTranscodeTimestamps',
    }
    date_cols |= {f'{prefix}.{name}' for name in date_cols for prefix in ('img', 'image', 'video')}
    for column in date_cols.intersection(df.columns):
        df[column] = pd.to_datetime(df[column], format='ISO8601', errors='coerce', utc=True)
    ordered = [column for column in cols if column in df]
    df = df[ordered + [column for column in df if column not in cols]]
    if 'modifiedDate' in df:
        df = df.sort_values('modifiedDate', ascending=False, kind='stable')
    return df.reset_index(drop=True)
