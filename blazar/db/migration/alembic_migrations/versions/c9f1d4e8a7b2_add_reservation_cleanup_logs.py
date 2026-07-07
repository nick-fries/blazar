# Copyright 2026 OpenStack Foundation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""add reservation_cleanup_logs

Audit table for the orphan-reservation reconciler. Each row records
one observation/decision against one CUSTOM_RESERVATION_<uuid>
resource class -- detected, skipped (allocations or grace period),
deleted, or errored.

Revision ID: c9f1d4e8a7b2
Revises: a7c4e2f9b1d0
Create Date: 2026-05-22 00:00:01.000000

"""

# revision identifiers, used by Alembic.
revision = 'c9f1d4e8a7b2'
down_revision = 'a7c4e2f9b1d0'

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import MEDIUMTEXT


def MediumText():
    return sa.Text().with_variant(MEDIUMTEXT(), 'mysql')


def upgrade():
    op.create_table(
        'reservation_cleanup_logs',
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('reservation_id', sa.String(length=36), nullable=True),
        sa.Column('resource_class_name', sa.String(length=255),
                  nullable=False),
        sa.Column('action', sa.String(length=32), nullable=False),
        sa.Column('triggered_by', sa.String(length=16), nullable=False),
        sa.Column('details', MediumText(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    # Operator queries are by time window, reservation, or class name.
    op.create_index('reservation_cleanup_logs_created_at_idx',
                    'reservation_cleanup_logs', ['created_at'])
    op.create_index('reservation_cleanup_logs_reservation_id_idx',
                    'reservation_cleanup_logs', ['reservation_id'])
    op.create_index('reservation_cleanup_logs_resource_class_name_idx',
                    'reservation_cleanup_logs', ['resource_class_name'])


def downgrade():
    op.drop_table('reservation_cleanup_logs')
