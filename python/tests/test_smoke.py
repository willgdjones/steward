import steward


def test_package_importable():
    assert steward.__version__ == "0.0.0"
