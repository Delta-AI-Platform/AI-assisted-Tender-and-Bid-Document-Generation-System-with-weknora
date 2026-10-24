"""Advisory signals only: never grant authority, stop a run, or claim completion."""
import json

def recovery_advice(calls):
    if not calls:return None
    recent=calls[-3:]
    empty_search_tools={'search_text','search_history'}
    if len(recent)==3 and all(c['tool'] in empty_search_tools and c['result'].get('ok') and c['result'].get('data',{}).get('results')==[] for c in recent):
        return {'code':'repeated_empty_search','detail':'最近三次检索均无命中。请检查是否已有明确路径、输入清单或可执行的工具参数；调整查证方式，避免仅换关键词重复检索。没有新证据时不得把假设写成事实。'}
    if len(calls)>=2:
        a,b=calls[-2:]
        if a['tool']==b['tool'] and a['args']==b['args'] and not a['result'].get('ok') and not b['result'].get('ok') and a['result'].get('error')==b['result'].get('error'):
            return {'code':'identical_failed_retry','detail':'同一工具与参数已连续两次失败，错误相同。根据错误检查输入或选择其他可验证方法；不要原样重试，也不要跳过校验后声称成功。'}
    return None
