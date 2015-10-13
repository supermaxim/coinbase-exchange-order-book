import asyncio
from collections import deque
from dateutil.parser import parse
from decimal import Decimal
import json
import logging
from logging.handlers import RotatingFileHandler
from pprint import pformat
import random
from socket import gaierror
import time

import requests
import websockets

from orderbook.tree import Tree


class Book(object):
    def __init__(self):
        self.matches = deque(maxlen=100)
        self.bids = Tree()
        self.asks = Tree()


file_logger = logging.getLogger('file_log')
file_handler = RotatingFileHandler('log.csv', 'a', 10 * 1024 * 1024, 100)
file_handler.setFormatter(logging.Formatter('%(asctime)s, %(levelname)s, %(message)s'))
file_handler.setLevel(logging.INFO)
file_logger.addHandler(file_handler)

quote_book = Book()


@asyncio.coroutine
def websocket_to_order_book():
    level_3 = None
    try:
        websocket = yield from websockets.connect("wss://ws-feed.exchange.coinbase.com")
    except gaierror:
        file_logger.error('socket.gaierror - had a problem connecting to Coinbase feed')
        return
    yield from websocket.send('{"type": "subscribe", "product_id": "BTC-USD"}')

    last_sequence = None
    while True:
        message = yield from websocket.recv()

        if not level_3:
            level_3 = requests.get('http://api.exchange.coinbase.com/products/BTC-USD/book',
                                   params={'level': 3}).json()
            for bid in level_3['bids']:
                quote_book.bids.insert(bid[2], Decimal(bid[1]), Decimal(bid[0]))
            for ask in level_3['asks']:
                quote_book.asks.insert(ask[2], Decimal(ask[1]), Decimal(ask[0]))

        if message is None:
            file_logger.error('Websocket message is None!')
            raise Exception()

        try:
            message = json.loads(message)
        except TypeError:
            file_logger.error('JSON did not load, see ' + str(message))
            continue

        new_sequence = int(message['sequence'])
        if not last_sequence:
            last_sequence = int(message['sequence'])
        else:
            if (new_sequence - last_sequence - 1) != 0:
                print('sequence gap: {0}'.format(new_sequence - last_sequence))
            last_sequence = new_sequence

        message_type = message['type']
        message_time = parse(message['time'])
        side = message['side']
        if 'order_id' in message:
            order_id = message['order_id']
        if 'maker_order_id' in message:
            maker_order_id = message['maker_order_id']
        if 'price' in message:
            price = Decimal(message['price'])
        if 'remaining_size' in message:
            remaining_size = Decimal(message['remaining_size'])
        if 'size' in message:
            size = Decimal(message['size'])
        if 'new_size' in message:
            new_size = Decimal(message['new_size'])

        if message_type == 'received':
            pass

        elif message_type == 'open' and side == 'buy':
            quote_book.bids.insert(order_id, remaining_size, price)
        elif message_type == 'open' and side == 'sell':
            quote_book.asks.insert(order_id, remaining_size, price)

        elif message_type == 'match' and side == 'buy':
            quote_book.bids.match(maker_order_id, size)
            quote_book.matches.appendleft((message_time, side, size, price))
        elif message_type == 'match' and side == 'sell':
            quote_book.asks.match(maker_order_id, size)
            quote_book.matches.appendleft((message_time, side, size, price))

        elif message_type == 'done' and side == 'buy':
            quote_book.bids.remove_order(order_id)
        elif message_type == 'done' and side == 'sell':
            quote_book.asks.remove_order(order_id)

        elif message_type == 'change' and side == 'buy':
            quote_book.bids.change(order_id, new_size)
        elif message_type == 'change' and side == 'sell':
            quote_book.asks.change(order_id, new_size)

        else:
            print(pformat(message))


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    n = 0
    while True:
        start_time = loop.time()
        loop.run_until_complete(websocket_to_order_book())
        end_time = loop.time()
        seconds = end_time - start_time
        if seconds < 2:
            n += 1
            sleep_time = (2 ** n) + (random.randint(0, 1000) / 1000)
            file_logger.error('Websocket connectivity problem, going to sleep for {0}'.format(sleep_time))
            time.sleep(sleep_time)
            if n > 6:
                n = 0