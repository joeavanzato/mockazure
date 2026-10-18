from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from mockazure.admin.errors import AdminError
from mockazure.content.models import ContentRef
from mockazure.content.ranges import content_range


def test_content_ref_is_immutable():
    value = ContentRef(uuid4(), 3, "a" * 64, "application/octet-stream")
    with pytest.raises(FrozenInstanceError):
        value.size = 4


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, (0, 10)),
        ("bytes=0-0", (0, 1)),
        ("bytes=9-", (9, 10)),
        ("bytes=-2", (8, 10)),
        ("bytes=0-99", (0, 10)),
    ],
)
def test_content_range_boundaries(header, expected):
    assert content_range(header, 10) == expected


@pytest.mark.parametrize(
    "header",
    [
        "bytes=10-",
        "bytes=2-1",
        "bytes=-0",
        "bytes=0-1,3-4",
        "items=0-1",
        "bytes=garbage",
        pytest.param("bytes=" + "9" * 5000 + "-", id="oversized-integer"),
    ],
)
def test_invalid_public_range_is_416(header):
    with pytest.raises(AdminError) as error:
        content_range(header, 10)
    assert error.value.status == 416
