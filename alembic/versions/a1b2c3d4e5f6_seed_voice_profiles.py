"""seed_voice_profiles

Revision ID: a1b2c3d4e5f6
Revises: 00fb7cb0addb
Create Date: 2026-08-05 02:00:00.000000

Seeds the voice_profiles table with 5 built-in AI voice personalities
for the Browser Voice Agent Demo. Uses ON CONFLICT DO NOTHING so it
is safe to run multiple times.
"""
from typing import Sequence, Union
import uuid
import json

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '00fb7cb0addb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


VOICE_PROFILES = [
    {
        "id": "550e8400-e29b-41d4-a716-446655440001",
        "name": "Sophia",
        "description": "Professional Female",
        "gender": "Female",
        "supported_languages": "English,Hindi",
        "voice_provider": "melotts",
        "voice_configuration": json.dumps({"speaker_id": "EN_INDIA", "speed": 0.95}),
        "status": "active",
        "avatar": None,
        "preview_audio": None,
    },
    {
        "id": "550e8400-e29b-41d4-a716-446655440002",
        "name": "Maya",
        "description": "Friendly Female",
        "gender": "Female",
        "supported_languages": "English,Telugu",
        "voice_provider": "melotts",
        "voice_configuration": json.dumps({"speaker_id": "EN_INDIA", "speed": 1.0}),
        "status": "active",
        "avatar": None,
        "preview_audio": None,
    },
    {
        "id": "550e8400-e29b-41d4-a716-446655440003",
        "name": "Ananya",
        "description": "Customer Support",
        "gender": "Female",
        "supported_languages": "English,Hindi,Telugu",
        "voice_provider": "melotts",
        "voice_configuration": json.dumps({"speaker_id": "EN_INDIA", "speed": 1.05}),
        "status": "active",
        "avatar": None,
        "preview_audio": None,
    },
    {
        "id": "550e8400-e29b-41d4-a716-446655440004",
        "name": "Arjun",
        "description": "Sales Specialist",
        "gender": "Male",
        "supported_languages": "English,Hindi",
        "voice_provider": "melotts",
        "voice_configuration": json.dumps({"speaker_id": "EN_INDIA", "speed": 1.0}),
        "status": "active",
        "avatar": None,
        "preview_audio": None,
    },
    {
        "id": "550e8400-e29b-41d4-a716-446655440005",
        "name": "David",
        "description": "Enterprise Consultant",
        "gender": "Male",
        "supported_languages": "English",
        "voice_provider": "melotts",
        "voice_configuration": json.dumps({"speaker_id": "EN_US", "speed": 0.98}),
        "status": "active",
        "avatar": None,
        "preview_audio": None,
    },
]


def upgrade() -> None:
    """Seed voice_profiles table with default AI voice personalities."""
    conn = op.get_bind()

    for profile in VOICE_PROFILES:
        # Use INSERT ... ON CONFLICT DO NOTHING so re-runs are idempotent
        conn.execute(
            sa.text("""
                INSERT INTO voice_profiles
                    (id, name, description, gender, supported_languages,
                     voice_provider, voice_configuration, preview_audio,
                     avatar, status, created_at, updated_at)
                VALUES
                    (:id, :name, :description, :gender, :supported_languages,
                     :voice_provider, :voice_configuration, :preview_audio,
                     :avatar, :status, now(), now())
                ON CONFLICT (id) DO NOTHING
            """),
            profile
        )


def downgrade() -> None:
    """Remove seeded voice profiles."""
    conn = op.get_bind()
    for profile in VOICE_PROFILES:
        conn.execute(
            sa.text("DELETE FROM voice_profiles WHERE id = :id"),
            {"id": profile["id"]}
        )
