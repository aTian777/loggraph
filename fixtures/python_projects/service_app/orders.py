import logging
logger = logging.getLogger(__name__)


def api_create_order(order_id):
    return update_order(order_id)


def update_order(order_id):
    if not order_id:
        logger.error("Failed to update order %s", order_id)
        raise ValueError("order id missing")
    logger.info("Updated order %s", order_id)
    return save_order(order_id)


def save_order(order_id):
    if order_id == 500:
        logger.critical("Database unavailable for order {}".format(order_id))
        raise RuntimeError("database unavailable")
    return True


class PaymentService:
    def charge(self, user_id, amount):
        if amount < 0:
            logger.exception("Payment failed for user %s amount %s", user_id, amount)
            raise ValueError("negative amount")
        return True

    def refund(self, order_id):
        logger.warning(f"Refund requested for order {order_id}")
        return True
