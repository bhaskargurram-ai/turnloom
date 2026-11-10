"""Run Turnloom's headless agent as a Harbor installed agent.

    harbor run -d <dataset> -a turnloom_harbor:Turnloom -m openai/<model-id> \\
        --ae OPENAI_API_KEY=... --ae OPENAI_BASE_URL=https://.../v1 --ak version=v0.6.0

Written against Harbor 0.24.0. The agent installs its own Python with uv, so it does not
depend on the task image, and finishes every download in install(): the run phase needs
only the model endpoint. It writes an ATIF trajectory to /logs/agent/trajectory.json
after every model call.
"""

import json
import shlex

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

REPOSITORY = "https://github.com/bhaskargurram-ai/turnloom"
PATH = 'export PATH="$HOME/.local/bin:$PATH"; '
# Facts the runner states about the model. Each maps onto the agent's own option.
FORWARDED = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "HLB_CONTEXT_WINDOW", "HLB_MAX_OUTPUT_TOKENS", "HLB_REASONING_EFFORT",
             "HARNESS_CONTEXT_TOKENS", "HARNESS_MAX_REPLY_TOKENS", "HARNESS_REASONING_EFFORT", "HARNESS_DEADLINE_SECONDS")


class Turnloom(BaseInstalledAgent):
    @staticmethod
    def name() -> str:
        return "turnloom"

    def get_version_command(self) -> str | None:
        return PATH + "turnloom --version"

    async def install(self, environment: BaseEnvironment) -> None:
        await self.ensure_system_dependencies(environment, ("curl", "bash", "git"))
        reference = self._version or "main"
        package = shlex.quote(f"turnloom @ git+{REPOSITORY}@{reference}")
        await self.exec_as_agent(environment, cwd="/tmp", env={"UV_NO_CONFIG": "1"}, command=(
            "set -euo pipefail; command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh; "
            + PATH + f"uv python install 3.12 && uv tool install --python 3.12 {package} && turnloom --version"))

    @with_prompt_template
    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        logs = self.environment_logs_dir
        env = {key: value for key in FORWARDED if (value := self._get_env(key))}
        model = (self.model_name or "").removeprefix("openai/")
        await self.exec_as_agent(environment, env=env, command=(
            PATH + f"turnloom-agent --model {shlex.quote(model)} --task {shlex.quote(instruction)} "
            f"--transcript {logs}/turnloom.jsonl --atif {logs}/trajectory.json "
            f"</dev/null 2>&1 | tee {logs}/turnloom.txt"))

    def populate_context_post_run(self, context: AgentContext) -> None:
        try:
            totals = json.loads((self.logs_dir / "trajectory.json").read_text(encoding="utf-8")).get("final_metrics") or {}
        except (OSError, ValueError) as error:
            self.logger.debug(f"No Turnloom trajectory to read: {error}")
            return
        context.n_input_tokens = totals.get("total_prompt_tokens") or 0
        context.n_output_tokens = totals.get("total_completion_tokens") or 0
        context.n_cache_tokens = totals.get("total_cached_tokens") or 0
        if totals.get("total_cost_usd") is not None:
            context.cost_usd = totals["total_cost_usd"]
