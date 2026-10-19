"""JSON-safe typed scalar representations shared by the two query services."""

from datetime import UTC


def cell(value, kind):
    if value is None:
        return None
    if kind == "datetime":
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if kind == "timespan":
        micros = (value.days * 86400 + value.seconds) * 1000000 + value.microseconds
        sign = "-" if micros < 0 else ""
        seconds, fraction = divmod(abs(micros), 1000000)
        days, seconds = divmod(seconds, 86400)
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        return f"{sign}{str(days) + '.' if days else ''}{hours:02}:{minutes:02}:{seconds:02}" + (
            f".{fraction:06}0" if fraction else ""
        )
    return value
