from .. import config


def get_provider():
    if config.USE_MOCK_COMMERCE:
        from . import mock_provider

        return mock_provider
    from . import shopify_provider

    return shopify_provider
