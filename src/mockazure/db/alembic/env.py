from alembic import context

from mockazure.activity import models as activity_models  # noqa: F401
from mockazure.auditlog import models as auditlog_models  # noqa: F401
from mockazure.cloud import models as cloud_models  # noqa: F401
from mockazure.collections import models as collection_models  # noqa: F401
from mockazure.content import models as content_models  # noqa: F401
from mockazure.db.models import Base
from mockazure.evidence import models as evidence_models  # noqa: F401
from mockazure.keyvault import models as vault_models  # noqa: F401
from mockazure.licensing import models as licensing_models  # noqa: F401
from mockazure.mail import collection_models as mail_collection_models  # noqa: F401
from mockazure.mail import (
    delegation_models,  # noqa: F401
    role_management_models,  # noqa: F401
    security_configuration_models,  # noqa: F401
    unified_audit_models,  # noqa: F401
)
from mockazure.mail import message_models as mail_message_models  # noqa: F401
from mockazure.mail import models as mail_models  # noqa: F401
from mockazure.managed_identity import models as managed_identity_models  # noqa: F401
from mockazure.monitor import workspaces as workspace_models  # noqa: F401
from mockazure.security import (
    authentication_models,  # noqa: F401
    reset_model,  # noqa: F401
)
from mockazure.security import models as security_models  # noqa: F401
from mockazure.storage import blob_models  # noqa: F401
from mockazure.storage import models as storage_models  # noqa: F401
from mockazure.workloads import models as workload_models  # noqa: F401

connection = context.config.attributes["connection"]
context.configure(connection=connection, target_metadata=Base.metadata)
with context.begin_transaction():
    context.run_migrations()
