---
title: "Prometheus Client Libraries Overview"
category: "exporters"
component: "client-libraries"
prometheus_version: "3.16.0"
documentation_version: "3.16"
source_repository: "https://github.com/prometheus/docs"
source_ref: "main"
source_commit: "cc0672badce86173b9afaa95a59904576bcd6d6a"
source_url: "https://prometheus.io/docs/instrumenting/clientlibs/"
source_version: "3.16.0"
retrieved_at: "2026-11-08T05:33:57.998178+00:00"
spdx_license: "Apache-2.0"
attribution: "Copyright The Prometheus Authors"
content_hash: "2fd4b80cb60284704866147b080d014198b3237984e84f454e2b17e9c8b5262e"
document_type: "reference"
doc_type: "reference"
---

Before you can monitor your services, you need to add instrumentation to their
code via one of the Prometheus client libraries. These implement the Prometheus
[metric types](/docs/concepts/metric_types/).

Choose a Prometheus client library that matches the language in which your
application is written. This lets you define and expose internal metrics via an
HTTP endpoint on your application’s instance:

* [Go](https://github.com/prometheus/client_golang)
* [Java or Scala](https://github.com/prometheus/client_java)
* [Node.js](https://github.com/prometheus/client_js)
* [Python](https://github.com/prometheus/client_python)
* [Ruby](https://github.com/prometheus/client_ruby)
* [Rust](https://github.com/prometheus/client_rust)

Unofficial third-party client libraries:

* [Bash](https://github.com/aecolley/client_bash)
* [C](https://github.com/digitalocean/prometheus-client-c)
* [C++](https://github.com/jupp0r/prometheus-cpp)
* [Common Lisp](https://github.com/deadtrickster/prometheus.cl)
* [Dart](https://github.com/tentaclelabs/prometheus_client)
* [Delphi](https://github.com/marcobreveglieri/prometheus-client-delphi)
* [Elixir](https://github.com/deadtrickster/prometheus.ex)
* [Erlang](https://github.com/deadtrickster/prometheus.erl)
* Haskell: [prometheus-client](https://github.com/fimad/prometheus-haskell)
* Haskell: [prometheus](https://github.com/bitnomial/prometheus)
* [Julia](https://github.com/fredrikekre/Prometheus.jl)
* [Lua](https://github.com/knyar/nginx-lua-prometheus) for Nginx
* [Lua](https://github.com/tarantool/metrics) for Tarantool
* [.NET / C#](https://learn.microsoft.com/en-us/dotnet/core/diagnostics/metrics-collection)
* [OCaml](https://github.com/mirage/prometheus)
* [Perl](https://metacpan.org/pod/Net::Prometheus)
* [PHP](https://github.com/promphp/prometheus_client_php)
* [R](https://github.com/cfmack/pRometheus)
* [Swift](https://github.com/swift-server/swift-prometheus)

When Prometheus scrapes your instance's HTTP endpoint, the client library
sends the current state of all tracked metrics to the server.

If no client library is available for your language, or you want to avoid
dependencies, you may also implement one of the supported [exposition
formats](/docs/instrumenting/exposition_formats/) yourself to expose metrics.

When implementing a new Prometheus client library, please follow the
[guidelines on writing client libraries](/docs/instrumenting/writing_clientlibs).
Note that this document is still a work in progress. Please also consider
consulting the [development mailing list](https://groups.google.com/forum/#!forum/prometheus-developers).
We are happy to give advice on how to make your library as useful and
consistent as possible.
