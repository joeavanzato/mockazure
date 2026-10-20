from alembic import context

from mockazure.activity import models as activity_models  # noqa: F401
from mockazure.auditlog import models as auditlog_models  # noqa: F401
from mockazure.calendar import models as calendar_models  # noqa: F401
from mockazure.cloud import models as cloud_models  # noqa: F401
from mockazure.collections import models as collection_models  # noqa: F401
from mockazure.console import models as console_models  # noqa: F401
from mockazure.content import models as content_models  # noqa: F401
from mockazure.db.models import Base
from mockazure.evidence import models as evidence_models  # noqa: F401
from mockazure.identity import (
    deleted_models,  # noqa: F401
    invitation_models,  # noqa: F401
)
from mockazure.keyvault import models as vault_models  # noqa: F401
from mockazure.keyvault import secret_models  # noqa: F401
from mockazure.licensing import models as licensing_models  # noqa: F401
from mockazure.mail import collection_models as mail_collection_models  # noqa: F401
from mockazure.mail import (
    delegation_models,  # noqa: F401
    role_management_models,  # noqa: F401
    security_configuration_models,  # noqa: F401
    send_models,  # noqa: F401
    unified_audit_models,  # noqa: F401
)
from mockazure.mail import message_models as mail_message_models  # noqa: F401
from mockazure.mail import models as mail_models  # noqa: F401
from mockazure.managed_identity import models as managed_identity_models  # noqa: F401
from mockazure.monitor import (
    diagnostic_models,  # noqa: F401
    log_models,  # noqa: F401
)
from mockazure.monitor import workspaces as workspace_models  # noqa: F401
from mockazure.notifications import models as notification_models  # noqa: F401
from mockazure.population import identity_models  # noqa: F401
from mockazure.population import models as population_models  # noqa: F401
from mockazure.security import (
    authentication_models,  # noqa: F401
    reset_model,  # noqa: F401
)
from mockazure.security import models as security_models  # noqa: F401
from mockazure.security.pim import models as pim_models  # noqa: F401
from mockazure.storage import (
    blob_models,  # noqa: F401
    file_models,  # noqa: F401
    queue_models,  # noqa: F401
    table_models,  # noqa: F401
)
from mockazure.storage import models as storage_models  # noqa: F401
from mockazure.workloads import models as workload_models  # noqa: F401

connection = context.config.attributes["connection"]
context.configure(connection=connection, target_metadata=Base.metadata)
with context.begin_transaction():
    context.run_migrations()
