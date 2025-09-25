from src.gather import fetch_all

def test_fetch():
    items = fetch_all()
    assert isinstance(items, list)
