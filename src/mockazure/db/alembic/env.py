from alembic import context

from mockazure.auditlog import models as auditlog_models  # noqa: F401
from mockazure.collections import models as collection_models  # noqa: F401
from mockazure.content import models as content_models  # noqa: F401
from mockazure.db.models import Base
from mockazure.evidence import models as evidence_models  # noqa: F401
from mockazure.licensing import models as licensing_models  # noqa: F401
from mockazure.mail import collection_models as mail_collection_models  # noqa: F401
from mockazure.mail import message_models as mail_message_models  # noqa: F401
from mockazure.mail import models as mail_models  # noqa: F401
from mockazure.managed_identity import models as managed_identity_models  # noqa: F401
from mockazure.security import models as security_models  # noqa: F401

connection = context.config.attributes["connection"]
context.configure(connection=connection, target_metadata=Base.metadata)
with context.begin_transaction():
    context.run_migrations()
