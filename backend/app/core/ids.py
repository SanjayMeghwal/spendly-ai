"""Primary-key generation.

Kept here rather than in a model module because every future model will need
it, and because it depends on nothing else in the application.
"""

import uuid

import uuid_utils


def uuid7() -> uuid.UUID:
    """Return a time-ordered UUID version 7.

    WHY UUIDv7 RATHER THAN A SEQUENTIAL INTEGER

    Integer primary keys appear in URLs (`/users/1`, `/users/2`), which lets
    anyone enumerate every account and read the signup rate straight off the
    numbers. For a finance application that is both a privacy leak and a
    business-metrics leak.

    WHY v7 RATHER THAN v4

    Both are unguessable, but a v4 is fully random, so consecutive inserts land
    at random positions in the primary-key B-tree. Every insert dirties a
    different page, the index stops fitting usefully in cache, and pages split
    repeatedly. A v7 puts a millisecond timestamp in its leading bits, so IDs
    generated later sort after IDs generated earlier and inserts append to the
    right-hand edge of the index - the same access pattern a sequence gives,
    without the enumerability.

    WHY THE CONVERSION

    `uuid_utils.uuid7()` returns uuid_utils' own UUID class, not the standard
    library's. SQLAlchemy's `Uuid` column type, pydantic, and every type
    annotation in this codebase expect `uuid.UUID`, so it is converted once
    here rather than being worked around at each call site.

    (Python gained `uuid.uuid7()` in 3.14 and PostgreSQL gained `uuidv7()` in
    18. We target 3.12 and PostgreSQL 17, so neither is available; when we move
    up, this function's body can shrink to a single stdlib call and nothing
    else in the codebase has to change - which is the point of wrapping it.)
    """
    return uuid.UUID(bytes=uuid_utils.uuid7().bytes)
