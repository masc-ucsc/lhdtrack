"""HTML rendering. A pure function of site/ledger.jsonl -- nothing is computed
here that is not already in the ledger."""

from .html import write_all, write_index, write_report, write_timeseries  # noqa: F401
