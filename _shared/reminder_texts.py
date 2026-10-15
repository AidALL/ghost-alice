"""Read canonical hook guidance shared by installers and native runners."""
import base64
import json
from pathlib import Path


def reminder_text(name):
    text = json.loads(Path(__file__).with_suffix('.json').read_text(encoding='utf-8'))[name]
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f'Missing hook instruction: {name}')
    return text


def instruction_override(encoded):
    """Only an explicit caller argument supplies custom instruction text."""
    if encoded is None:
        return None
    return base64.b64decode(encoded.encode('ascii'), altchars=b'-_', validate=True).decode('utf-8')
