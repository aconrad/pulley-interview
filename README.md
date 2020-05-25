# Pulley coding challenge

## Installation

After cloning the repository and with Python>=3.7 installed, run the following
commands:

```bash
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

```bash
gunicorn -w `sysctl -n hw.logicalcpu` -k uvicorn.workers.UvicornWorker stock_cert_server:app
```

> **NOTE:** ensure the Python vitualenv is activated with `. venv/bin/activate`

The server will spawn one process per logical CPUs on the Mac. If you don't have
a Mac, replace `sysctl -n hw.logicalcpu` with the number of CPU cores x 2.

### Stock Inventory Service (TCP backend server)

This will run `StockInventoryService`. It's the backend service that tracks
inventory changes. In a separate terminal run:

```bash
python3 stock_cert_server.py
```

> **NOTE:** ensure the Python vitualenv is activated with `. venv/bin/activate`

## Benchmark

Let's prepare a JSON file that we will use as our request payload for each
request:

```bash
# Common stock
cat > salt_bae_buys_CS.data <<EOF
{"name":"Salt Bae","amount":10,"class":"CS"}
EOF

# Preferred stock
cat > salt_bae_buys_PS.data <<EOF
{"name":"Salt Bae","amount":10,"class":"PS"}
EOF
```

Then we will use Apache Benchmark (command `ab`) to measure server performance.

```bash
# Request common stock
ab -n 10000 -c 20 -T 'application/json' -p ./salt_bae_buys_CS.data 'http://127.0.0.1:8000/'

# Request preferred stock
ab -n 10000 -c 20 -T 'application/json' -p ./salt_bae_buys_PS.data 'http://127.0.0.1:8000/'
```

> **NOTE:** I noticed that running `ab` twice in a row on MacOs must put some
sort of strain on the kernel/network and causes the second run to hang for a few
seconds at ~6000 requests but it eventually finishes within a few more seconds.
I give it ~40 seconds between runs to show best results.

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

## Benchmark with `wrk` (instead of `ab`)

Because `ab` seemed so finicky, I took a look at other benchmarking tools and
`wrk` seems popular, and was a lot more stable in my experience. Install `wrk`:

```bash
brew install wrk
```

Prep the `POST` data:

```bash
cat > salt-bae-1share-ps.lua<<EOF
wrk.method = "POST"
wrk.body = '{"name":"Salt Bae","amount":1,"class":"PS"}'
wrk.headers["Content-Type"] = "application/json"
EOF

cat > salt-bae-1share-cs.lua<<EOF
wrk.method = "POST"
wrk.body = '{"name":"Salt Bae","amount":1,"class":"CS"}'
wrk.headers["Content-Type"] = "application/json"
EOF
```

Then run the benchmark (it will go for 10 seconds by default):

```bash
wrk -s salt-bae-1share-cs.lua -c 20 -t 20 http://127.0.0.1:8000/
wrk -s salt-bae-1share-ps.lua -c 20 -t 20 http://127.0.0.1:8000/
#   ^ use .lua file           ^     ^ 20 threads
#                             | 20 concurrent requests
```

Results:

```
Running 10s test @ http://127.0.0.1:8000/
  20 threads and 20 connections
  Thread Stats   Avg      Stdev     Max   +/- Stdev
    Latency     1.50ms  292.17us   7.77ms   69.54%
    Req/Sec   669.61     91.80     0.88k    70.25%
  133438 requests in 10.01s, 31.84MB read
Requests/sec:  13324.21
Transfer/sec:      3.18MB
```

> **NOTE:** Don't confuse `Req/Sec` (per thread) with `Requests/sec` (total).

Running it multiple times in a row show more consistent results compared by
`ab`.

> **NOTE:** you might get 403s after you run out of shares and if you do the
mention "Non-2xx or 3xx responses" will appear:

```
Running 10s test @ http://127.0.0.1:8000/
  20 threads and 20 connections
  Thread Stats   Avg      Stdev     Max   +/- Stdev
    Latency     1.50ms  277.19us   3.82ms   65.93%
    Req/Sec   669.63     86.49     0.90k    68.25%
  133417 requests in 10.01s, 28.82MB read
  Non-2xx or 3xx responses: 61643
Requests/sec:  13322.89
Transfer/sec:      2.88MB
```

## Approach

Before I share my approach to the problem, let me restate the requirements:

> Build an API endpoint where clients can concurrently generate valid paper
> certificates.

> The certificate ID is a combination of the share class abbreviation and a
> number. The requirements for this number are:

> * This number must start from 1 for a given share class. The very first common
  stock certificate must be `CS-1`. The first preferred stock certificate must
  be `PS-1`.
> * This number must be sequential for a given share class. There should be no
  gaps in the certificate numbers.
> * This number must be unique for a given share class. There should be no
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

> **NOTE:** this version did not persist the changes to disk and it didn't
register a total number of shares.

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
even on localhost.

### Introduce a connection pool

I then introduced a connection pool `ConnectionPool` used by
`StockCertificateApi` that eagerly reuses already-established TCP connections to
the stock inventory service. Every request attempts to acquire a connection from
the pool, the pool will check if a connection is available, if not it will
establish a new connection.

With this change, I was running at ~12,000 req/s.

### Data persistence

At first, I was saving the state of the stock inventory service by JSON-dumping
the in-memory inventory data upon quitting the server (CTRL+C). That worked well
but if the server were to crash (or `kill -9`), we could lose track of
already-issued stock certificates.

So I dumped the inventory data to a file every time the inventory changed. As
expected, it brought the backend server to a crawl because of the disk I/O to
rewrite the file, and likely JSON serialization on top of that.

I still wanted to keep track of all the data changes for traceability purposes,
so I decided that I would write a log of all the transactions in a file called
`stockdb.dat`. This is done by opening the file in "append" mode. So whenever
the stock inventory changes, you can see all the changes happening in this file.
It's also a nice way to check that no certificates are duplicated or have gaps.

You see the inventory updates in realtime by running:

```bash
tail -f stockdb.dat
```

To squeeze in more speed, I decided to write my own (basic) serialization
protocol to save me from the the slower JSON serialization process. Got an
additional ~1,000 req/s.

When the backend starts, it finds the last line in `stockdb.dat` and uses it as
the latest known transaction so it can restart where it left off.

## Technology decisions

* [Python](https://www.python.org/): I'm most comfortable with Python, despite
  not known for not being the fastest language, I understand the language well
  enough to squeeze the most performance out of it.

* [Uvicorn](https://www.uvicorn.org/): To leverage async/await in Python within
  a web server, I had to use a web framework that supported the newer
  [ASGI](https://asgi.readthedocs.io/en/latest/specs/main.html) protocol.
  Uvicorn implements the ASGI protocol but it's somewhat bare in terms of
  features for your everyday web framework.

Other technologies considered:

* [Redis](https://redis.io/): I thought about using Redis as my backend
  inventory service. Operations are atomic and it's really good at incrementing
  counters. Combined with custom Lua code, Redis would have been a great fit to
  manage a stock inventory. But that would have required additional external
  dependencies and didn't want to make it a hassle to test my code.

* [Starlette](https://www.starlette.io/): Starlette is built on top of Uvicorn
  and provides many useful features for real web application development. But it
  would have added unnecessary overhead for my use case. URL matching/routing,
  HTML templates, GraphQL support, WebSockets, etc. I needed none of it for this
  exercise and it would've likely made the service a little slower despite
  having a nicer API. That said, for everyday coding, it's probably a good
  choice for Python.

* [Go](https://golang.org/): With a requirement of 10,000 reqs/s, I was unsure
  if I could make it work in Python. I wrote a few bare-bones "hello world" HTTP
  servers in Javascript, Python, and Go. Python and Javascript were about the
  same (~8,000 req/s), but Go was significantly faster (~22,000 req/s). Yet,
  Python wasn't far from the goal and I thought I could make it work with
  multi-processing.

* [Unix Domain Socket](https://en.wikipedia.org/wiki/Unix_domain_socket): I
  thought about using Unix Domain Socket to reduce the network overhead a fair
  amount between the frontend and backend services, but it's unlikely that these
  services would run on the same machine in a real world scenario.

* [Protobuf](https://developers.google.com/protocol-buffers): This could have
  been used for communication between the frontend and backend service. But I
  was already beyond 10,000 req/s so I decided that it was not necessary to
  introduce this complexity at this stage. Worth considering, though.

* [SQL DB](https://en.wikipedia.org/wiki/SQL): That seemed overkill to setup for
  this exercise.
