# AWS Resource Inventory

[![PyPI](https://img.shields.io/pypi/v/aws-resource-inventory.svg)](https://pypi.org/project/aws-resource-inventory/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Code Style: Black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/ishuar/aws-resource-inventory/main.svg)](https://results.pre-commit.ci/latest/github/ishuar/aws-resource-inventory/main)

A comprehensive AWS multi-service scanner with tag-based filtering, parallel processing, advanced logging capabilities, and optimization features. This tool enables efficient discovery and analysis of AWS resources across multiple regions and services with intelligent caching, a self-describing JSON results document, and detailed AWS API tracing.

## Features

- Scans eight AWS services — EC2, S3, ECS, EFS, VPC, RDS, ELB, and Auto Scaling — across multiple regions concurrently, with or without tag filters.
- Discovers resources from 100+ AWS services via the Resource Groups Tagging API (`--all-services`, requires a tag filter).
- Results render as a terminal table and are always written as one self-describing JSON document — to a file, or to stdout with `--output -` for piping straight into `jq`.
- Result caching with a 10-minute TTL (`--cache` / `--no-cache`).
- Configurable parallelism per region (`--max-workers`) and per service (`--service-workers`), dry-run preview, continuous refresh mode, and graceful Ctrl+C handling.
- Debug traces (`--debug`), full AWS API tracing (`--verbose`), and custom log files (`--log-file`) — see [Logging Architecture](docs/LOGGING_ARCHITECTURE.md).

## 🏗️ Supported AWS Services

| Service          | Description                                                                                                             | Resources Scanned                                                 |
|------------------|-------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------|
| **All Services** | With [Resource Groups Tagging API](https://docs.aws.amazon.com/resourcegroupstagging/latest/APIReference/overview.html) | 100+ AWS services when using tags                                 |
| **EC2**          | Elastic Compute Cloud                                                                                                   | Instances, Volumes, Security Groups, AMIs, Snapshots, Elastic IPs |
| **S3**           | Simple Storage Service                                                                                                  | Buckets and their configurations                                  |
| **ECS**          | Elastic Container Service                                                                                               | Clusters, Services, Task Definitions, Capacity Providers          |
| **VPC**          | Virtual Private Cloud                                                                                                   | VPCs, Subnets, Route Tables, IGWs, NAT Gateways, DHCP Options, Peering Connections, Endpoints |
| **Auto Scaling** | Auto Scaling Groups                                                                                                     | ASGs, Launch Configurations, Launch Templates                     |
| **ELB**          | Elastic Load Balancing                                                                                                  | Load Balancers (ALB/NLB), Target Groups, Listeners, Listener Rules |
| **RDS**          | Relational Database Service                                                                                             | DB Instances, DB Clusters, DB Snapshots, Aurora Cluster Snapshots |
| **EFS**          | Elastic File System                                                                                                     | File Systems (with size and mount-target details)                 |

> **📚 Architecture Details**: For detailed information about the scanning architecture and service implementation patterns, see [Architecture Documentation](docs/Architecture.md).

## 📋 Prerequisites

Before installing the AWS Resource Inventory, ensure you have the following dependencies:

### To run it
- **Python 3.10+** — the only hard requirement.
- **An installer** — [uv](https://docs.astral.sh/uv/) or
  [pipx](https://pipx.pypa.io/) recommended; plain `pip` works too.
- **AWS CLI** — optional, but the easiest way to set up credentials.

### To develop on it
- **Poetry** — dependency management and packaging.
- **pre-commit** — git hooks framework.

### AWS Configuration
- Valid AWS credentials configured via:
  - AWS SSO (`aws sso login`)
  - AWS CLI (`aws configure`)
  - Environment variables
  - IAM roles (for EC2/containers)

### Required IAM Permissions

The scanner is read-only. This policy covers every API call it makes —
the eight service scanners, the credential check (`sts`), and the tag
scan (`tag:GetResources`, only exercised with `--tag-key`/`--tag-value`/
`--all-services`):

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "AwsResourceInventoryReadOnly",
            "Effect": "Allow",
            "Action": [
                "sts:GetCallerIdentity",
                "ec2:Describe*",
                "s3:ListAllMyBuckets",
                "s3:GetBucketLocation",
                "s3:GetBucketTagging",
                "ecs:List*",
                "ecs:Describe*",
                "elasticloadbalancing:Describe*",
                "elasticfilesystem:Describe*",
                "rds:Describe*",
                "autoscaling:Describe*",
                "tag:GetResources"
            ],
            "Resource": "*"
        }
    ]
}
```

> [!NOTE]
> `ec2:Describe*` also covers the VPC scanner and Auto Scaling launch
> templates — both use the EC2 API. The AWS-managed `ReadOnlyAccess`
> policy works too if you prefer not to maintain a custom one.

## 🚀 Installation

Install from PyPI — it is a CLI, so install it as a tool rather than into
a project's dependencies:

```bash
# uv (recommended — fastest, isolated tool environment)
uv tool install aws-resource-inventory

# pipx (equally good; the established option for Python CLIs)
pipx install aws-resource-inventory

# pip — use a virtualenv, not a system Python
pip install aws-resource-inventory
```

Verify it:

```bash
aws-inventory --help
```

Both `aws-inventory` and the longer `aws-resource-inventory` are
installed; they are the same command.

### Run it once without installing

```bash
uvx aws-resource-inventory scan --regions us-east-1
```

### Pin a version, or upgrade

Latest is the default above. Pin an exact version for CI or reproducible
environments — available versions are listed on
[PyPI](https://pypi.org/project/aws-resource-inventory/#history):

```bash
uv tool install "aws-resource-inventory==X.Y.Z"
pipx install "aws-resource-inventory==X.Y.Z"
```

```bash
# Upgrade an existing install
uv tool upgrade aws-resource-inventory
pipx upgrade aws-resource-inventory
```

> [!TIP]
> Shell tab-completion needs `aws-inventory` on your `PATH`, which the
> installs above give you. Run
> `aws-inventory --install-completion`, then restart your shell — see
> [Shell Completion](docs/SHELL_COMPLETION.md).

### Development setup

Working on the scanner itself, rather than using it:

```bash
git clone https://github.com/ishuar/aws-resource-inventory.git
cd aws-resource-inventory

# Automated: installs Python, Poetry, pre-commit, dependencies, AWS CLI
./setup.sh

# Or manually
brew install python3 poetry pre-commit awscli   # macOS
poetry install
pre-commit install --install-hooks
./run_quick_tests.sh
```

From a clone, prefix commands with `poetry run` — `poetry run
aws-inventory scan …` — or activate the virtualenv. The examples below
assume an installed CLI.

## 💻 Usage

### Command Structure

All commands follow this pattern:
```bash
aws-inventory [GLOBAL OPTIONS] COMMAND [COMMAND OPTIONS]
```

Global options apply to all commands: `--verbose` / `-v` (AWS API tracing,
use with `--debug`) and `--log-file` / `-l` (custom log file path). The
main command is `scan`.

> [!IMPORTANT]
> Global options must come **before** the command. `aws-inventory scan --verbose`
> fails with `No such option: --verbose` — the correct form is
> `aws-inventory --verbose scan`.

### Basic Commands

```bash
# Display help and available commands
aws-inventory --help

# Display scan command help and options
aws-inventory scan --help

# Basic scan with default settings (all supported services)
aws-inventory scan --regions us-east-1,eu-west-1,eu-central-1,us-west-2

# Scan specific services
aws-inventory scan --service ec2

# Scan specific regions
aws-inventory scan --regions us-east-1,eu-west-1
```

### Debug and Logging Options

```bash
# Enable debug mode for detailed execution traces
aws-inventory scan --debug --regions us-east-1

# Enable verbose AWS API tracing (requires --debug)
aws-inventory --verbose scan --debug --service ec2

# Custom log file for debug output
aws-inventory --log-file /tmp/my-scan.log scan --debug --regions us-east-1

# Combine verbose logging with custom log file
aws-inventory --verbose --log-file /tmp/aws-api-trace.log scan --debug --service ec2 --service s3

# Debug with dry run (no actual scanning)
aws-inventory --verbose scan --debug --dry-run --service vpc
```

### Service-Specific Scanning

```bash
# Scan only EC2 resources
aws-inventory scan --service ec2

# Scan multiple services
aws-inventory scan --service ec2 --service s3 --service vpc

# Scan all built-in services in specific regions
aws-inventory scan --regions us-east-1,us-west-2

# Combine service and region filtering
aws-inventory scan --service ec2 --regions eu-central-1,eu-west-1

# Scan ALL AWS services using Resource Groups API (requires tags)
aws-inventory scan --all-services --tag-key Environment --tag-value Production
```

### Tag-Based Filtering

```bash
# Filter by environment tag
aws-inventory scan --tag-key Environment --tag-value Production

# Filter by application tag
aws-inventory scan --tag-key app --tag-value web-server

# Filter by cost center in specific regions
aws-inventory scan --regions us-east-1 --tag-key CostCenter --tag-value Engineering
```

### Output

Every scan renders a table in the terminal and writes the JSON results
document. With no `--output` it lands in
`$XDG_DATA_HOME/aws-resource-inventory`, falling back to
`~/.local/share/aws-resource-inventory` — a per-user directory, never
shared `/tmp` (ADR-0009). The path is printed when the scan finishes.
`--output` controls where the document goes:

```bash
# Table in the terminal, JSON document under
# $XDG_DATA_HOME/aws-resource-inventory (default ~/.local/share/...)
aws-inventory scan

# Choose the file path
aws-inventory scan --output results.json

# Stream the document to stdout instead — every other line is
# suppressed, so it pipes cleanly into jq; nothing is written to disk
aws-inventory scan --output - | jq '.summary'

# Export filtered results
aws-inventory scan --tag-key Environment --tag-value Production --output prod-resources.json
```

The JSON document is self-describing — scan metadata, a summary, and the
flat `resources` array, sorted by region → type → id:

```json
{
  "schema_version": 1,
  "scan": {
    "tool": { "name": "aws-resource-inventory", "version": "0.2.0" },
    "account": "111122223333",
    "partition": "aws",
    "regions": ["eu-central-1"],
    "source": "services",
    "filters": {
      "services": ["ec2", "s3"],
      "tag_key": null,
      "tag_value": null,
      "all_services": false
    },
    "started_at": "2026-08-23T09:14:22Z",
    "duration_seconds": 12.4,
    "errors": []
  },
  "summary": {
    "total": 1,
    "by_region": { "eu-central-1": 1 },
    "by_type": { "ec2:instance": 1 }
  },
  "resources": [
    {
      "region": "eu-central-1",
      "type": "ec2:instance",
      "id": "i-0abc123def456789a",
      "name": "web-server-prod-01",
      "arn": "arn:aws:ec2:eu-central-1:111122223333:instance/i-0abc123def456789a",
      "arn_source": "constructed"
    }
  ]
}
```

Every resource carries the same six keys (`name` is `null` when AWS
supplies none; `arn_source` states whether the ARN was observed from
the API or constructed from the caller identity), so the data loads
straight into `jq`, pandas, or SQL. `schema_version` bumps only on
breaking changes.

`summary.by_region` names every region you scanned, reporting `0` for
one that returned nothing. `scan.errors` states whether you can trust
those counts: always present, `[]` when the scan completed cleanly, one
`{region, service, message}` object per failed scan unit (`service` is
`null` when the whole region failed). `summary.by_type` lists only the
types actually found: types are discovered, not requested.

A scan that finds nothing still writes the file. The envelope records
which account, regions and filters were used, and that is evidence in
its own right — "no resources found" is a result worth keeping.

The exit code states how much of the scan ran, so scripts don't need to
parse the JSON:

| Exit code | Meaning |
|---|---|
| `0` | Complete scan — `scan.errors` is `[]` |
| `1` | No usable inventory — bad credentials, or every region failed (the envelope is still written) |
| `2` | Usage error (bad flags) |
| `3` | Partial scan — some regions or services errored; the envelope names them |

### Advanced Options

```bash
# Dry run (preview without execution)
aws-inventory scan --dry-run --service ec2

# Disable caching for fresh data
aws-inventory scan --no-cache

# Configure worker threads for performance
aws-inventory scan --max-workers 10 --service-workers 6

# Continuous refresh mode with custom interval
aws-inventory scan --refresh --refresh-interval 30 --service ec2

# Debug mode with performance timing
aws-inventory --verbose scan --debug --max-workers 1 --service ec2
```

### Real-World Examples

```bash
# Production infrastructure audit with comprehensive logging
aws-inventory --verbose --log-file prod-audit.log scan \
    --debug --tag-key Environment --tag-value Production \
    --output production-audit.json

# Regional compliance check, piped straight into jq
aws-inventory scan \
    --regions eu-west-1,eu-central-1 --service ec2 \
    --output - | jq '.resources[] | select(.name == null)'

# Application-specific resource discovery across all AWS services
aws-inventory scan \
    --all-services --tag-key Application --tag-value MyApp \
    --regions us-east-1

# Development environment troubleshooting with verbose logging
aws-inventory --verbose --log-file dev-debug.log scan \
    --debug --regions us-west-2 \
    --tag-key Environment --tag-value Development \
    --no-cache --dry-run

# Performance analysis with sequential processing
aws-inventory --verbose --log-file perf-analysis.log scan \
    --debug --max-workers 1 --service-workers 1 \
    --service ec2 --service s3 --regions us-east-1
```

## 🔧 Configuration

### AWS Profile Configuration

```bash
# Set AWS profile
export AWS_PROFILE=your-profile-name

# Login via SSO
aws sso login --profile $AWS_PROFILE

# Verify credentials
aws sts get-caller-identity
```

### Logging Configuration

The scanner features a comprehensive logging system with multiple configuration options:

```bash
# Global logging options (apply to all commands)
aws-inventory --verbose --log-file /path/to/logfile.log scan --debug

# Debug modes explained:
# --debug: Enable debug mode with rich console output and file logging
# --verbose: Enable AWS API tracing (requires --debug)
# --log-file: Custom log file path (default: .debug_logs/aws_scanner_debug_TIMESTAMP.log)
```

Logging levels: normal runs show progress and results; `--debug` adds
execution traces, timing, and caller context; `--verbose` together with
`--debug` adds full AWS API tracing including HTTP requests and responses.

> [!Tip]
> **📖 Detailed Logging Guide**: For comprehensive logging documentation, configuration examples, and troubleshooting, see [Logging Architecture](docs/LOGGING_ARCHITECTURE.md).

### Performance Tuning

- `--max-workers` (1-20) controls region-level parallelism; `--service-workers` (1-10) controls service-level parallelism.
- `--cache` speeds up repeated scans (10-minute TTL). Entries live in
`$XDG_CACHE_HOME/aws-resource-inventory`, falling back to
`~/.cache/aws-resource-inventory` — owner-only, never shared `/tmp`; `--no-cache` forces fresh data.

> [!NOTE]
> Verbose logging adds roughly 10-20% overhead — use it for troubleshooting, not routine scans.

## 📁 **Project Structure**

```
aws-resource-inventory/
├── aws_resource_inventory/          # The installed package (one top-level name)
│   ├── cli.py                       # Command-line interface (typer)
│   ├── orchestrator.py              # Scan orchestration across regions
│   │
│   ├── lib/                         # Shared engine and infrastructure
│   │   ├── engine.py                # Pagination, concurrency, error policy
│   │   ├── records.py               # Resource + CallerIdentity — the typed record every output consumes
│   │   ├── envelope.py              # The JSON envelope — the tool's public output schema
│   │   ├── arn.py                   # ARN id extraction shared by both scan paths
│   │   ├── clients.py               # The only boto3 client factory (pooling, adaptive retries)
│   │   ├── scan.py                  # Region/service scan orchestration + caching hooks
│   │   ├── outputs.py               # Table rendering + JSON envelope output
│   │   ├── resource_groups_utils.py # Resource Groups Tagging API path (--all-services, tags)
│   │   ├── cache.py                 # Result caching with TTL
│   │   └── logging.py               # Unified logging with AWS API tracing
│   │
│   └── services/                    # One scanner module per AWS service
│       ├── registry.py              # Single source of truth: service → scanner + output processor
│       ├── ec2_service.py           # Instances, volumes, security groups, AMIs, snapshots, Elastic IPs
│       ├── s3_service.py            # Buckets (region-filtered, tag-enriched)
│       ├── ecs_service.py           # Clusters, services, task definitions, capacity providers
│       ├── efs_service.py           # File systems
│       ├── elb_service.py           # Load balancers, target groups, listeners, rules
│       ├── rds_service.py           # DB instances, clusters, snapshots (incl. Aurora)
│       ├── vpc_service.py           # VPC networking components
│       └── autoscaling_service.py   # ASGs, launch configurations, launch templates
│
├── pyproject.toml                   # Project configuration and dependencies
├── setup.sh                         # Automated setup script
├── run_quick_tests.sh               # Smoke-test script
│
├── scripts/
│   └── e2e-diff.sh                  # Before/after functional comparison against real AWS
│
├── docs/                            # Documentation
│   ├── adr/                         # Architecture decision records
│   ├── Architecture.md              # System architecture and design patterns
│   └── LOGGING_ARCHITECTURE.md      # Logging system documentation
│
└── tests/                           # Test suite — runs with ZERO AWS credentials (moto)
```

## 🧪 Testing

> [!NOTE]
> The suite needs no AWS credentials: moto fakes AWS, and the test fixtures
> force fake credentials so no test can ever touch a real account.

```bash
# Run the full suite (coverage is reported automatically)
poetry run pytest

# Run one test file
poetry run pytest tests/test_engine.py

# Quick smoke checks (help, dry-run, stdout JSON, error handling)
./run_quick_tests.sh
```

After merging a change that touches scan behaviour, verify against real
AWS with the before/after comparison script:

```bash
scripts/e2e-diff.sh                        # compares origin/main~1 vs origin/main
scripts/e2e-diff.sh --tag-key team         # exercise the tag-scan path too
```

## 🐛 Troubleshooting

### Common Issues

1. **AWS Credentials**: Ensure AWS credentials are properly configured
   ```bash
   aws sts get-caller-identity
   ```

2. **Python Version**: Verify Python 3.10+ is installed
   ```bash
   python3 --version
   ```

3. **Reinstall** if the CLI misbehaves after an upgrade
   ```bash
   uv tool install --force aws-resource-inventory   # installed from PyPI
   poetry install --no-cache                        # working from a clone
   ```

4. **Permissions**: Ensure your AWS user/role has necessary permissions for the services you're scanning

### Debug and Logging Troubleshooting

The advanced logging system provides powerful debugging capabilities:

```bash
# Basic debug information
aws-inventory scan --debug --dry-run

# Verbose AWS API tracing for troubleshooting
aws-inventory --verbose --log-file debug-trace.log scan --debug --service ec2

# Check debug log files (automatically created)
ls .debug_logs/

# Monitor real-time logging
tail -f .debug_logs/aws_scanner_debug_*.log

# Filter AWS API calls
grep -E "(boto|botocore|HTTP)" .debug_logs/aws_scanner_debug_*.log
```

Common logging issues: no AWS API logs usually means one of `--debug` or
`--verbose` is missing (both are required, and `--verbose` goes before
`scan`); for sequential, readable debugging use `--max-workers 1`; if log
files are missing, check permissions on the `.debug_logs/` directory.

> [!Tip]
> **🔧 Advanced Troubleshooting**: For detailed logging troubleshooting and configuration options, see [Logging Architecture - Troubleshooting Section](docs/LOGGING_ARCHITECTURE.md#troubleshooting).

## 📌 Quick Reference

### Most Common Commands

```bash
# Quick scan with basic output
aws-inventory scan --regions us-east-1

# Debug mode with detailed logging
aws-inventory scan --debug --regions us-east-1

# Full AWS API tracing (development/troubleshooting)
aws-inventory --verbose --log-file trace.log scan --debug --service ec2

# Tag-based filtering across all AWS services
aws-inventory scan --all-services --tag-key Environment --tag-value Production

# Production audit with comprehensive logging
aws-inventory --verbose --log-file audit.log scan --debug \
    --tag-key Environment --tag-value Production --output audit.json
```

### Flag Combinations Guide

| Scenario         | Command Pattern                                          | Purpose                        |
|------------------|----------------------------------------------------------|--------------------------------|
| **Basic Scan**   | `aws-inventory scan`                            | Standard resource discovery    |
| **Debug Mode**   | `aws-inventory scan --debug`                    | Detailed execution information |
| **API Tracing**  | `aws-inventory --verbose scan --debug`          | Full AWS API call logging      |
| **Custom Logs**  | `aws-inventory --log-file path scan --debug`    | Custom log file location       |
| **All Services** | `aws-inventory scan --all-services --tag-key X` | Discover 100+ AWS services     |

## 📖 Documentation

The project includes comprehensive documentation covering all aspects of the system:

- [Architecture Documentation](docs/Architecture.md) — system design, component interactions, and patterns
- [Architecture Decision Records](docs/adr/) — why things are built the way they are
- [Shell Completion](docs/SHELL_COMPLETION.md) — tab-completion setup for the CLI
- [Logging Architecture](docs/LOGGING_ARCHITECTURE.md) — the logging system: API tracing, configuration, integration patterns, troubleshooting, performance

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Install development dependencies: Use [`./setup.sh`](./setup.sh)
4. Set up pre-commit hooks: `pre-commit install --install-hooks`
5. Make your changes and run tests: `poetry run pytest`
6. Test logging changes: `poetry run aws-inventory --verbose scan --debug --dry-run`
7. Commit with a conventional-commit message (titles feed the release changelog): `git commit -m 'feat: add amazing feature'`
8. Push to the branch: `git push origin feature/amazing-feature`
9. Open a Pull Request

> [!TIP]
> **Development Notes:**
> - Use the debug and verbose flags extensively during development
> - Check the logging architecture documentation when modifying logging behavior
> - Ensure all new features include appropriate logging and error handling

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- Built with [Typer](https://typer.tiangolo.com/) for CLI interface
- Styled with [Rich](https://rich.readthedocs.io/) for beautiful console output
- Powered by [Boto3](https://boto3.amazonaws.com/) for AWS integration
- Managed with [Poetry](https://python-poetry.org/) for dependency management

---

> _Made with ❤️ for the AWS community_
