# Questions:
# > * The prompt suggests that stocks are only for a single company but I want
# >   to make sure I'm not expected to build a
# >   stock-certificate-generator-as-a-service for multiple companies,
# >   eventually. Should we assume that the issued stock certificates are always
# >   for a single company (i.e. hard-coded)?

# Yes, just assume that it's for a single company.

# > * What command will be executed to measure performance? What is the type of
# >   hardware the server will run on?

# Glad you asked. My colleague just wanted candidates to keep performance in
# mind while coding. To make it concrete, let's use apache benchmark. `ab -n
# 5000 -c 20 http://localhost:3000/` for 20 concurrent requests. 99% of the
# requests should be < 100ms. Keep # requests per second in the thousands.

# > * Should we assume that there will be low network-latency overhead between
# >   the client and server? Will the client issuing requests run on the same
# >   machine as the server?

# For this task, assume there's a low network-latency overhead between client
# and server. In the real world, that wouldn't be the case. If you have
# experience about how this would change your solution, just attach a small
# writeup in the readme.

# > * Is the server meant to run on a distributed environment or just as a
# >   standalone program from the command line on a single machine as part of
# >   this exercise?

# It's meant to be a standalone program on a single machine as a part of this
# exercise. If you have experience about how this would change your solution,
# just attach a small writeup in the readme.

# > * Should the generated certificate IDs persist between server restarts for
# >   this exercise or can we assume that upon server restart, the certificates
# >   issued will restart at 1?

# generated certificate IDs should persist between server restarts.

# > * Can we assume that the request will always be valid for this exercise or
# >   should we do some input validation/security enforcement?

# you can assume requests will be valid.

# > * Can we assume that the number of shares on the response will always be
# >   equal to the number of shares from the request? I.e. there are no
# >   shortages of shares (someone buys them all)

# No, good question. There is a total number of authorized shares for a share
# class, when the share class is initialized. If there are not enough shares
# available for a share certificate, it should return an http error. I'll add
# this detail to the doc.


request = {
    'name': 'Salt Bae',
    'amount': 10,
    'class': 'CS'  # PS
}

response = {
    'copmany': 'Impossible Cuts Inc.',
    'owner': 'Salt Bae',
    'shares': 10,
    'cert_id': 'CS-32'  # PS-11
}

VALID_SHARE_CLASSES = set(['CS', 'PS'])

def increment(last_at=0):
    """
    A count generator.

    Keywords:
    * `last_at` represents the last known counter generated, defaults to 0.

    """
    i = last_at
    while True:
        i += 1
        yield i


from collections import defaultdict
# Holds the counters for each share class.
# `defaultdict` allows creating keys with a default value upon looking up of a
# non-existent key.
id_generator_by_share_class = defaultdict(increment)


def generate_cert(company, share_class, stakeholder, share_amount):
    id_generator = id_generator_by_share_class[share_class]
    return {
        'id': f'{share_class}-{next(id_generator)}',
        'company': company,
        'stakeholder': stakeholder,
        'amount': share_amount,
    }


class StockGeneratorApp:
    pass


async def save_state():
    pass


async def restore_state():
    pass


async def lifespan_handler(scope):
    pass



import orjson as json
COMPANY_NAME = 'Impossible Cuts Inc.'

async def request_handler(scope, receive, send):
    message = await receive()
    # print('message:', message)
    payload = json.loads(message['body'])
    response_headers_event = {
        'type': 'http.response.start',
        'status': 200,
        'headers': [
            [b'content-type', b'application/json; charset=utf-8'],
        ]
    }
    try:
        share_cert = generate_cert(
            COMPANY_NAME,
            payload['class'],
            payload['name'],
            payload['amount']
        )
        response_body_event = {
            'type': 'http.response.body',
            'body': share_cert,
        }
    except Exception as err:
        response_headers_event['status'] = 500
        response_body_event = {
            'type': 'http.response.body',
            'body': {'error': str(err)},
        }

    yield response_headers_event

    response_body_event['body'] = json.dumps(
        response_body_event['body']
    )

    yield response_body_event


scope_handlers_by_scope_type = {
    'lifespan': lifespan_handler,
    'http': request_handler
}


async def app(scope, receive, send):
    # TODO: With scope = {'type': 'lifespan'}, save counter states to disk on
    # shutdown.
    scope_handler = scope_handlers_by_scope_type[scope['type']]
    async for response in scope_handler(scope, receive, send):
        await send(response)
