"""Receipt parsing and searchable static archive."""

from lidlplus.archive.receipt import build_product_index, receipt_record
from lidlplus.archive.site import write_site

__all__ = ["build_product_index", "receipt_record", "write_site"]
