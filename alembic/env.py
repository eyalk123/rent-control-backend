import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy import create_engine
from alembic import context

from app.config import settings
from app.models.base import Base
from app.models.cpi_index import CpiIndex
from app.models.device_token import DeviceToken
from app.models.document_extraction_log import DocumentExtractionLog
from app.models.expense_category import ExpenseCategory
from app.models.job_run import JobRun
from app.models.notification import Notification
from app.models.notification_rule import NotificationRule
from app.models.notification_settings import NotificationSettings
from app.models.owner import Owner
from app.models.property import Property
from app.models.property_file import PropertyFile
from app.models.renter import Renter
from app.models.report_export import ReportExport
from app.models.supplier import Supplier
from app.models.transaction import Transaction

# this is the Alembic Config object
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here for 'autogenerate' support
target_metadata = Base.metadata

# Override sqlalchemy.url from config/settings
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
