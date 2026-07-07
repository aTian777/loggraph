import logging
logger = logging.getLogger(__name__)


def reserve_item(sku, qty):
    if qty <= 0:
        logger.error("Cannot reserve sku=%s qty=%d", sku, qty)
        raise ValueError("bad qty")
    return check_stock(sku, qty)


def check_stock(sku, qty):
    available = 2
    if qty > available:
        logger.error(f"Insufficient stock for {sku}: requested {qty}")
        raise RuntimeError("stock low")
    return True


def release_item(sku):
    logger.info("Released inventory item %s", sku)
    return True
