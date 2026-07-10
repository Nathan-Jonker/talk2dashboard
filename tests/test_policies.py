from __future__ import annotations

from talk2dashboard.integrations.places import PlacesClient


async def test_places_enforces_five_kilometre_limit(services):
    settings, database, _sources, _query, _dashboard = services
    client = PlacesClient(settings, database)
    try:
        await client.nearby(
            latitude=51.7,
            longitude=4.6,
            included_types=["school"],
            radius_m=5001,
            max_results=5,
            rank="distance",
            fields_profile="minimal",
        )
    except ValueError as exc:
        assert str(exc) == "radius_m must be between 1 and 5000"
    else:
        raise AssertionError("radius above five kilometres must fail before API access")


def test_public_config_contains_no_secret_values(services):
    settings, _database, _sources, _query, _dashboard = services
    settings = settings.model_copy(
        update={"elevenlabs_api_key": "secret-value", "knmi_api_key": "knmi-secret"}
    )

    serialized = str(settings.public_config())

    assert "secret-value" not in serialized
    assert "knmi-secret" not in serialized
    assert settings.public_config()["voice"]["configured"] is True
