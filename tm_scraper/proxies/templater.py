"""Expand ${random_...} placeholders in proxy usernames (ported from axs).

Supported: random_uuid, random_number, random_alphanumeric_#, random_letters_#,
random_lc_letters_#, random_uc_letters_# (# = 1..99).
"""
import random
import string
import time
import uuid
from string import Template
from typing import Callable


def _random_number() -> str:
    return str(random.randint(1, 2 ** 32 - 1))


def _random_string(population, length: int) -> str:
    if length <= 0 or length > 100:
        raise ValueError("length must be 1..100")
    return "".join(random.choices(population, k=length))


random.seed(time.time())
_VAR_FUNC: dict[str, Callable[[], str]] = {
    "random_uuid": lambda: str(uuid.uuid4()),
    "random_number": _random_number,
}
for _i in range(1, 100):
    _VAR_FUNC[f"random_alphanumeric_{_i}"] = lambda k=_i: _random_string(string.ascii_letters + string.digits, k)
    _VAR_FUNC[f"random_letters_{_i}"] = lambda k=_i: _random_string(string.ascii_letters, k)
    _VAR_FUNC[f"random_lc_letters_{_i}"] = lambda k=_i: _random_string(string.ascii_lowercase, k)
    _VAR_FUNC[f"random_uc_letters_{_i}"] = lambda k=_i: _random_string(string.ascii_uppercase, k)


class _LazyVars(dict):
    """Compute a placeholder's value on lookup; unknown names raise KeyError so
    safe_substitute leaves them untouched. (Avoids Template.get_identifiers,
    which is Python 3.11+.)"""

    def __getitem__(self, key):
        if key in _VAR_FUNC:
            return _VAR_FUNC[key]()
        raise KeyError(key)


def expand_template(template: str) -> str:
    """Expand `$VAR` / `${VAR}` placeholders; unknown names are left untouched."""
    if not template:
        return template
    return Template(template).safe_substitute(_LazyVars())
