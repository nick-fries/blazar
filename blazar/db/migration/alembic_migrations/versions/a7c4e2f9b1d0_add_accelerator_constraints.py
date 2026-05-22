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

"""add accelerator_constraints to instance_reservations

Persists the topology-aware reservation constraints (accelerator
resources, required traits, topology locality, source flavor id) so
that overlapping-window pre-flights can read back the committed
accelerator counts of other leases on the same hosts.

Revision ID: a7c4e2f9b1d0
Revises: 553383923ca0
Create Date: 2026-05-22 00:00:00.000000

"""

# revision identifiers, used by Alembic.
revision = 'a7c4e2f9b1d0'
down_revision = '553383923ca0'

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import MEDIUMTEXT


def MediumText():
    return sa.Text().with_variant(MEDIUMTEXT(), 'mysql')


def upgrade():
    op.add_column(
        'instance_reservations',
        sa.Column('accelerator_constraints', MediumText(), nullable=True))


def downgrade():
    op.drop_column('instance_reservations', 'accelerator_constraints')
