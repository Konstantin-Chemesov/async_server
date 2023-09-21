
import logging
import sys

def get_logger(path: str):
    _log_format = f"%(asctime)s - [%(levelname)s] - %(name)s - (%(filename)s).%(funcName)s(%(lineno)d) - %(message)s"
    logging.basicConfig(level=logging.INFO, filename=path,filemode="w", format=_log_format)
    log = logging.getLogger(__name__)
    log.setLevel(logging.INFO)
    log.addHandler(logging.StreamHandler(stream=sys.stdout))

    return log