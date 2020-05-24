import asyncio
import logging

from collections import defaultdict
from pathlib import Path

import orjson as json


class StockDb:
    """
    This class represents the stocks database server. It holds information about
    the available share classes, their amount, and how many certificates have
    been issued for each share class.

    Upon start-up, it will load the provided stock database from disk (if
    available) into memory and start listening to a TCP port. Upon exit, the
    database will be persisted to disk.
    """

    def __init__(self, db_filename):
        self.db_filename = Path(db_filename)
        self._load_data_from_disk()

    def register(self, share_class, amount):
        """
        Will add `amount` new shares `share_class` to the database. The action
        will be ignored if `share_class` already exists.
        """
        if share_class in self.shares_inventory:
            return

        self.shares_inventory[share_class] = {
            'issued_certificates': 0,
            'amount': amount,
        }

    def _load_data_from_disk(self):
        if not self.db_filename.exists():
            self.shares_inventory = {}
            return

        with self.db_filename.open('rb') as f:
            self.shares_inventory = json.loads(f.read())

    def save(self):
        """
        Persist database to disk.
        """
        db_dump = json.dumps(self.shares_inventory)
        print('Dumping memory to disk:', db_dump)
        with self.db_filename.open('wb') as f:
            f.write(db_dump)

    async def grant(self, grant_request):
        """
        Return a grant ID for a request `grant_request`. The number of shares
        from the grant request will be deducted from the total amount of
        available shares.

        If there are insufficient shares to satisfy the request, return `False`.
        """
        share_class = grant_request['share_class']
        if share_class not in self.shares_inventory:
            return False

        shares = self.shares_inventory[share_class]
        grant_amount = grant_request['share_amount']
        # TODO: acquire lock? Since it runs in the context of a corouting, it shouldn't be a problem.
        if shares['amount'] - grant_amount >= 0:
            shares['amount'] -= grant_amount
            shares['issued_certificates'] += 1
            # self.save()  # should we save on every update?

            return shares['issued_certificates']

    async def handle_request(self, reader, writer):
        """
        Read the grant request from `reader` and write the new certificate to
        `writer`. If the grant isn't approved, write an error instead.
        """
        request_data = await reader.read(1000)
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
        while True:
            await self.handle_request(reader, writer)

    async def serve(self, host, port):
        server = await asyncio.start_server(self.handle_connection, host, port)
        addr = server.sockets[0].getsockname()
        logging.info(f'Serving on {addr}')
        async with server:
            await server.serve_forever()


DB_HOST, DB_PORT = ('localhost', 8001)

if __name__ == '__main__':
    stock_db = StockDb('stockdb.dat')
    stock_db.register('CS', 2600000)  # 2.6M
    stock_db.register('PS', 750000)  # 750K

    try:
        asyncio.run(stock_db.serve(DB_HOST, DB_PORT))
    except KeyboardInterrupt:
        logging.info("Bye!")
    finally:
        # TODO: we should likely call .save() on a on-going basis to reduce
        # data corruption with power failures or kill -9
        stock_db.save()


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
        return await asyncio.open_connection(self._host, self._port)

    async def acquire(self):
        """
        Return reader and writer streams to the database. If no connections are
        available from the DB connection pool, a new one will be spawn.
        """
        if not self._conn_pool:
            db_reader, db_writer = await self._new()
        else:
            db_reader, db_writer = self._conn_pool.pop()

        return (db_reader, db_writer)

    def release(self, reader, writer):
        """
        Return provided `reader` and `writer` streams to the DB connection pool.
        """
        self._conn_pool.append((reader, writer))


class StockCertificateGeneratorApp:
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


db_connection_pool = ConnectionPool(DB_HOST, DB_PORT)
stock_cert_generator = StockCertificateGeneratorApp(
    db_connection_pool,
    'Impossible Cuts Inc.'
)


async def app(scope, receive, send):
    # Lifespan requests aren't supported by gunicorn, ignore them if we see
    # them.
    if scope['type'] == 'lifespan':
        return

    async for response in stock_cert_generator.handle_request(scope, receive, send):
        await send(response)
