"""QR code rendering for subscription links (pure-python, no image deps)."""

from __future__ import annotations

import io


def subscription_qr_png(url: str, *, scale: int = 6, border: int = 2) -> bytes:
    """
    Render a subscription URL as a PNG.

    Falls back to a 1x1 placeholder if the URL is empty, so a broken link can
    never crash a message send.
    """
    if not url:
        return _PLACEHOLDER_PNG

    # Imported lazily: a missing optional dependency should only break the QR
    # feature, never the whole bot.
    import segno

    buffer = io.BytesIO()
    segno.make(url, error="m").save(
        buffer, kind="png", scale=scale, border=border, dark="#000000", light="#ffffff"
    )
    return buffer.getvalue()


def qr_send_photo(url: str, *, scale: int = 6) -> bytes:
    """Alias kept for call sites that read better as 'send this photo'."""
    return subscription_qr_png(url, scale=scale)


# 1x1 transparent PNG — used when there is nothing to encode.
_PLACEHOLDER_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000d49444154789c636060f8cf000001010100e2b9e7"
    "1e0000000049454e44ae426082"
)
