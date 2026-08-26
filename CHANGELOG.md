# Changelog

## [0.2.0](https://github.com/ishuar/aws-resource-inventory/compare/v0.1.1...v0.2.0) (2026-08-26)


### ⚠ BREAKING CHANGES

* the scan reports failed regions and services in its output and exit code ([#69](https://github.com/ishuar/aws-resource-inventory/issues/69))
* scan documents move out of shared /tmp into a per-user directory ([#66](https://github.com/ishuar/aws-resource-inventory/issues/66))
* remove --format — the scan always writes JSON, with --output - for stdout ([#61](https://github.com/ishuar/aws-resource-inventory/issues/61))
* ECS service ids are the cluster/service path AWS puts in the ARN ([#65](https://github.com/ishuar/aws-resource-inventory/issues/65))
* scan output is a self-describing JSON document ([#58](https://github.com/ishuar/aws-resource-inventory/issues/58))
* resource names are real AWS names or null ([#57](https://github.com/ishuar/aws-resource-inventory/issues/57))
* resource types always start with the CLI service key ([#56](https://github.com/ishuar/aws-resource-inventory/issues/56))
* every resource has a real ID and a real ARN ([#55](https://github.com/ishuar/aws-resource-inventory/issues/55))

### ✨ Features

* default output filenames start with the AWS account id ([#79](https://github.com/ishuar/aws-resource-inventory/issues/79)) ([da1566a](https://github.com/ishuar/aws-resource-inventory/commit/da1566a6a717b6af5ad2ec75c35426b363866ae7))
* every resource has a real ID and a real ARN ([#55](https://github.com/ishuar/aws-resource-inventory/issues/55)) ([2f475e6](https://github.com/ishuar/aws-resource-inventory/commit/2f475e62e5bbd5fec989767b3271e0f8d70a79a8))
* remove --format — the scan always writes JSON, with --output - for stdout ([#61](https://github.com/ishuar/aws-resource-inventory/issues/61)) ([e9518ca](https://github.com/ishuar/aws-resource-inventory/commit/e9518ca590cab33b245b487d4b71b72fad261b6f))
* resource names are real AWS names or null ([#57](https://github.com/ishuar/aws-resource-inventory/issues/57)) ([ac3c9cd](https://github.com/ishuar/aws-resource-inventory/commit/ac3c9cd29c22e20e61403afab80e199b4040da7c))
* resource types always start with the CLI service key ([#56](https://github.com/ishuar/aws-resource-inventory/issues/56)) ([de60f07](https://github.com/ishuar/aws-resource-inventory/commit/de60f077c5341d10d5148b4a5cc2804623000cdf))
* scan output is a self-describing JSON document ([#58](https://github.com/ishuar/aws-resource-inventory/issues/58)) ([82ddbac](https://github.com/ishuar/aws-resource-inventory/commit/82ddbac3510c7657238f3882e4b8e2015e363f19))
* the results table shows names and ids under an account header ([#59](https://github.com/ishuar/aws-resource-inventory/issues/59)) ([1c6972a](https://github.com/ishuar/aws-resource-inventory/commit/1c6972a97fe156d30a5f0ccfaa6801a619206ae9))
* the scan reports failed regions and services in its output and exit code ([#69](https://github.com/ishuar/aws-resource-inventory/issues/69)) ([aeaf677](https://github.com/ishuar/aws-resource-inventory/commit/aeaf6779631df9753c4c11932eb50c1da8c72592))


### 🐞 Bug Fixes

* e2e-diff.sh stops passing the --format flag that was removed ([#67](https://github.com/ishuar/aws-resource-inventory/issues/67)) ([89bf221](https://github.com/ishuar/aws-resource-inventory/commit/89bf221cd42e0a3c0fa6d32e5d66c090aa58f896))
* ECS service ids are the cluster/service path AWS puts in the ARN ([#65](https://github.com/ishuar/aws-resource-inventory/issues/65)) ([b1eb220](https://github.com/ishuar/aws-resource-inventory/commit/b1eb2200123305e7fae307b74d8777fe3a283de2))
* every scanning client gets its own botocore config ([#60](https://github.com/ishuar/aws-resource-inventory/issues/60)) ([2710c55](https://github.com/ishuar/aws-resource-inventory/commit/2710c55707dc2c2428cda9b65a5bf9ba0ef828bc))
* logging is configured only by the CLI, never as an import side effect ([#68](https://github.com/ishuar/aws-resource-inventory/issues/68)) ([ac19cb9](https://github.com/ishuar/aws-resource-inventory/commit/ac19cb984e1d95b82acba6e155b332106534d2b2))
* scan documents move out of shared /tmp into a per-user directory ([#66](https://github.com/ishuar/aws-resource-inventory/issues/66)) ([d0fb28f](https://github.com/ishuar/aws-resource-inventory/commit/d0fb28f0009f16ccfffc5e9d0ff66823b0515c76))
* the scan cache moves out of shared /tmp into a per-user directory ([#62](https://github.com/ishuar/aws-resource-inventory/issues/62)) ([e550a27](https://github.com/ishuar/aws-resource-inventory/commit/e550a275f54b2e74c233f1cbfbe876e28bf8796a))


### 📦 Other Changes

* ADR-0005 records the resource identity and output model ([#53](https://github.com/ishuar/aws-resource-inventory/issues/53)) ([957bd1e](https://github.com/ishuar/aws-resource-inventory/commit/957bd1e43e081d6b7f28123bfde44a063e478144))
* delete the completed Poetry migration guide ([#49](https://github.com/ishuar/aws-resource-inventory/issues/49)) ([50aa2ce](https://github.com/ishuar/aws-resource-inventory/commit/50aa2ced59cbba962b152c4619a46ac622176a4c))
* one definition of the scan-path choice ([#52](https://github.com/ishuar/aws-resource-inventory/issues/52)) ([65d9ddf](https://github.com/ishuar/aws-resource-inventory/commit/65d9ddf88868e5e03a14a3472decf3ffd84757a1))
* records.py states the name guarantee the code actually gives ([#64](https://github.com/ishuar/aws-resource-inventory/issues/64)) ([724b4f0](https://github.com/ishuar/aws-resource-inventory/commit/724b4f0a8606668534ee6def1bce82031ba9adc5))

## [0.1.1](https://github.com/ishuar/aws-resource-inventory/compare/v0.1.0...v0.1.1) (2026-08-22)

> [!IMPORTANT]
> **0.1.0 was published with a broken wheel.** `cli.py` was left out of the
> distribution, so the installed `aws-inventory` command died immediately with
> `ModuleNotFoundError: No module named 'cli'`. **0.1.1 is the first working
> release**; 0.1.0 is yanked on PyPI.
>
> ```bash
> uv tool install aws-resource-inventory   # or: pipx install aws-resource-inventory
> aws-inventory --help
> ```
>
> Module paths also moved under a single `aws_resource_inventory/` package. That
> only affects direct imports, and since 0.1.0 never ran, nothing depended on the
> old paths.


### 🐞 Bug Fixes

* e2e-diff refuses refs it cannot honestly compare ([#48](https://github.com/ishuar/aws-resource-inventory/issues/48)) ([215d844](https://github.com/ishuar/aws-resource-inventory/commit/215d8444da39a9a9abfd69df978dae588de63912))
* installed CLI starts — ship cli.py and aws_scanner.py in the wheel ([#45](https://github.com/ishuar/aws-resource-inventory/issues/45)) ([c926547](https://github.com/ishuar/aws-resource-inventory/commit/c9265477c50153761a729c2076fea40d2152a1d0))
* release pipeline publishes to PyPI, and packaging is checked before release ([#42](https://github.com/ishuar/aws-resource-inventory/issues/42)) ([b4d7935](https://github.com/ishuar/aws-resource-inventory/commit/b4d7935dc48741a4e41d7aea6e46570b9dc5c179))


### 📦 Other Changes

* everything ships inside one aws_resource_inventory package ([#47](https://github.com/ishuar/aws-resource-inventory/issues/47)) ([2f284e9](https://github.com/ishuar/aws-resource-inventory/commit/2f284e98e15187253b665cad4b31162724dffc9f))
* install from PyPI with uv or pipx ([#46](https://github.com/ishuar/aws-resource-inventory/issues/46)) ([b80f403](https://github.com/ishuar/aws-resource-inventory/commit/b80f4034b48744da3b1e948b97cf0ef0c70a6eae))

## 0.1.0 (2026-08-22)

Initial release of **aws-resource-inventory** — a read-only CLI
(`aws-inventory`) that inventories AWS resources across regions and
services.

### ✨ Highlights

* Scans eight AWS services — EC2, S3, ECS, EFS, VPC, RDS, ELB, and Auto Scaling — across multiple regions concurrently, with or without tag filters.
* Discovers resources from 100+ AWS services via the Resource Groups Tagging API (`--all-services` with a tag filter).
* Table, JSON, and Markdown output — rendered in the terminal and written to a file for further processing.
* Result caching with a 10-minute TTL, configurable parallelism per region (`--max-workers`) and per service (`--service-workers`), dry-run preview, continuous refresh mode, and graceful Ctrl+C handling.
* Debug traces (`--debug`), full AWS API tracing (`--verbose`), custom log files (`--log-file`), and shell tab-completion for commands, options, and service names.

Install: `pip install aws-resource-inventory`
