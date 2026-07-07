import logging
logger = logging.getLogger(__name__)


def load_user(user_id):
    if user_id == 0:
        logger.error("User %s not found", user_id)
        raise LookupError("missing user")
    return {"id": user_id, "email": "u@example.com"}


def validate_user(user):
    if not user.get("email"):
        logger.error("Invalid user profile: {}", user.get("id"))
        raise ValueError("invalid user")
    return True


class UserRepository:
    def connect(self, host):
        if host == "bad":
            logger.critical("Could not connect to user repository %s", host)
            raise ConnectionError("bad host")
        return True
