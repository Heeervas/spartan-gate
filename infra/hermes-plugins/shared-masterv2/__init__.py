"""Common MASTER instructions via Hermes's native frozen system section."""
import logging

from hermes_constants import get_default_hermes_root
from hermes_cli.plugins import MAX_SYSTEM_PROMPT_SECTION_CHARS

logger = logging.getLogger(__name__)


def _read_master(_session_info):
    path = get_default_hermes_root() / "MASTER.md"
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("shared-masterv2: common MASTER.md is missing; instructions omitted. Restore it in the common Hermes root.")
        return ""
    except (OSError, UnicodeError):
        logger.warning("shared-masterv2: common MASTER.md is unreadable; instructions omitted. Check permissions and UTF-8 encoding.")
        return ""
    if not content.strip():
        logger.warning("shared-masterv2: common MASTER.md is empty; instructions omitted. Add the common instructions.")
    return content


def register(ctx):
    ctx.register_system_prompt_section(
        "shared-masterv2.master", _read_master,
        max_chars=MAX_SYSTEM_PROMPT_SECTION_CHARS,
    )
