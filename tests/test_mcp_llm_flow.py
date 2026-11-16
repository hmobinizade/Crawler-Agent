import asyncio
import json
import sys
import types

mcp=types.ModuleType('mcp'); mcp.ClientSession=object; mcp.StdioServerParameters=object
mcp_client=types.ModuleType('mcp.client')
mcp_stdio=types.ModuleType('mcp.client.stdio'); mcp_stdio.stdio_client=lambda *a,**k: None
mcp_http=types.ModuleType('mcp.client.streamable_http'); mcp_http.streamable_http_client=lambda *a,**k: None
sys.modules.setdefault('mcp',mcp); sys.modules.setdefault('mcp.client',mcp_client)
sys.modules.setdefault('mcp.client.stdio',mcp_stdio); sys.modules.setdefault('mcp.client.streamable_http',mcp_http)

from app.extractor import ExtractorAgent

class Tool:
    def __init__(self, name):
        self.name=name; self.description=name; self.inputSchema={'type':'object','properties':{}}

class FakeBrowser:
    def __init__(self): self.calls=[]
    async def list_tools(self): return [Tool('browser_snapshot'), Tool('browser_evaluate'), Tool('browser_navigate')]
    async def call(self,name,args=None):
        self.calls.append((name,args or {}))
        fn=(args or {}).get('function','')
        if name=='browser_evaluate' and 'scrollBy' in fn:
            return {'height':12000,'url':'https://example.com/article/1','title':'Example'}
        if name=='browser_evaluate' and 'outerHTML' in fn:
            return '<html><body><main><h1 class="article-title">Example title</h1><time class="publish-date">2026-09-19</time></main></body></html>'
        if name=='browser_evaluate' and 'document.title' in fn:
            return {'title':'Example','url':'https://example.com/article/1'}
        if name=='browser_evaluate':
            return {'targeted':'evidence'}
        if name=='browser_snapshot':
            return {'snapshot':'main > article > h1, time'}
        if name=='browser_navigate':
            return {'url': args.get('url')}
        raise AssertionError(name)

class FakeLLM:
    def __init__(self): self.tool_calls=[]; self.chat_json_calls=[]
    async def chat_with_tools(self, system, user, tools, executor, max_rounds, max_tool_output_chars, max_repeated_tool_calls, max_tool_calls=None, round_delay_seconds=0.0, on_event=None):
        stage='analysis' if 'senior web-forensics' in system.lower() else 'repair'
        self.tool_calls.append((stage,max_rounds,max_tool_calls))
        # Exercise one tool call through the real executor path.
        await on_event('round_start', {'round':1,'max_rounds':max_rounds})
        result = await executor('browser_evaluate', {'function':'() => ({targeted:true})'})
        await on_event('tool_call', {'round':1,'name':'browser_evaluate','arguments':{}})
        await on_event('tool_result', {'round':1,'name':'browser_evaluate','result_preview':json.dumps(result)})
        await on_event('final_response', {'round':1})
        if stage=='recon':
            return {'page_type':'article','page_title':'Example','regions':[],'collection_regions':[],'metadata_sources':[],'special_behaviors':['none'],'anti_bot_state':'NONE','notes':[]}
        return {'page_title':'Example','anti_bot_state':'NONE','fields':[{'path':'title','found':True,'value':'Example title','selector':'h1.article-title','selector_type':'css','selector_candidates':['h1.article-title'],'confidence':0.99}], 'proposed_json':{'title':'Example'}, 'evidence':[], 'crawler_notes':[]}
    async def chat_json(self, system, user, max_tokens=None):
        self.chat_json_calls.append((system,user))
        return {'page_title':'Example','anti_bot_state':'NONE','proposed_json':{'title':'Example title'},'fields':[{'path':'title','found':True,'value':'Example title','selector':'h1.article-title','selector_type':'css','selector_candidates':['h1.article-title'],'confidence':0.99}]}

def test_multi_round_analysis_uses_bounded_browser_tools():
    agent=ExtractorAgent(); browser=FakeBrowser(); llm=FakeLLM(); agent.browser=browser; agent.llm=llm
    async def run():
        return await agent._inspect_with_mcp('https://example.com/article/1', {'title':''}, [{'name':'title','path':'title','value_type':'string','required':True,'description':'','item_schema':{}}])
    result=asyncio.run(run())
    assert result.proposed_json['title']=='Example title'
    assert [x[0] for x in browser.calls].count('browser_evaluate') >= 3
    assert llm.tool_calls[0][0] == 'analysis'
    assert llm.tool_calls[0][1] == 8
    assert llm.tool_calls[0][2] == 14
    assert len(llm.chat_json_calls) == 0
