# Roadmap

Turnloom stays small on purpose: one loop, a context store and a handful of tools. The work below makes what exists more trustworthy and easier to reach. Nothing here is a promise or a date. Each item links to an issue where the details and the discussion live, and each is open for someone to pick up.

## Help wanted now

These need a machine, an editor or an account the maintainer does not have, which makes them the most useful places to contribute.

| Area | What is needed | Issue |
| --- | --- | --- |
| Editors | Run `turnloom acp` inside Zed, JetBrains and Neovim, fix what breaks, and document the setup that works | [#8](https://github.com/bhaskargurram-ai/turnloom/issues/8) |
| Browsers | Run the Studio browser test on Firefox and WebKit and fix the differences | [#9](https://github.com/bhaskargurram-ai/turnloom/issues/9) |
| Providers | Connect through a preset you use and report whether discovery, tools and streaming work | [#10](https://github.com/bhaskargurram-ai/turnloom/issues/10) |
| Desktop app | Build the standalone app on macOS and Linux, and work out signing for Windows and macOS | [#11](https://github.com/bhaskargurram-ai/turnloom/issues/11) |
| Packaging | Homebrew, winget, Scoop and Nix packages | [#12](https://github.com/bhaskargurram-ai/turnloom/issues/12) |
| Benchmarks | Run the whole of Terminal-Bench 2.1 with three attempts per task and publish the trajectories | [#13](https://github.com/bhaskargurram-ai/turnloom/issues/13) |

## Next

| Area | What is needed | Issue |
| --- | --- | --- |
| MCP | Move the client and the server to the 2.x SDK | [#14](https://github.com/bhaskargurram-ai/turnloom/issues/14) |
| Transports | Speak the Anthropic Messages API and the OpenAI Responses API directly. Today both are reached through LiteLLM or an OpenAI-compatible route | [#15](https://github.com/bhaskargurram-ai/turnloom/issues/15) |
| Isolation | Run approved commands in an optional sandbox, so `--yes` is safe outside a container | [#16](https://github.com/bhaskargurram-ai/turnloom/issues/16) |

## Not planned

- A hosted service, accounts or telemetry. Turnloom runs on your machine and talks only to the model endpoint you connect.
- A second way to do something the loop already does. See the first paragraph of [AGENTS.md](AGENTS.md).

## Proposing something else

Open a [discussion](https://github.com/bhaskargurram-ai/turnloom/discussions) for ideas and questions, or an issue for a concrete change. For anything larger than a fix, agree the direction in an issue before writing the code; [CONTRIBUTING.md](CONTRIBUTING.md) explains what a pull request needs.
