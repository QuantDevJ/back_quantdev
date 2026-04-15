from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def success_response(data: Any, status_code: int = 200) -> Tuple[Dict[str, Any], int]:
    return (
        {
            "success": True,
            "data": data,
            "error": None,
            "timestamp": utc_timestamp(),
        },
        status_code,
    )


def error_response(code: str, message: str, details: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {
        "success": False,
        "data": None,
        "error": {
            "code": code,
            "message": message,
            "details": details or [],
        },
        "timestamp": utc_timestamp(),
    }
