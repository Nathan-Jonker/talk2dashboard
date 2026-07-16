from talk2dashboard.speech import normalize_dutch_speech


def test_dutch_speech_normalization():
    spoken = normalize_dutch_speech("Om 14:35 was de A12 3,7 km lang met 142 km/u en 21% PM10.")
    assert "veertien uur vijfendertig" in spoken
    assert "de A twaalf" in spoken
    assert "drie komma zeven kilometer" in spoken
    assert "honderdtweeënveertig kilometer per uur" in spoken
    assert "eenentwintig procent" in spoken
    assert "P M tien" in spoken


def test_urls_and_hashes_are_not_spoken_out():
    value = "https://example.nl/x aabbccddeeff0011"
    assert normalize_dutch_speech(value) == value


def test_government_dates_and_units_are_spoken_naturally():
    spoken = normalize_dutch_speech("KNMI en RWS melden op 11-07-2026 18 µg/m³ en 22 °C.")
    assert "K N M I" in spoken
    assert "Rijkswaterstaat" in spoken
    assert "elf juli tweeduizendzesentwintig" in spoken
    assert "microgram per kubieke meter" in spoken
    assert "graden Celsius" in spoken
