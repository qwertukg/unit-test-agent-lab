from product.pricing import total


def test_regular_total():
    assert total(1000, False) == 1000
