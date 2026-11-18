"""Beijing timestamps and redaction for logs, notifications and job summaries."""

import datetime
import logging
import logging.config
import os


_secrets: set[str] = set()


def register_secret(value: str) -> None:
    if not value:
        return
    _secrets.add(value)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        # Structured/multiline GitHub secrets do not automatically mask every
        # individual Cookie or session value parsed from them.
        escaped = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::add-mask::{escaped}", flush=True)


def redact_text(text: str) -> str:
    for secret in sorted(_secrets, key=len, reverse=True):
        text = text.replace(secret, "***")
    return text


class SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage())
        record.args = ()
        return True


def beijing_time_converter(timestamp):
    timezone = datetime.timezone(datetime.timedelta(hours=8))
    return datetime.datetime.fromtimestamp(timestamp, timezone).timetuple()


class BeijingFormatter(logging.Formatter):
    converter = staticmethod(beijing_time_converter)


def init_logger():
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "()": BeijingFormatter,
                "format": "%(asctime)s - %(levelname)s - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "filters": {"secrets": {"()": SecretFilter}},
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "standard",
                "filters": ["secrets"],
                "level": "INFO",
            },
        },
        "root": {"handlers": ["console"], "level": "INFO"},
    })
    return logging.getLogger()
