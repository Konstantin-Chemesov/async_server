import asyncio
from typing import Coroutine
from utils.config_getter import get_config
from utils.logger import get_logger
from aioconsole import ainput


configs = get_config()
log = get_logger(configs.client_logs)


class Client:
    def __init__(self, name, server_host: str = configs.host, server_port: int = configs.port):
        self.server_host = server_host
        self.server_port = server_port
        self.name = name
        self.messages_sent = {}

    async def get_connection(self) -> Coroutine:
        """ Подсоединение к серверу по сокету """

        self.reader, self.writer = await asyncio.open_connection(self.server_host,
                                                                 self.server_port)
        while True:
            result = await self.get_response_from_server()

    async def authorize(self) -> Coroutine:
        """ Запрос к серверу на создание связи и авторизацию пользователя """

        await self.send_request_to_server(f'/connect&name={self.name}')

    async def get_status(self) -> Coroutine:
        """ Получение статуса чата и информации о пользователе """

        await self.send_request_to_server('/status')
    
    async def send_message_to_common_chat(self, message) -> Coroutine:
        """ Отправка сообщения в общий чат """

        await self.send_request_to_server(f'/chat/send&data={message}')

    async def push_strike(self, username: str) -> Coroutine:
        """ Отправка жалобы на пользователя """

        await self.send_request_to_server(f'/user/push_strike&name={username}')

    async def send_private_message(self, username: str, message: str) -> Coroutine:
        """ Отправка жалобы на пользователя """

        await self.send_request_to_server(f'/user/send_message&name={username}&message={message}')

    async def recieve_messages(self, type: str) -> Coroutine:
        """ Получение сообщений для пользователя """

        await self.send_request_to_server(f'/receive_messages&type={type}')

    async def get_response_from_server(self) -> Coroutine:
        """ Получение обратной связи от сервера """

        while True:
            data = await self.reader.read(configs.reader_chunk_size)
            log.info(f'Received: {data.decode()!r}')
            if not data or data == '':
                log.info('disconnected')
                exit()

    async def send_request_to_server(self, message) -> Coroutine:
        """ Отправление запроса на сервер """

        self.writer.write(message.encode())
        await self.writer.drain()
        log.info(f'Request {message} sent to server')

    def run_async(self, method):
        loop = asyncio.get_event_loop()
        task1 = loop.create_task(method)
        loop.run_until_complete(task1)

    async def main(self):

        tasks = [client.run_console(), client.get_connection()]
        await asyncio.gather(*tasks)
    
    async def run_console(self):

        while True:
            line = await ainput(">>> ")
            if line == 'connect':
                # connect
                await self.authorize()
            elif line == 'status':
                # status
                await self.get_status()
            elif 'send_chat&' in line:
                # send_chat&Hello!
                message = line.split('&')[1]
                await self.send_message_to_common_chat(message)
            elif 'push_strike&user=' in line:
                # push_strike&user=Pacman
                username = line.split('=')[1]
                await self.push_strike(username)
            elif 'send_private&name=' in line:
                # send_private&name=Pacman&data=Hello
                username = line.split('&')[1].split('=')[1]
                message = line.split('&')[-1].split('=')[1]
                await self.send_private_message(username, message)
            elif 'get_unread_chat' in line:
                # get_unread_chat
                await self.recieve_messages('common')
            elif 'get_unread_private' in line:
                # get_unread_private
                await self.recieve_messages('private')
            else:
                print('Unknown command')

if __name__ == '__main__':
    client = Client('Pacman')
    asyncio.run(client.main())


    