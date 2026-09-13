import pytest
from langchain_core.messages import AIMessage, ToolMessage

from agent_engine.engine.langgraph.engine import LangGraphEngine
from tests.approvals.test_engine_hitl import _spec as approval_spec


class SingleToolCallModel:
    def __init__(self):
        self.calls = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        if any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(content='done')
        self.calls += 1
        return AIMessage(content='', tool_calls=[{
            'name': 'record_value', 'args': {'message': 'reviewed-value'},
            'id': f'call-{self.calls}', 'type': 'tool_call',
        }])


@pytest.mark.asyncio
async def test_async_local_tool_executes(tmp_path):
    output = tmp_path / 'async-executed.txt'
    tool = tmp_path / 'plugins/tools/record_value.py'
    tool.parent.mkdir(parents=True)
    tool.write_text('from pathlib import Path\n'
                    'async def record_value(message: str) -> str:\n'
                    f'    Path({str(output)!r}).write_text(message)\n'
                    '    return message\n')
    model = SingleToolCallModel()
    async with LangGraphEngine(tmp_path, model_factory=lambda *a, **kw: model) as engine:
        await engine.build(approval_spec('record_value', auto_mode=True))
        result = await engine.run('record a value')
        print(f'async tool executed={output.exists()}, used_tools={result.used_tools}')
        assert output.exists()
