---
title: "Pushing Metrics to Pushgateway"
category: "exporters"
component: "pushgateway"
prometheus_version: "3.16.0"
documentation_version: "3.16"
source_repository: "https://github.com/prometheus/docs"
source_ref: "main"
source_commit: "cc0672badce86173b9afaa95a59904576bcd6d6a"
source_url: "https://prometheus.io/docs/instrumenting/pushing/"
source_version: "3.16.0"
retrieved_at: "2026-11-08T05:33:57.998178+00:00"
spdx_license: "Apache-2.0"
attribution: "Copyright The Prometheus Authors"
content_hash: "12b19ecdd6b2f4f2e86b2b337b83da2b13e77cccc46857f2313b2e13282c18ca"
document_type: "guide"
doc_type: "guide"
---

Occasionally you will need to monitor components which cannot be scraped. The
[Prometheus Pushgateway](https://github.com/prometheus/pushgateway) allows you
to push time series from [short-lived service-level batch
jobs](/docs/practices/pushing/) to an intermediary job which Prometheus can
scrape. Combined with Prometheus's simple text-based exposition format, this
makes it easy to instrument even shell scripts without a client library.

 * For more information on using the Pushgateway and use from a Unix shell, see the project's
[README.md](https://github.com/prometheus/pushgateway#readme).

 * For use from Java see the
[Pushgateway documentation](https://prometheus.github.io/client_java/exporters/pushgateway/).

 * For use from Go see the [Push](https://pkg.go.dev/github.com/prometheus/client_golang/prometheus/push#Pusher.Push) and [Add](https://pkg.go.dev/github.com/prometheus/client_golang/prometheus/push#Pusher.Add) methods.

 * For use from Python see [Exporting to a Pushgateway](https://prometheus.github.io/client_python/exporting/pushgateway/).

 * For use from Ruby see the [Pushgateway documentation](https://github.com/prometheus/client_ruby#pushgateway).

* To find out about Pushgateway support of [client libraries maintained outside of the Prometheus project](/docs/instrumenting/clientlibs/), refer to their respective documentation.
