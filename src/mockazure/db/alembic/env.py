from alembic import context

from mockazure.db.models import Base
from mockazure.evidence import models as evidence_models  # noqa: F401

connection = context.config.attributes["connection"]
context.configure(connection=connection, target_metadata=Base.metadata)
with context.begin_transaction():
    context.run_migrations()
