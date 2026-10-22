"""Append completed private-message interactions as one JSON object per line."""
from datetime import datetime, timezone
import json
import threading

from config import CHAT_INTERACTION_LOG

_lock = threading.Lock()


def log_interaction(player, request, decision, *, response_sent, game_closed=None, error=None):
    sender, message = request
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "player": player,
        "sender": sender,
        "message": message,
        "response": decision.response,
        "action": decision.action,
        "response_sent": response_sent,
        "sender_is_gm": bool(getattr(request, 'is_gm', False)),
    }
    if game_closed is not None:
        record["game_closed"] = game_closed
    if error:
        record["error"] = error
    with _lock:
        with CHAT_INTERACTION_LOG.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
