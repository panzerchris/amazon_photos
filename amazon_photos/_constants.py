MAX_TRASH_BATCH = 50  # max nodes to batch into trash request
MAX_PURGE_BATCH = 50  # max nodes to batch into purge (delete) request
MAX_DOWNLOAD_BATCH = 1200  # max nodes to batch into zip file for download
MAX_NODES = 9999  # this is a real limit, not a large arbitrary number
MAX_LIMIT = 200  # max number of nodes to return in a single request
AP_DATE_FMT = "%Y-%m-%dT%H:%M:%S.000Z"  # ISO 8601 with timezone offset

Black = '\x1b[30m'
Red = '\x1b[31m'
Green = '\x1b[32m'
Yellow = '\x1b[33m'
Blue = '\x1b[34m'
Magenta = '\x1b[35m'
Cyan = '\x1b[36m'
White = '\x1b[37m'
Reset = '\x1b[0m'

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

NORTH_AMERICA_TLD_MAP = {
    "ca": "Canada",
    "com": "United States",
}

EUROPEAN_TLD_MAP = {
    "at": "Austria",
    "be": "Belgium",
    "bg": "Bulgaria",
    "hr": "Croatia",
    "ch": "Switzerland",  # todo
    "cy": "Cyprus",
    "cz": "Czech Republic",
    "dk": "Denmark",
    "ee": "Estonia",
    "fi": "Finland",
    "fr": "France",
    "de": "Germany",
    "gr": "Greece",
    "hu": "Hungary",
    "is": "Iceland",  # todo
    "ie": "Ireland",
    "it": "Italy",
    "li": "Liechtenstein",  # todo
    "lv": "Latvia",
    "lt": "Lithuania",
    "lu": "Luxembourg",
    "mt": "Malta",
    "no": "Norway",  # todo
    "nl": "Netherlands",
    "pl": "Poland",
    "pt": "Portugal",
    "ro": "Romania",
    "sk": "Slovakia",
    "si": "Slovenia",
    "es": "Spain",
    "se": "Sweden",
    "uk": "United Kingdom",
}

TLD_EXCEPTIONS = {
    "uk": "co.uk",
    "jp": "co.jp",
    "au": "com.au",
    "br": "com.br",
    "mx": "com.mx",
    "be": "com.be",
}
