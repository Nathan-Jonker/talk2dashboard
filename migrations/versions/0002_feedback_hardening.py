"""Feedback hardening storage.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tool_audit", sa.Column("error_json", sa.Text(), nullable=True))
    op.add_column("tool_audit", sa.Column("turn_id", sa.String(), nullable=True))
    op.create_index("ix_tool_audit_turn_id", "tool_audit", ["turn_id"])
    op.create_table(
        "ephemeral_location_resolutions",
        sa.Column("resolution_id", sa.String(), primary_key=True),
        sa.Column("input_hash", sa.String(), nullable=False),
        sa.Column("google_place_id", sa.String(), nullable=True),
        sa.Column("display_label", sa.Text(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("attribution", sa.String(), nullable=False),
        sa.Column("policy_version", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=False),
    )
    op.create_index("ix_ephemeral_location_input_hash", "ephemeral_location_resolutions", ["input_hash"])
    op.create_index("ix_ephemeral_location_expires_at", "ephemeral_location_resolutions", ["expires_at"])
    op.create_table(
        "claim_audits",
        sa.Column("claim_id", sa.String(), primary_key=True),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("turn_id", sa.String(), nullable=True),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("numeric_value", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
    )
    for column in ("conversation_id", "turn_id", "event_id", "status", "created_at"):
        op.create_index(f"ix_claim_audits_{column}", "claim_audits", [column])


def downgrade() -> None:
    op.drop_table("claim_audits")
    op.drop_table("ephemeral_location_resolutions")
    op.drop_column("tool_audit", "error_json")
    op.drop_index("ix_tool_audit_turn_id", table_name="tool_audit")
    op.drop_column("tool_audit", "turn_id")
