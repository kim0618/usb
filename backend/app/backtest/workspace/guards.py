"""Defence against a credential reaching a file that Google Drive will replicate.

Shared state files carry stage bookkeeping only. Both the key names and the values are
screened so that a careless caller cannot park an API key in ``notes``.
"""

import re
from typing import Any

from app.backtest.workspace.errors import SecretLikeValue


SECRET_KEY_PATTERN = re.compile(
    r"(key|secret|token|password|passwd|credential|cookie|authorization|auth_)", re.IGNORECASE)
SECRET_VALUE_PATTERN = re.compile(
    r"(api[_\- ]?key|app[_\- ]?key|app[_\- ]?secret|access[_\- ]?token|private[_\- ]?key"
    r"|client[_\- ]?secret|password|passwd|credential|bearer\s+\S|sk-[A-Za-z0-9]{8,})",
    re.IGNORECASE)


def assert_no_secret_like(payload: dict[str, Any], *, where: str) -> None:
    for key, value in payload.items():
        if SECRET_KEY_PATTERN.search(str(key)):
            raise SecretLikeValue(f"{where}: field {key!r} looks like a credential")
        if isinstance(value, str) and SECRET_VALUE_PATTERN.search(value):
            raise SecretLikeValue(f"{where}: value of {key!r} looks like a credential")
