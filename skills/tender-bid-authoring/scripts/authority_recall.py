# -*- coding: utf-8 -*-
"""
权威库复用召回（L0 整卷优先 + L1/L2/L3 参数兜底）。

用法（经 bid.ps1 调用，勿用系统 Python）：
  # L0 整卷层：按招标编号命中，输出整按节聚合的素材基线（叙述类正文首选）
  .\bid.ps1 "...\authority_recall.py" --tender-no 0632-2644H04E1076
  .\bid.ps1 "...\authority_recall.py" --customer 山西柳林凌志柳家庄煤业 --outline
  .\bid.ps1 "...\authority_recall.py" --tender-no 0632-2644H04E1076 --section 技术服务 --show-text

  # L1/L2/L3 参数层（原有能力）
  .\bid.ps1 "...\authority_recall.py" --model WLR-12 --param 离地间隙
  .\bid.ps1 "...\authority_recall.py" --requirement "最小离地间隙：≥170mm" --model WLR-12

  # 模拟新项目：屏蔽本项目历史投标件，验证「参数兜底」路径是否真的走得通
  .\bid.ps1 "...\authority_recall.py" --model WLR-19 --exclude-case hac-xxxxxxxx --param 离地间隙
  .\bid.ps1 "...\authority_recall.py" --model WLR-19 --exclude-customer 国家能源集团 --requirement "..."

排除参数（--exclude-case / --exclude-customer）：
  用于「新项目模拟」与回归测试——该项目在库中虽有历史投标件，但按新项目对待，
  只能靠同车型(L2)/同参数语义(L3)兜底。排除后若召回量骤降，即暴露真实复用能力。
  台账会记录 excluded 条件，保证测试可核验（不是"没排除却说排除了"）。

层级说明：
  L0 整卷     按 project_number / customer_entity_name 命中，返回 blocks 完整正文
              ——叙述类内容（服务计划/培训/保养/资质清单/方案论述）只存在于此层
  L1 同项目同标段  L0 的精确子集
  L2 同车型跨项目  参数主力
  L3 同参数语义跨车型 兜底，仅句式参考

复用边界：
  参数类  → 走 pairs/blocks 的参数应答；数值必须替换，偏离必须按本次阈值重判
  叙述类  → 走 L0 blocks；整节结构可复用，但须核对时效与本项目配置/型号/供货范围

台账：--ledger <path> 输出可核验的复用台账 JSON（source_id/case_id/层/节/字数）
"""
import sys, io, os, json, gzip, re, argparse, difflib
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
PARAMS = json.load(open(os.path.join(HERE, 'param_semantics.json'), encoding='utf-8'))['params']

AUTHORITY_DIR = os.environ.get('BID_AUTHORITY_DIR') or HERE
IDXpath = os.path.join(AUTHORITY_DIR, 'authority.index.json.gz')
PAIRSp = os.path.join(AUTHORITY_DIR, 'authority.pairs.json.gz')
CASESp = os.path.join(AUTHORITY_DIR, 'authority.cases.json.gz')


# ---------------------------------------------------------------- 加载
def load_index():
    """返回 (files, cases)；index 是唯一含 blocks 正文的载体，缺它等于丢失 83% 正文"""
    if not os.path.exists(IDXpath):
        return None, None
    d = json.load(gzip.open(IDXpath, 'rt', encoding='utf-8'))
    return d.get('files', []), d.get('cases', [])


def load_pairs():
    pairs = json.load(gzip.open(PAIRSp, 'rt', encoding='utf-8'))['pairs']
    cases = json.load(gzip.open(CASESp, 'rt', encoding='utf-8'))['cases']
    return pairs, cases


def norm(s):
    s = re.sub(r'\s+', '', s or '')
    return re.sub(r'[（）()【】\[\]""\'\'、，。：:；;]', '', s)


def block_text(b):
    """块的正文。response_pair 没有 text 字段，内容在 requirement/response 里。
    全库 25,661/187,628 块属于此类（占 response 字数的 22.7%），
    曾因只读 text 被当成空块，导致关键词漏召回、去重指纹全同而被集体丢弃。"""
    if b.get('kind') == 'response_pair':
        req = b.get('requirement') or ''
        resp = b.get('response') or ''
        return req + '\n' + resp if req != resp else req
    return b.get('text') or ''


# ---------------------------------------------------------------- L0 整卷层
def match_l0_files(files, tender_no=None, customer=None, model=None,
                   exclude_cases=None, exclude_customer=None):
    """按招标编号 / 客户实体命中源文件。招标编号是精确键，优先级最高。"""
    if not files:
        return []
    out = []
    excl = set(exclude_cases or [])
    for f in files:
        # 排除：模拟新项目时屏蔽本项目/本客户的历史投标件
        fk = f.get('case_key') or f.get('case_id') or ''
        if fk in excl:
            continue
        if exclude_customer and exclude_customer in json.dumps(
                {k: f.get(k) for k in ('project_name', 'customer_entity_name',
                                       'customer_group_name', 'absolute_source_path')},
                ensure_ascii=False):
            continue
        ok = False
        why = []
        if tender_no and (f.get('project_number') or '') == tender_no:
            ok, why = True, ['招标编号精确命中']
        elif customer and customer in json.dumps(
                {k: f.get(k) for k in ('project_name', 'source_path', 'absolute_source_path')},
                ensure_ascii=False):
            ok, why = True, ['客户实体命中']
        elif model and model.upper() in [str(m).upper() for m in (f.get('models') or [])]:
            ok, why = True, ['车型命中']
        if ok:
            out.append((f, why))
    return out


def show_l0(matched, args):
    print("=" * 78)
    print("L0 整卷层 — 素材基线（叙述类正文的唯一来源）")
    print("=" * 78)
    if not matched:
        print("未命中。请确认：招标编号写法（含横杠/前缀）、客户实体全称，或改用 --customer/--model。")
        print("注意：未命中不等于库中没有，也可能是 project_number 字段为空。")
        return []
    import hashlib
    total_blocks = total_chars = 0
    dup_blocks = dup_chars = 0
    seen = {}          # 正文指纹 -> 首次出现的文件名（用于识别同标段重复正文）
    ledger = []
    for f, why in matched:
        blocks = [b for b in f.get('blocks', []) if not b.get('excluded_from_reuse')]
        # 节名过滤
        if args.section:
            blocks = [b for b in blocks if args.section in (b.get('section_title') or '')]
        # 关键词过滤
        if args.keyword:
            blocks = [b for b in blocks if args.keyword in block_text(b)]
        # 跨文件去重：同一延标的不同标段常共用模板正文，不去重会高估素材量
        uniq = []
        n_dup_here = 0
        for b in blocks:
            fp = hashlib.sha1(norm(block_text(b)).encode('utf-8')).hexdigest()
            if fp in seen:
                n_dup_here += 1
                dup_chars += len(block_text(b))
                continue
            seen[fp] = f.get('filename')
            uniq.append(b)
        dup_blocks += n_dup_here
        blocks_uniq = uniq
        chars = sum(len(block_text(b)) for b in blocks_uniq)
        total_blocks += len(blocks_uniq)
        total_chars += chars
        print()
        print(f"■ {f.get('domain')} | {f.get('filename')}")
        print(f"  命中依据: {','.join(why)} | project_number={f.get('project_number')} | year={f.get('year')}")
        print(f"  车型: {', '.join(f.get('models') or [])[:90]}")
        print(f"  去重后可用块 {len(blocks_uniq)} / 全文件 {f.get('block_count')}   字符数 {chars}"
              + (f"   (与他文件重复 {n_dup_here} 块已剔除)" if n_dup_here else ""))
        print(f"  absolute_source_path: {f.get('absolute_source_path')}")
        # 按节聚合（基于去重后的块）
        blocks = blocks_uniq
        # 按节聚合
        sec = defaultdict(lambda: {'n': 0, 'c': 0})
        for b in blocks:
            k = b.get('section_title') or '(无节标题)'
            sec[k]['n'] += 1
            sec[k]['c'] += len(block_text(b))
        top = sorted(sec.items(), key=lambda kv: -kv[1]['c'])[:args.top_sections]
        if top:
            print(f"  --- 节粒度 TOP{len(top)}（叙述类按整节复用）---")
            for k, v in top:
                print(f"    {v['c']:7d}字 {v['n']:4d}块  {k[:56]}")
        # 台账条目
        for k, v in sec.items():
            ledger.append({
                'source_id': f.get('source_id'), 'case_id': f.get('case_key'),
                'layer': 'L0', 'file': f.get('filename'),
                'project_number': f.get('project_number'),
                'section_title': k, 'blocks': v['n'], 'chars': v['c'],
            })
    print()
    print(f"合计：{len(matched)} 份源文件 / 去重后 {total_blocks} 块 / {total_chars:,} 字"
          + (f"（另剔除跨文件重复 {dup_blocks} 块 / {dup_chars:,} 字）" if dup_blocks else ""))
    if dup_blocks:
        print("注意：同招标编号下不同标段常共用模板正文，已按正文指纹去重，素材量以去重后为准。")
    print("提示：叙述类素材（服务计划·培训·保养·资质清单）核对时效与本项目配置后按整节重组，不整卷照抄。")
    print("      节名来自源文档自身标题，与本次招标格式章节名未必一致——用 --keyword 或 --show-text 定位更稳。")
    return ledger


def show_blocks_text(matched, args):
    """输出命中块的正文（用于实际取素材）"""
    print()
    print("=" * 78)
    print(f"正文输出（节包含「{args.section or ''}」关键词「{args.keyword or ''}」，限 {args.limit} 块）")
    print("=" * 78)
    n = 0
    for f, _ in matched:
        for b in f.get('blocks', []):
            if b.get('excluded_from_reuse'):
                continue
            if args.section and args.section not in (b.get('section_title') or ''):
                continue
            if args.keyword and args.keyword not in block_text(b):
                continue
            t = block_text(b).strip()
            if not t:
                continue
            n += 1
            if n > args.limit:
                break
            print(f"\n[{n}] {f.get('filename')[:34]} | 节: {b.get('section_title') or '(无)'} | kind={b.get('kind')}")
            print(f"    block_id={b.get('block_id')} order={b.get('order')}")
            print(f"    {t[:400]}")
        if n > args.limit:
            break
    if n == 0:
        print("无匹配正文。试试放宽 --section/--keyword，或加 --show-text 前先看 --outline。")


# ---------------------------------------------------------------- 参数层
def detect_param(text):
    t = text or ''
    best, hits = None, 0
    for name, cfg in PARAMS.items():
        n = sum(1 for k in cfg['keywords'] if k in t)
        if n > hits:
            best, hits = name, n
    return best


def show_param_layer(args):
    pairs, cases = load_pairs()
    cmap = {c.get('case_id'): c for c in cases}
    excl = set(args.exclude_case or [])

    def is_excluded(p):
        cid = p.get('case_id') or p.get('case_key') or ''
        if cid in excl:
            return True
        if args.exclude_customer:
            c = cmap.get(cid) or {}
            if args.exclude_customer in json.dumps(
                    {k: c.get(k) for k in ('project_name', 'customer_entity_name',
                                           'customer_group_name')}, ensure_ascii=False):
                return True
        return False

    n_before = len(pairs)
    pairs = [p for p in pairs if not is_excluded(p)]
    model = (args.model or '').upper()
    req = args.requirement or ''
    pname = args.param or detect_param(req)
    print(f"\n车型: {model or '(未指定)'}   参数语义: {pname or '(未识别)'}")
    if excl or args.exclude_customer:
        print(f"【新项目模拟】已排除 {n_before - len(pairs):,} 条（"
              f"case={','.join(excl) or '-'} / 客户={args.exclude_customer or '-'}）"
              f"  剩余候选 {len(pairs):,}")

    by_model = [p for p in pairs if model and model in json.dumps(p, ensure_ascii=False).upper()]
    l1 = [p for p in pairs if args.project and args.project in json.dumps(p, ensure_ascii=False)] if args.project else []

    cand = by_model if by_model else pairs
    if pname:
        kws = PARAMS[pname]['keywords']
        cand = [p for p in cand if any(k in (p['block'].get('requirement') or '') for k in kws)] or cand

    rn = norm(req)
    scored = []
    for p in cand:
        b = p['block']
        r = b.get('requirement') or ''
        s = 1.0 if (rn and (norm(r) in rn or rn in norm(r))) else difflib.SequenceMatcher(None, rn, norm(r)).ratio() if rn else 0.0
        scored.append((s, b, p))
    scored.sort(key=lambda x: -x[0])

    print("=" * 78)
    print(f"参数层召回（候选 {len(scored)}，展示前 {min(args.limit, len(scored))}）")
    print("=" * 78)
    n = 0
    ledger = []
    for s, b, p in scored[:args.limit]:
        resp = (b.get('response') or '').strip()
        if not resp:
            continue
        n += 1
        src = b.get('source_ref', {}).get('source_path', '')
        level = 'L1 同项目' if p in l1 else ('L2 同车型跨项目' if model and model in json.dumps(p, ensure_ascii=False).upper() else 'L3 跨车型')
        print(f"\n[{n}] {level} | 相似度 {s:.2f} | 历史偏离: {b.get('deviation')}")
        print(f"  来源: {src[:88]}")
        print(f"  历史要求: {(b.get('requirement') or '').strip()[:120]}")
        print(f"  历史响应: {resp[:260]}")
        print("  复用边界: 句式/优势说明=可复用 | 证明引用=核对后复用 | 数值=必须替换 | 偏离=按本次阈值重判")
        ledger.append({'layer': level, 'source': src, 'similarity': round(s, 3), 'param': pname})
    if n == 0:
        print("\n未召回。确认车型后缀写法，或改用 --param 参数语义名重试。")
    return ledger


def force_reminder():
    print()
    print("=" * 78)
    print("强制提醒（每次复用都要执行）")
    print("  1. 数值来自本项目车型检验报告实测值，不得照抄他项目数值")
    print("  2. 偏离按本次招标阈值重判，历史正偏离不等于本次正偏离")
    print("  3. 证明引用须确认本项目确实附入该报告，索引有名称不等于材料已提供")
    print("  4. 叙述类素材须核对时效与本项目配置/型号/供货范围，不沿用他项目承诺与年份")
    print("  5. 半径/直径、kW/Ah、kg/人 等口径不可混算")


# ---------------------------------------------------------------- main
def main():
    global AUTHORITY_DIR, IDXpath, PAIRSp, CASESp
    ap = argparse.ArgumentParser()
    ap.add_argument('--authority-dir', default=AUTHORITY_DIR,
                    help='包含 authority.cases/pairs/index 的目录；也可设置 BID_AUTHORITY_DIR')
    ap.add_argument('--tender-no', help='招标编号，如 0632-2644H04E1076（L0 精确键）')
    ap.add_argument('--customer', help='客户实体名，如 山西柳林凌志柳家庄煤业有限公司')
    ap.add_argument('--model', help='投标车型，如 WLR-12')
    ap.add_argument('--param', help='参数语义名，如 离地间隙')
    ap.add_argument('--requirement', help='本次招标要求原文')
    ap.add_argument('--project', help='本次项目名称关键词（L1 判定）')
    ap.add_argument('--section', help='按节标题过滤，如 技术服务')
    ap.add_argument('--keyword', help='按正文关键词过滤，如 保养')
    ap.add_argument('--outline', action='store_true', help='仅输出整卷目录/节聚合，不输出正文')
    ap.add_argument('--show-text', action='store_true', help='输出命中块正文')
    ap.add_argument('--top-sections', type=int, default=12)
    ap.add_argument('--limit', type=int, default=10)
    ap.add_argument('--ledger', help='复用台账输出路径(JSON)')
    ap.add_argument('--exclude-case', action='append', default=[],
                    help='排除的 case_id，可多次传（模拟新项目：屏蔽本项目历史投标件）')
    ap.add_argument('--exclude-customer',
                    help='排除的客户/集团名，如 国家能源集团（模拟全新客户）')
    a = ap.parse_args()

    AUTHORITY_DIR = os.path.abspath(a.authority_dir)
    IDXpath = os.path.join(AUTHORITY_DIR, 'authority.index.json.gz')
    PAIRSp = os.path.join(AUTHORITY_DIR, 'authority.pairs.json.gz')
    CASESp = os.path.join(AUTHORITY_DIR, 'authority.cases.json.gz')

    files, _cases = load_index()
    if files is None:
        print("!" * 78)
        print("缺失 authority.index.json.gz —— 只能检索 pairs 参数层（约覆盖 16.8% 内容）。")
        print("叙述类整节正文（服务计划/培训/保养/资质清单）将无法召回。")
        print(f"请先同步权威库四件套到指定目录：{AUTHORITY_DIR}")
        print("!" * 78)

    ledger = []

    use_l0 = bool(a.tender_no or a.customer or (a.model and not a.requirement and not a.param))
    if use_l0:
        matched = match_l0_files(files, a.tender_no, a.customer, a.model,
                                 a.exclude_case, a.exclude_customer)
        lg = show_l0(matched, a)
        ledger += lg
        if a.show_text:
            show_blocks_text(matched, a)

    if a.requirement or a.param or a.project:
        ledger += show_param_layer(a)

    if not use_l0 and not (a.requirement or a.param or a.project):
        print("未指定检索条件。示例：")
        print("  --tender-no 0632-2644H04E1076            # L0 整卷（首选）")
        print("  --customer 山西柳林凌志柳家庄煤业有限公司    # L0 按客户")
        print("  --requirement '最小离地间隙：≥170mm' --model WLR-12   # 参数层")

    force_reminder()

    if a.ledger:
        os.makedirs(os.path.dirname(os.path.abspath(a.ledger)) or '.', exist_ok=True)
        json.dump({
            'query': {
                'tender_no': a.tender_no, 'customer': a.customer, 'model': a.model,
                'section': a.section, 'keyword': a.keyword,
                'excluded_cases': a.exclude_case, 'excluded_customer': a.exclude_customer,
            },
            'index_available': files is not None,
            'entries': ledger,
            'total entries': len(ledger),
        }, open(a.ledger, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        print(f"\n复用台账已写出: {a.ledger}  ({len(ledger)} 条)")


if __name__ == '__main__':
    main()
