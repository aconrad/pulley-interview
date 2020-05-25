import asyncio
import logging
import os

from collections import defaultdict
from pathlib import Path

import orjson as json


"""
                        +---(benchmark client)----+
                     +---(benchmark client)----+  |
                  +---(benchmark client)----+  |--+
               +---(benchmark client)----+  |--+
               |    HTTP JSON REQUEST  |--+
               +-----------------------+
                      |   |   |
                      |   |   v
                      |   v
                      v
            +-------------------------------+
            | gunicorn spawns the processes |
            +-------------------------------+

      +------------------process---------------------+
   +------------------process---------------------+  |
+------------------process---------------------+  |  |
|                                              |  |  |
|                +---------+                   |  |  |
|                | uvicorn |                   |  |  |
|                +---------+                   |  |  |
|                     |                        |  |  |
|                     v                        |  |  |
|    +------------------------------------+    |  |  |
|    | StockCertificateApi (frontend API) |    |  |  |
|    |                                    |    |  |  |
|    |         [ConnectionPool]           |    |  |  |
|    +------------------------------------+    |  |  |
|                     |                        |  |--+
|                     v                        |--+
+----------------------------------------------+
                      |   |   |
                      |   |   v
                      |   v
                      v
+-------------------process--------------------+
|                     |                        |
|                     v                        |
|     +---------------------------------+      |
|     | StockInventoryService (backend) |      |
|     +---------------------------------+      |
|                                              |
+----------------------------------------------+
"""

# Configure logging
logging.basicConfig(
    format='%(asctime)s [%(levelname)s] %(message)s',
    level=logging.INFO
    # level=logging.DEBUG
)

###################
# BACKEND SERVICE #
###################


class StockInventoryService:
    """
    This class represents the stock inventory server. It holds information about
    the available share classes, their amount, and how many stock grants have
    been issued for each share class.

    Upon start-up, it will load the provided stock database from disk (if
    available) into memory and start listening to a TCP port.
    """

    def __init__(self, db_filename):
        self.db_filename = Path(db_filename)
        self._shares_inventory = {}
        self._load_data_from_disk()
        self._transaction_log = self.db_filename.open('a')  # open file in append mode

    def register(self, share_class, amount):
        """
        Will add `amount` new shares `share_class` to the database. The action
        will be ignored if `share_class` already exists.
        """
        if share_class in self._shares_inventory:
            return

        self._shares_inventory[share_class] = {
            'issued_certificates': 0,
            'amount': amount,
        }

    def _load_data_from_disk(self):
        if not self.db_filename.exists():
            return

        # Open file, seek to the end of it, and backtrack until we find a `\n`,
        # excluding the trailing `\n`.
        with self.db_filename.open('rb') as f:
            f.seek(-2, os.SEEK_END)  # move to EOF and skip trailing newline
            line_length = 0  # keep track of how long the last line is
            while f.read(1) != b'\n':  # consume 1 char
                f.seek(-2, os.SEEK_CUR)  # backtrack
                line_length += 1
            f.read(1)  # consume the first `|`
            serialized = f.read(line_length - 1).decode()

        for stock in serialized.split('|'):
            share_class, amount, issued_certificates = stock.split(':', 2)
            self._shares_inventory[share_class] = {
                'issued_certificates': int(issued_certificates),
                'amount': int(amount),
            }

    def save(self):
        """
        Persist data to disk.

        The data is serialized to disk in the form:

        ```
        |<share_class>:<amount>:<issued_certificates>[, ...]\n
        ```

        For example:

        ```
        |CS:2587320:1268|PS:694140:5586\n
        ```
        """
        for share_class, share_data in self._shares_inventory.items():
            serialized = f'|{share_class}:{share_data["amount"]}:{share_data["issued_certificates"]}'
            self._transaction_log.write(serialized)
        self._transaction_log.write('\n')

        # TODO: we could imagine a maintenance function to compact the data file
        # on server start-up if the file size became a problem.

    async def grant(self, grant_request):
        """
        Return a grant ID for a request `grant_request`. The number of shares
        from the grant request will be deducted from the total amount of shares
        available.

        If there are insufficient shares to satisfy the request, return `False`.
        """
        logging.debug(grant_request)
        share_class = grant_request['share_class']
        if share_class not in self._shares_inventory:
            return False

        shares = self._shares_inventory[share_class]
        grant_amount = grant_request['share_amount']
        # Note: As long as this server is running in a single thread/process, we
        # shouldn't need locking.
        if shares['amount'] - grant_amount >= 0:
            shares['amount'] -= grant_amount
            shares['issued_certificates'] += 1
            self.save()

            return shares['issued_certificates']

    async def handle_request(self, reader, writer):
        """
        Read the grant request from `reader` and write the new certificate to
        `writer`. If the grant isn't approved, write an error instead.
        """
        request_data = await reader.read(1000)

        # The client may have disconnected
        if not request_data:
            writer.close()
            await writer.wait_closed()
            addr = writer.get_extra_info('peername')
            logging.info(f'Closed connection {addr}')
            return

        request_data = json.loads(request_data)

        # Ignore requests that aren't for a grant, that shouldn't happen...
        if request_data['action'] != 'grant':
            return

        # If we get a `cert_id`, then the grant was approved. Otherwise there
        # might not be enough shares available.
        cert_id = await self.grant(request_data)
        if cert_id:
            response = {
                'cert_id': cert_id,
                'share_class': request_data['share_class'],
                'share_amount': request_data['share_amount'],
            }
        else:
            response = {'error': 'the grant was denied'}

        writer.write(json.dumps(response))
        await writer.drain()

    async def handle_connection(self, reader, writer):
        """
        Handles an incoming connections received by the asyncio loop.
        """
        # Handle a connection forever, never close it so it can be reused by the
        # client for connection pooling purposes.
        addr = writer.get_extra_info('peername')
        logging.info(f'Received connection {addr}')
        while not writer.is_closing():
            await self.handle_request(reader, writer)

    async def serve(self, host, port):
        server = await asyncio.start_server(self.handle_connection, host, port)
        addr = server.sockets[0].getsockname()
        logging.info(f'Serving on {addr}')
        async with server:
            await server.serve_forever()


DB_HOST, DB_PORT = ('127.0.0.1', 8001)

##############################################
# BOOTSTRAP CODE TO START THE BACKEND SERVER #
##############################################


if __name__ == '__main__':
    stock_db = StockInventoryService('stockdb.dat')
    stock_db.register('CS', 2600000)  # 2.6M
    stock_db.register('PS', 750000)  # 750K

    try:
        asyncio.run(stock_db.serve(DB_HOST, DB_PORT))
    except KeyboardInterrupt:
        logging.info("Bye!")


class ConnectionPool:
    """
    A connection pool manager for async TCP streams.
    """
    def __init__(self, host, port):
        self._host = host
        self._port = port
        self._conn_pool = []

    async def _new(self):
        """
        Return a tuple of reader/writer streams.
        """
        reader, writer = await asyncio.open_connection(self._host, self._port)
        addr = writer.get_extra_info('peername')
        logging.info(f'Opened connection {addr}')
        return reader, writer

    async def acquire(self):
        """
        Return reader and writer streams to the database. If no connections are
        available from the DB connection pool, a new one will be spawn.
        """
        if not self._conn_pool:
            reader, writer = await self._new()
        else:
            reader, writer = self._conn_pool.pop()

        return (reader, writer)

    def release(self, reader, writer):
        """
        Return provided `reader` and `writer` streams to the DB connection pool.
        """
        self._conn_pool.append((reader, writer))


####################
# FRONTEND SERVICE #
####################


class StockCertificateApi:
    """
    A web server to handle incoming JSON requests.
    """

    def __init__(self, db_conn_pool, company_name):
        self.company_name = company_name
        self._db_conn_pool = db_conn_pool

    async def _request_grant(self, share_class, share_amount):
        """
        Connect to the database server and request a grant for `share_amount`
        shares of class `share_class`.
        """
        stock_db_reader, stock_db_writer = await self._db_conn_pool.acquire()

        db_request = {
            'action': 'grant',
            'share_class': share_class,
            'share_amount': share_amount
        }
        stock_db_writer.write(json.dumps(db_request))
        await stock_db_writer.drain()

        grant_response = await stock_db_reader.read(1000)
        logging.debug(f'grant_response: {grant_response}')

        if not grant_response:  # the server may have disconnected, retry...
            stock_db_writer.close()
            await stock_db_writer.wait_closed()
            addr = stock_db_writer.get_extra_info('peername')
            logging.info(f'Closed connection {addr}')
            return await self._request_grant(share_class, share_amount)

        self._db_conn_pool.release(stock_db_reader, stock_db_writer)
        return json.loads(grant_response)

    def _generate_cert(self, cert_id, stakeholder, share_class, share_amount):
        """
        Return a share certificate compliant with the API.
        """
        return {
            'cert_id': f'{share_class}-{cert_id}',
            'stakeholder': stakeholder,
            'company': self.company_name,
            'amount': share_amount,
        }

    def _make_response(self, stakeholder, grant_response):
        # Prepare response headers
        response_headers = {
            'type': 'http.response.start',
            'status': 200,
            'headers': [
                [b'content-type', b'application/json; charset=utf-8'],
            ]
        }

        # Prepare response body
        response_body = {
            'type': 'http.response.body',
            'body': None,
        }

        # Fill in the blanks
        if 'error' in grant_response:
            response_headers['status'] = 403  # Forbidden
            response_body['body'] = json.dumps(grant_response)
        else:
            # Generate the output certificate
            grant_certificate = self._generate_cert(
                grant_response['cert_id'],
                stakeholder,
                grant_response['share_class'],
                grant_response['share_amount'],
            )
            response_body['body'] = json.dumps(grant_certificate)

        return (response_headers, response_body)

    async def handle_request(self, scope, receive, send):
        """
        Handles the processing of incoming requests.
        """
        # Read the request payload
        message = await receive()
        payload = json.loads(message['body'])
        logging.debug(f'payload: {payload}')

        # Request the grant
        grant_response = await self._request_grant(
            payload['class'], payload['amount']
        )
        logging.debug(f'grant_response: {grant_response}')

        # Send response to client
        response_headers, response_body = self._make_response(
            payload['name'], grant_response
        )
        yield response_headers
        yield response_body


#################################################################
# FRONTEND SERVICE REQUEST HANDLER CALLED BY THE HTTP FRAMEWORK #
#################################################################

stock_inventory_conn_pool = ConnectionPool(DB_HOST, DB_PORT)
stock_cert_generator = StockCertificateApi(
    stock_inventory_conn_pool,
    'Impossible Cuts Inc.'
)

async def app(scope, receive, send):
    """
    Called by the web framework `uvicorn` upon incoming HTTP requests.
    """
    # Lifespan requests aren't supported by gunicorn, ignore them if we see
    # them.
    if scope['type'] == 'lifespan':
        return

    async for response in stock_cert_generator.handle_request(scope, receive, send):
        await send(response)
