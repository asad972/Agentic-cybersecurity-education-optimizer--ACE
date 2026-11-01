def pytest_configure(config):
    config.addinivalue_line("markers", "flink: runs the stage on a local Flink (needs Java; about 30 s)")
