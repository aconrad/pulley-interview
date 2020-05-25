# Pulley coding challenge

## Installation

After cloning the repository and with Python>=3.7 installed, run the following
commands:

```
python3 -m venv venv
. venv/bin/activate
pip install uvicorn orjson gunicorn
```

## Running

Once installed, we will have to start two services:
* The Stock Certificate API service to handle incoming JSON requests from
  clients.
* A Stock Inventory Service that grants stock certificates as long as the
  requested amount of shares to be granted is available.

### Stock Certificate API (JSON HTTP API frontend server)

This command will run the API server, `StockCertificateApi`. It handles incoming
requests from `ab`.

```
gunicorn -w `sysctl -n hw.logicalcpu` -k uvicorn.workers.UvicornWorker stock_cert_gen_uvicorn:app
```

The server will spawn one process per logical CPUs on the Mac. If you don't have
a Mac, replace `sysctl -n hw.logicalcpu` with the number of CPU cores x 2.

### Stock Inventory Service (TCP backend server)

This will run `StockInventoryService`.

```
python3 stock_cert_server.py
```

## Benchmark

Let's prepare a JSON file that we will use as our request payload for each
request:

```
cat > salt_bae_buys_CS.data <<EOF
{"name":"Salt Bae","amount":10,"class":"CS"}
EOF
```

Then we will use Apache Benchmark (command `ab`) to measure server performance.

```
ab -n 10000 -c 20 -T 'application/json' -p ./salt_bae_buys_CS.data 'http://127.0.0.1:8000/'
```

```
This is ApacheBench, Version 2.3 <$Revision: 1843412 $>
Copyright 1996 Adam Twiss, Zeus Technology Ltd, http://www.zeustech.net/
Licensed to The Apache Software Foundation, http://www.apache.org/

Benchmarking 127.0.0.1 (be patient)
Completed 1000 requests
Completed 2000 requests
Completed 3000 requests
Completed 4000 requests
Completed 5000 requests
Completed 6000 requests
Completed 7000 requests
Completed 8000 requests
Completed 9000 requests
Completed 10000 requests
Finished 10000 requests


Server Software:        uvicorn
Server Hostname:        127.0.0.1
Server Port:            8000

Document Path:          /
Document Length:        103 bytes

Concurrency Level:      20
Time taken for tests:   0.727 seconds
Complete requests:      10000
Failed requests:        5001
   (Connect: 0, Receive: 0, Length: 5001, Exceptions: 0)
Non-2xx responses:      5001
Total transferred:      2244947 bytes
Total body sent:        1800000
HTML transferred:       729940 bytes
Requests per second:    13750.96 [#/sec] (mean)
Time per request:       1.454 [ms] (mean)
Time per request:       0.073 [ms] (mean, across all concurrent requests)
Transfer rate:          3014.67 [Kbytes/sec] received
                        2417.16 kb/s sent
                        5431.83 kb/s total

Connection Times (ms)
              min  mean[+/-sd] median   max
Connect:        0    0   0.1      0       1
Processing:     1    1   0.9      1      21
Waiting:        1    1   0.9      1      21
Total:          1    1   0.9      1      22

Percentage of the requests served within a certain time (ms)
  50%      1
  66%      1
  75%      1
  80%      2
  90%      2
  95%      2
  98%      2
  99%      2
 100%     22 (longest request)
```

## Approach

Before I share my approach to the problem, let me restate the requirements of
the problem:

> Build an API endpoint where clients can concurrently generate valid paper
> certificates.

> The certificate ID is a combination of the share class abbreviation and a
> number. The requirements for this number are:

* This number must start from 1 for a given share class. The very first common
  stock certificate must be `CS-1`. The first preferred stock certificate must
  be `PS-1`.
* This number must be sequential for a given share class. There should be no
  gaps in the certificate numbers.
* This number must be unique for a given share class. There should be no
  duplicates in the certificate numbers.

> Make sure these constraints for the certificate ID holds when concurrent users
> are generating certificates. It'd be bad if two certificates had the same ID
> or if there was a gap between them!

> The API endpoint should be able to respond within 100ms 99% of the time. It
> should also be able to service 10,000 queries per second.

The requirements were later updated with the following:

> Each security class (common and preferred) is initialized with a total number
> of authorized shares. If there aren’t enough shares left to be issued in
> response to a request, it should return an error.

> The API endpoint should be able to respond within 100ms 99% of the time. It
> should also be able to service thousands of queries per second. Use
> apache benchmark with ~20 concurrent requests.
> `ab -n 5000 -c 20 http://localhost:3000/`

> generated certificate IDs should persist between server restarts

### Build a single Python server

I initially implemented a single-server process in Python that acted as an API
and a simple certificate generator. But unfortunately I couldn't get
passed ~7,000 req/s and the requirement was 10,000 req/s. You can see this
version [here](https://github.com/aconrad/pulley-interview/blob/6683606921c71ede571816343d28ad7a3876793f/stock_cert_server.py).

NOTE: this version did not persist the changes to disk and it didn't register a
total number of shares.

My next step was to spawn multiple processes of the service but that would cause
duplicate certificates to be generated, evidently. We could certainly ingest the
load of `ab` faster if we had multiple processes to serve the requests. So
something had to be modified...

I thought about having a distributed counter (aka
[G-Counter](https://en.wikipedia.org/wiki/Conflict-free_replicated_data_type#G-Counter_(Grow-only_Counter)))
by having each process write the number of issued certificates in their own
file. When we need the next certificate number, we could then aggregate the sum
of the counts. But that approach seemed fragile and might slow us down because
we'd have to read each file (disk access) and sum the counters for every
request.

Also, a detail that was brought to my attention later was that certificate
should no longer issue certificates once the total number of shares have been
distributed. This was more that just a counter, but a bit of inventory
management.

### Build a stock inventory service

So I built a stock inventory service, `StockInventoryService`. With that, I was
at a low ~1,000 req/s because each incoming requests from the API established a
connection to the stock inventory service, and these TCP handshakes can be slow,
even on localhost. I thought about using Unix Domain Socket to reduce the
network overhead a fair amount (x5-7?) but realistically, these services wouldn't
run on the same machine in a real world scenario.

### Introduce a connection pool

I then introduced a connection pool `ConnectionPool` used by
`StockCertificateApi` that eagerly reuses already-established TCP connections to
the stock inventory service. Every request attempts to acquire a connection from
the pool, the pool will check if a connection is available, if not it will
establish a new connection.

With this change, I was running at ~12,000 req/s.

### Don't use JSON between the two services

JSON encoding turned out to be a bottle neck. I mana

## Technology decision

* [Python](https://www.python.org/): I'm most comfortable with Python, despite
  not know for not being the fastest language, I believe I understand the
  language well enough to squeeze the most performance out of it.

* [Uvicorn](https://www.uvicorn.org/): To leverage async/await in Python within
  a web server, I had to use a web framework that supported the newer ASGI
  protocol.

Things I have considered:

* [Redis](https://redis.io/): I thought about using Redis as my backend
  inventory service. Operations are atomic and it's really good at incrementing
  counters. Combined with custom Lua code, Redis would have been a great fit to
  manage a stock inventory. But that would have required additional external
  dependencies and didn't want to make it a hassle to test my code.

* [Starlette](https://www.starlette.io/): Starlette is built on top of Uvicorn
  and provides many useful features for real web application development. But it
  would have added unnecessary overhead for my use case. URL matching/routing,
  HTML templates, GraphQL support, WebSockets, etc. I didn't need any of it for
  this exercise and it would have likely made the service a little slower.

* [Go](https://golang.org/): With a requirement of 10,000 reqs/s, I was unsure
  if I could make it work in Python. I wrote a few barebones "hello world" HTTP
  servers in Javascript, Python, and Go. Python and Javascript were about the
  same, but Go was significantly faster. Yet, Python wasn't far from the goals
  and with multi-processing, I thought I could make it. And the learning curve
  of Go would have been too involved for me.
