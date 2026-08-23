# Logging

How to get the right amount of logging out of `aws-inventory`, and how
the logging system works inside. Simple rule: three levels, each one
flag more than the last.

## The three levels

| Level | Command | What you see | Cost |
|---|---|---|---|
| Normal | `aws-inventory scan` | Progress bars, results, warnings | none |
| Debug | `aws-inventory scan --debug` | Execution traces, timings, caller file:line, a log file | small |
| Verbose | `aws-inventory --verbose scan --debug` | Everything above plus every boto3/botocore API call and HTTP request/response | ~10-20% slower |

> [!IMPORTANT]
> `--verbose` and `--log-file` are global options — they go **before**
> `scan`. `--verbose` only has an effect together with `--debug`.

## Where logs go

1. Regular log lines go to **stdout** (rendered by rich) — except under
   `--output -`, where stdout carries the JSON document alone and logs
   move to **stderr**. They are redirected, never dropped: a scan that
   fails still says why, and `... --output - | jq` stays clean.
2. Progress bars and the live display go to **stderr** — a separate
   console, so logs and progress never overwrite each other. Under
   `--output -` the progress display is silenced, which is what frees
   stderr for the log lines.
3. In debug mode, everything also goes to a **file**:
   `.debug_logs/aws_scanner_debug_<timestamp>.log` by default, or the
   path you pass with `--log-file`. File lines carry the full caller
   context (`path:function:line`), which the console omits.

Useful commands:

```bash
ls .debug_logs/                                      # find the log files
tail -f .debug_logs/aws_scanner_debug_*.log          # follow a live scan
grep -E "(boto|botocore|HTTP)" .debug_logs/*.log     # only the AWS API traffic
```

What the API traffic looks like with `--verbose` + `--debug` (this is
botocore's own logging, let through — see below):

```
[DEBUG] Event before-call.ec2.DescribeInstances: calling handler <function>
[DEBUG] Making request for OperationModel(name=DescribeInstances) with params: {...}
[DEBUG] Sending http request: <AWSPreparedRequest method=POST, url=https://ec2.us-east-1.amazonaws.com/>
[DEBUG] https://ec2.us-east-1.amazonaws.com:443 "POST / HTTP/1.1" 200 1234
[DEBUG] Response headers: {'x-amzn-RequestId': '12345-67890', ...}
```

## How it works inside (`aws_resource_inventory/lib/logging.py`)

One `AWSLogger` object serves the whole process (a singleton —
`get_logger()` returns it from anywhere; the `name` argument is
accepted for compatibility but ignored).

1. `configure_logging(debug, log_file, verbose)` sets everything up
   once, at CLI startup: log level, the rich stdout handler, the debug
   file handler, and third-party logger volume.
2. Level methods: `debug/info/warning/error/critical`, plus helpers
   that keep messages consistent — `log_aws_operation`,
   `log_scan_progress`, `log_cache_operation`, `log_error_context` —
   and `timer("what")`, a context manager that logs start/finish with
   the duration.
3. `get_output_console()` returns the stderr console (the one the
   progress display owns).
4. Without `--verbose`, noisy third-party loggers (boto3, botocore,
   urllib3, …) are capped at WARNING. With `--verbose` + `--debug`,
   they are opened up to DEBUG and attached to the same handlers — that
   is the whole API-tracing feature: botocore already logs every
   request; this system just lets it through.

> [!NOTE]
> While the live progress display is running, console logging is paused
> (`disable_console_output` / `enable_console_output` around the
> display in `aws_resource_inventory/cli.py`) so log lines cannot corrupt the progress bars.
> File logging continues uninterrupted. This pairing is a known sharp
> edge; a progress-event redesign is on the roadmap.

## Extending

To trace one more library under `--verbose`, add its logger name to
`_enable_detailed_aws_logging()` in `aws_resource_inventory/lib/logging.py`. To
silence a noisy one in normal mode, add it to
`_suppress_noisy_loggers()`. That is the entire extension surface —
scanners never configure logging themselves; they call `get_logger()`
and log.

## Troubleshooting

1. No AWS API logs? You need **both** flags, in the right places:
   `aws-inventory --verbose scan --debug`.
2. Output interleaved or hard to read? Run sequentially:
   `--max-workers 1 --service-workers 1`.
3. No log file appearing? Debug mode creates it — check `--debug` is
   set and `.debug_logs/` is writable.
4. Too slow with verbose on? That is expected; use verbose for
   diagnosis, not routine scans.
