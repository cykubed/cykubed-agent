_running = True


def is_running() -> bool:
    global _running
    return _running


def shutdown():
    global _running
    _running = False
