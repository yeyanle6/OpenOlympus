"""Agent pool for managing concurrent agent execution."""

from __future__ import annotations

import asyncio

from olympus.agent.llm_agent import LLMAgent
from olympus.agent.definition import AgentDefinition
from olympus.agent.loader import AgentLoader


class AgentPool:
    """Manages agent instances and concurrent execution limits."""

    def __init__(self, loader: AgentLoader, max_global_concurrency: int = 10):
        self.loader = loader
        self._semaphore = asyncio.Semaphore(max_global_concurrency)
        self._agent_semaphores: dict[str, asyncio.Semaphore] = {}

    def get_agent(self, agent_id: str) -> LLMAgent | None:
        defn = self.loader.get(agent_id)
        if defn is None:
            return None
        return LLMAgent(defn)

    def get_agents(self, agent_ids: list[str]) -> list[LLMAgent]:
        agents = []
        for aid in agent_ids:
            agent = self.get_agent(aid)
            if agent:
                agents.append(agent)
        return agents

    def _get_agent_semaphore(self, defn: AgentDefinition) -> asyncio.Semaphore:
        if defn.agent_id not in self._agent_semaphores:
            self._agent_semaphores[defn.agent_id] = asyncio.Semaphore(
                defn.max_concurrent
            )
        return self._agent_semaphores[defn.agent_id]

    async def execute_with_limit(
        self,
        agent: LLMAgent,
        task: str,
        context=None,
    ):
        agent_sem = self._get_agent_semaphore(agent.definition)
        async with self._semaphore:
            async with agent_sem:
                return await agent.execute(task, context)
