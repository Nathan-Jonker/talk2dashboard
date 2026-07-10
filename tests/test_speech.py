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
