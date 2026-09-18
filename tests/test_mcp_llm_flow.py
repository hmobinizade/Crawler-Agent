import sys
import types

# Minimal stubs so extractor can be imported in a test environment without MCP installed.
mcp=types.ModuleType('mcp'); mcp.ClientSession=object; mcp.StdioServerParameters=object
mcp_client=types.ModuleType('mcp.client')
mcp_stdio=types.ModuleType('mcp.client.stdio'); mcp_stdio.stdio_client=lambda *a,**k: None
mcp_http=types.ModuleType('mcp.client.streamable_http'); mcp_http.streamable_http_client=lambda *a,**k: None
sys.modules.setdefault('mcp',mcp)
sys.modules.setdefault('mcp.client',mcp_client)
sys.modules.setdefault('mcp.client.stdio',mcp_stdio)
sys.modules.setdefault('mcp.client.streamable_http',mcp_http)

import asyncio
from app.extractor import ExtractorAgent


class FakeBrowser:
    def __init__(self): self.calls=[]
    async def call(self,name,args=None):
        self.calls.append((name,args or {}))
        if name == 'browser_evaluate' and 'function' in (args or {}):
            fn=args['function']
            if 'scrollBy' in fn:
                return {'height':10000,'url':'https://example.com/article/1','title':'Example'}
            if 'JSON.stringify' in fn:
                return '{"title":{"value":"Example title","selector":"h1.article-title","selector_unique":true}}'
        raise AssertionError(f'unexpected call {name}')


class FakeLLM:
    def __init__(self): self.called=False
    async def chat_json(self, system, user):
        self.called=True
        assert 'COMPACT BROWSER EVIDENCE' in user
        return {
            'page_title':'Example',
            'anti_bot_state':'NONE',
            'proposed_json':{'title':'Example title'},
            'fields':[{
                'path':'title','value_type':'string','selector':'h1.article-title','selector_type':'css',
                'found':True,'value':'Example title','evidence':'headline','confidence':0.99,
            }]
        }


def test_browser_flow_does_not_use_llm_tool_loop(monkeypatch):
    agent=ExtractorAgent()
    browser=FakeBrowser(); llm=FakeLLM()
    agent.browser=browser; agent.llm=llm
    async def run():
        result=await agent._inspect_with_mcp(
            'https://example.com/article/1',
            {'title':''},
            [{'name':'title','path':'title','value_type':'string','required':True,'description':'','item_schema':{}}],
        )
        return result
    result=asyncio.run(run())
    assert llm.called
    assert result.proposed_json['title']=='Example title'
    assert [name for name,_ in browser.calls] == ['browser_evaluate','browser_evaluate','browser_evaluate','browser_evaluate']
    assert all('chat_with_tools' not in str(type(x)) for x in [agent.llm])
