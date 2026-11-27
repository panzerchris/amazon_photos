"""Upload one file using credentials supplied via a private JSON file."""

import argparse
import json
import os
import sys
from pathlib import Path

from amazon_photos import AmazonPhotos, AmazonPhotosError, AuthenticationError, UploadUncertainError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    args = parser.parse_args()
    if not args.image.expanduser().is_file():
        parser.error('image must be a file')

    try:
        credentials = Path(os.environ['AP_COOKIES_FILE']).expanduser()
        cookies = json.loads(credentials.read_text(encoding='utf-8'))
        with AmazonPhotos(
            cookies, tld=os.getenv('AP_TLD', 'co.uk'), init_db=False, init_folders=False,
            db_path=None, user_agent=os.getenv('AP_USER_AGENT'),
        ) as photos:
            receipts = photos.upload(args.image, refresh=False, max_connections=1)
            if receipts:
                print(f"Uploaded node {receipts[0].json()['id']}")
            else:
                print('A matching image is already available in Amazon Photos.')
        return 0
    except AuthenticationError as error:
        print(error, file=sys.stderr)
        return 2
    except UploadUncertainError as error:
        print(error, file=sys.stderr)
        return 3
    except (OSError, KeyError, ValueError, AmazonPhotosError) as error:
        print(f'Upload failed ({type(error).__name__}). Check the source file, credentials and endpoint.',
              file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
