# Unwind: native Windows stdio

`0001-native-windows-stdio.patch` is a change for [Unwind](https://github.com/bhaskargurram-ai/unwind), written against the 0.2.0 release of `unwind/proxy/passthrough.py`.

Unwind 0.2.0 refuses to start its stdio proxy on Windows, because the Proactor event loop cannot attach to inherited pipe handles. Its source lists a thread-backed reader as future work. The patch adds that reader and a matching writer, so `unwind run -- <server>` works natively on Windows. It also closes the upstream server's input when the client disconnects first, so the proxy exits instead of waiting on a server that will never see end of input.

Checked on Windows 11 with Python 3.14: the unmodified `unwind run` command, with the patch applied, listed and called VerifyDoc's tools through an MCP client and shut down cleanly. POSIX behavior is unchanged by construction and was not rerun.

The patch paths assume the package lives under `src/` in the Unwind repository. Adjust the prefix if it does not.

Until a release includes this, Turnloom carries the same adapters in [`turnloom/unwind_stdio.py`](../../turnloom/unwind_stdio.py).
