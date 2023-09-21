import asyncio
import pickle
import time
import os
from asyncio.streams import StreamReader, StreamWriter
from dateutil.parser import parse
from utils.logger import get_logger
from utils.config_getter import get_config

configs = get_config()
log = get_logger(configs.server_logs)


class Server:
    """
    Сервер для работы чата с возможностями переписки в общем или приватных чатах.
    - Реализована возможность подключения пользователей с разных устройств, идентификация по уникальному имени пользователя
    - Прочитанные сообщения удаляются автоматически через час хранения
    - Реализована возможность отправки жалобы на одного из пользователей. По появлении 3 страйков - пользователь уходит в 
    бан и не может отправлять сообщения в течение 4 часов
    
    """

    def __init__(self, host: str = configs.host, port: int = configs.port):
        self.host = host
        self.port = port
        self.clients_writers_dict = {}
        self.common_chat_messages = []
        self.users_statuses_dict = {}
        self.suspended_messages = []

    async def listen(self):
        """ Запуск сервера на прослушивание сокета """

        srv = await asyncio.start_server(
            self.client_connected, self.host, self.port)
        async with srv:
            await srv.serve_forever()

    def get_username(self, writer):
        """ Получение имени подключенного пользователя по объекту writer """
        if self.clients_writers_dict:
            for name, writers_list in self.clients_writers_dict.items():
                if writer in writers_list:
                    result = name
                    break
                result = 'UnknownUser'
        else:
            result = 'UnknownUser'
            
        return result

    async def client_connected(self, reader: StreamReader, writer: StreamWriter):
        """ Прием и обработка сообщения от пользователя """
        
        address = writer.get_extra_info('peername')
        log.info('Start serving %s', address)
        while True:
            data = await reader.read(configs.reader_chunk_size)
            log.info(f'client connected from socket {address}')
            if not data:
                username = self.get_username(writer)
                if username == 'UnknownUser':
                    log.info('Not registered client finished connection')
                else:
                    self.clients_writers_dict[username].remove(writer)
                    log.info(f'user {username} disconnected')
                break
            else:
                username = self.get_username(writer)
                if username not in self.clients_writers_dict or writer not in self.clients_writers_dict[username]:
                    if b'/connect&name' in data:
                        await self.authorize_user(writer, data)
                    else:
                        log.info(f'client from socket {address} tried to send request without authorization')
                        await self.send_response(writer, 'Not authorised')
                else:
                    await self.classify_request(data, writer)
            self.dump_chat_history()
        log.info('Stop serving %s', address)
        writer.close()

    async def classify_request(self, data, writer):
        """ Определение сущности запроса и проброс его на правильный метод класса для выдачи ответа """

        if b'/connect&name' in data:
            # /connect&name=Fiel
            await self.authorize_user(writer, data)
        elif b'/status' in data:
            # /status
            await self.send_chat_status(writer)
        elif b'/chat/send' in data:
            # /chat/send&data=Hello
            await self.post_chat_message(writer, data)
        elif b'/user/push_strike&name=' in data:
            # /user/push_strike&name=Fiel
            await self.push_strike_to_user(writer, data)
        elif b'/user/send_message&name=' in data:
            # /user/send_message&name=Fiel&message=hi my friend 
            await self.send_private_message(writer, data)
        elif b'/chat/read_last_messages' in data:
            await self.read_common_messages(writer)
        elif b'/receive_messages&type=' in data:
            # /receive_messages&type=private (common)
            await self.receive_messages(writer, data)
        else:
            await self.send_response(writer, 'Bad request, status 400')

    async def receive_messages(self, writer: StreamWriter, data: bytes):
        """ Получение пользователей сообщений из общего или приватного чата """

        sender_name = self.get_username(writer)
        messages_type = data.decode().split('=')[1].strip('\r\n')
        unread_messages = self.users_statuses_dict[sender_name][f'{messages_type}_unread_messages']
        if unread_messages:
            messages = '\r\n'.join(unread_messages)
            self.users_statuses_dict[sender_name][f'{messages_type}_unread_messages'] = []
            await self.send_response(writer, messages)
        else: 
            message = f'no unread messages in {messages_type} category'
            await self.send_response(writer, message)
        log.info(f'user {sender_name} got unread messages from {messages_type} chat')

    async def send_private_message(self, writer: StreamWriter, data: bytes):
        """ Отправка личного сообщения одному из пользователей """

        is_banned = await self.check_if_banned(writer)
        sender_name = self.get_username(writer)
        if not is_banned: 
            addressee_name = data.decode().strip('\r\n').split('&')[1].split('=')[1]
            message_text = data.decode().strip('\r\n').split('&')[2].split('=')[1]
            message_text_res = f'{time.asctime()}::{sender_name}::{message_text}'
            self.users_statuses_dict[addressee_name]['private_unread_messages'].append(message_text_res)
            await self.send_response(writer, 'private message sent')
            log.info(f'{sender_name} sent a message to {addressee_name}')
        else:
            log.info(f'banned user {sender_name} tried to send a message to a private chat')
            await self.send_response(writer, 'you are banned')

    async def push_strike_to_user(self, writer: StreamWriter, data: bytes, strikes_limit: int = configs.strikes_limit):
        addressee_name = data.decode().strip('\r\n').split('&')[1].split('=')[1]
        self.users_statuses_dict[addressee_name]['strikes'] += 1
        log.info(f'{addressee_name} got new strike')
        if self.users_statuses_dict[addressee_name]['strikes'] > strikes_limit:
            self.users_statuses_dict[addressee_name]['ban_started_at'] = time.asctime()
            log.info(f'{addressee_name} got banned')
            self.users_statuses_dict[addressee_name]['strikes'] = 0
        await self.send_response(writer, f'Strike pushed to {addressee_name}')

    async def authorize_user(self, writer: StreamWriter, data: bytes):
        """ Авторизация пользователя на сервере """

        client_name = data.decode().split('&')[1].split('=')[1].strip('\r\n')
        if client_name not in self.users_statuses_dict:
            self.create_new_user(client_name)    
        if client_name not in self.clients_writers_dict:
            self.clients_writers_dict[client_name] = []
        if writer not in self.clients_writers_dict[client_name]:
            self.clients_writers_dict[client_name].append(writer)
            await self.send_response(writer, 'connected')
            log.info(f'{client_name} connected')
        else:
            messages_for_client_str = ('already connected')
            await self.send_response(writer, messages_for_client_str)
            log.info(f'{client_name} tried to connected from the same address')

    def create_new_user(self, client_name: str) -> None:
        """ Создание профиля для нового пользователя """
        
        self.users_statuses_dict[client_name] = {}
        self.users_statuses_dict[client_name]['private_unread_messages'] = []
        self.users_statuses_dict[client_name]['common_unread_messages'] = []
        self.users_statuses_dict[client_name]['strikes'] = 0
        self.users_statuses_dict[client_name]['ban_started_at'] = None
        self.clients_writers_dict[client_name] = []
        log.info(f'new user {client_name} registered')

    async def send_chat_status(self, writer: StreamWriter):
        """ Отправка пользователю информации о статусе чата и количестве непрочитанных сообщений """

        client_name = self.get_username(writer)
        is_banned = not self.users_statuses_dict[client_name]['ban_started_at'] is None
        unread_common_messages_amount = str(len(self.users_statuses_dict[client_name]['common_unread_messages']))
        unread_private_messages_amount = str(len(self.users_statuses_dict[client_name]['private_unread_messages']))
        messages_for_client_str = f'is banned: {is_banned} \r\nUsers: ' + str(', '.join(list(self.users_statuses_dict.keys())) + '\r\n' +
                                                        f'unread_common: {unread_common_messages_amount}' + '\r\n' +
                                                        f'unread_private: {unread_private_messages_amount}')
        await self.send_response(writer, messages_for_client_str)
        log.info(f'{client_name} received status info')

    async def read_common_messages(self, writer: StreamWriter, messages_number: int = 20):
        """ Отправка пользователю N последних сообщений из общего чата """

        await self.send_response(writer, '\r\n'.join(self.common_chat_messages[-messages_number:]))

    async def post_chat_message(self, writer: StreamWriter, data: bytes):
        """ Отправка сообщения от пользователя в общий чат """

        is_banned = await self.check_if_banned(writer)
        username = self.get_username(writer)
        if not is_banned:
            message = f'{time.asctime()}::{username}::{data.decode().split("&")[-1].split("=")[-1]}'
            self.common_chat_messages.append(message)
            for addressee in self.users_statuses_dict:
                if addressee in self.clients_writers_dict.keys():
                    for writer in self.clients_writers_dict[addressee]:
                        self.send_response(writer, message)
                        log.info(f'user {addressee} is online, message from {username} delivered')
                else:
                    self.users_statuses_dict[addressee]['common_unread_messages'].append(message)
                    log.info(f'user {addressee} got unread message from {username}')
            log.info(f'{username} sent a message to a common chat')
            await self.send_response(writer, 'received')
        else:
            log.info(f'banned user {username} tried to send a message to a common chat')
            await self.send_response(writer, 'you are banned')
        
    async def check_if_banned(self, writer: StreamWriter, ban_time: int = configs.ban_period):
        """ Проверка пользователя на бан """

        username = self.get_username(writer)
        ban_start_time = self.users_statuses_dict[username]['ban_started_at']
        if ban_start_time:
            ban_period = parse(time.asctime()) - parse(ban_start_time)
            is_banned = ban_period.seconds < ban_time
            if not is_banned:
                self.users_statuses_dict[username]['ban_started_at'] = None
        else:
            is_banned = False
        log.info(f'{username} got his ban information - {is_banned}')

        return is_banned
        
    async def send_response(self, writer: StreamWriter, message: str):
        """ Отправка по сокету сообщения пользователю """

        username = self.get_username(writer)
        message += '\r\n'
        if username == 'UnknownUser':
            writer.write(message.encode())
            await writer.drain()
        elif self.clients_writers_dict[username]:
            for writer_elem in set(self.clients_writers_dict[username]):
                writer_elem.write(message.encode())
                await writer_elem.drain()
        log.info('feedback given')

    async def check_messages_lifetime(self, life_time: int = configs.messages_lifetime):
        """ Проверка прочитанных сообщений на устаревание. Устаревшие сообщения подлежат удалению """

        while True:
            if self.common_chat_messages:
                for message in self.common_chat_messages:
                    life_long = parse(time.asctime()) - parse(message.split('::')[0])
                    if life_long.seconds > life_time:
                        self.common_chat_messages.remove(message)
                        log.info(f'message {message} deleted - time has come')  
            self.dump_chat_history()
            await asyncio.sleep(60, result=None)

    def restore_chat_history(self):
        """ Восстановление истории работы сервера """

        if os.path.exists(configs.common_chat_dump):
            with open(configs.common_chat_dump, 'rb') as pickle_file:
                self.common_chat_messages = pickle.load(pickle_file)

        if os.path.exists(configs.users_status_dump):
            with open(configs.users_status_dump, 'rb') as pickle_file:
                self.users_statuses_dict = pickle.load(pickle_file)
        log.info('server history restored')

    def dump_chat_history(self):
        """ Восстановление истории работы сервера """

        with open(configs.common_chat_dump, 'wb') as pickle_file:
            pickle.dump(self.common_chat_messages, pickle_file)
        with open(configs.users_status_dump, 'wb') as pickle_file:
            pickle.dump(self.users_statuses_dict, pickle_file)
        log.info('users statuses dumped')

async def main(server):
    tasks = [server.listen(), server.check_messages_lifetime()]
    await asyncio.gather(*tasks)


if __name__ == '__main__':
    server = Server()
    server.restore_chat_history()
    asyncio.run(main(server))
