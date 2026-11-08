---
title: "Alertmanager Overview"
category: "alertmanager"
component: "alertmanager"
prometheus_version: "0.34.2"
documentation_version: "3.16"
source_repository: "https://github.com/prometheus/alertmanager"
source_ref: "v0.34.2"
source_commit: "e8349048265ccc0871c3d0d668b05eacc68baf50"
source_url: "https://prometheus.io/docs/alerting/latest/overview/"
source_version: "0.34.2"
retrieved_at: "2026-11-08T05:33:57.998178+00:00"
spdx_license: "Apache-2.0"
attribution: "Copyright The Prometheus Authors"
content_hash: "034e375813eef72b7d56b15be888512710fa5a7e7513552500fb6ad379a3a07d"
document_type: "concept"
doc_type: "concept"
---

Alerting with Prometheus is separated into two parts. Alerting rules in
Prometheus servers send alerts to an Alertmanager. The [Alertmanager](alertmanager.md)
then manages those alerts, including silencing, inhibition, aggregation and
sending out notifications via methods such as email, on-call notification systems, and chat platforms.

The main steps to setting up alerting and notifications are:

* Setup and [configure](configuration.md) the Alertmanager
* [Configure Prometheus](https://prometheus.io/docs/prometheus/latest/configuration/configuration/#alertmanager_config) to talk to the Alertmanager
* Create [alerting rules](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/) in Prometheus
