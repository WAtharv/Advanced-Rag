---
title: "SQL Storage Backend Permissions and Security"
category: "security"
component: "storage"
helm_version: "4.3.1"
source_repository: "https://github.com/helm/helm-www"
source_ref: "main"
source_commit: "60d4db89421147aec9fc3fcef9e2d49e2729f50b"
source_url: "https://helm.sh/docs/topics/permissions_sql_storage_backend/"
source_version: "4.3.1"
retrieved_at: "2026-11-08T04:55:44.197718+00:00"
spdx_license: "CC-BY-4.0"
attribution: "Copyright The Helm Authors"
content_hash: "76269d1b0ab0f405a6d3c8901a7b37c01c08b445ea1778cd549b0203b90318c2"
document_type: "guide"
doc_type: "guide"
---

This document aims to provide guidance to users for setting up and managing
permissions when using the SQL storage backend.

## Introduction

To handle permissions, Helm leverages the RBAC feature of Kubernetes. When using
the SQL storage backend, Kubernetes' roles can't be used to determine whether or
not an user can access a given resource. This document shows how to create and
manage these permissions.

## Initialization

The first time the Helm CLI will make connect to your database, the client will
make sure that it was previously initialized. If it is not, it will take care of
the necessary setup automatically. This initialization requires admin privileges
on the public schema, or at least to be able to:

* create a table
* grant privileges on the public schema

After the migration was run against your database, all the other roles can use
the client.

## Grant privileges to a non admin user in PostgreSQL

To manage permissions, the SQL backend driver leverages the
[RLS](https://www.postgresql.org/docs/9.5/ddl-rowsecurity.html)(Row Security
Level) feature of PostgreSQL. RLS allows all users to be able to read/write
from/to the same table, without being able to manipulate the same rows if they
are not explicitly allowed to. By default, any role that has not been
explicitly granted with the right privileges will always return an empty list
when running `helm list` and will not be able to retrieve or modify any resource
in the cluster.

Let's see how to grant a given role access to specific namespaces:

```sql
CREATE POLICY <name> ON releases_v1 FOR ALL TO <role> USING (namespace = 'default');
```

This command will grant the permissions to read and write all resources that
meet the `namespace = 'default'` condition to the role `role`. After creating
this policy, the user being connected to the database on the behalf of the role
`role` will therefore be able to see all the releases living in the `default`
namespace when running `helm list`, and to modify and delete them.

Privileges can be managed granularly with RLS, and one might be interested in
restraining access given the different columns of the table:
* key
* type
* body
* name
* namespace
* version
* status
* owner
* createdAt
* modifiedAt
