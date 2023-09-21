import yaml
from box import Box

def get_config():
    """ Загрузка файлов с параметрами работы сервера """

    with open("configs/configurations.yaml", "r") as stream:
        try:
            configs = Box(yaml.safe_load(stream))
        except yaml.YAMLError as exc:
            raise exc

    return configs