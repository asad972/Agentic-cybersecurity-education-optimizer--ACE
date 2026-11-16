def pytest_configure(config):
    config.addinivalue_line("markers", "flink: runs the stage on a local Flink (needs Java; about 30 s)")
    config.addinivalue_line("markers", "kafka: needs the local Kafka stack and ACE_KAFKA_TESTS=1")
