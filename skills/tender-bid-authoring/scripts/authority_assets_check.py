# -*- coding: utf-8 -*-
"""
权威库本地资产自检 —— 每个项目动手制作前必跑一次。

用法（经 bid.ps1 调用，勿用系统 Python）：
  .\bid.ps1 "...\authority_assets_check.py"

校验内容：
  1. 四件套是否齐全（缺 authority.index.json.gz 等于丢失约 86% 正文）
  2. 各文件大小是否与云端基准一致
  3. meta.version 与 index.version 是否一致（版本漂移会导致召回错位）
  4. 报告 pairs 覆盖度，量化"只查 pairs 会丢多少"

退出码：0=齐全一致，1=缺件或版本不一致（应停止制作并先同步）。
"""
import os, json, gzip, sys, io, argparse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))

# 云端 current/ 基准大小（版本 authority-20260902-013339）
CLOUD_BASELINE = {
    'authority.meta.json': 734,
    'authority.cases.json.gz': 50749,
    'authority.pairs.json.gz': 3657498,
    'authority.index.json.gz': 25158869,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--authority-dir', default=os.environ.get('BID_AUTHORITY_DIR') or HERE,
                    help='包含 authority.meta/cases/pairs/index 的目录；也可设置 BID_AUTHORITY_DIR')
    args = ap.parse_args()
    authority_dir = os.path.abspath(args.authority_dir)
    print("=" * 74)
    print("权威库本地资产自检（动手制作前必跑）")
    print(f"资产目录: {authority_dir}")
    print("=" * 74)
    ok = True

    for name, expect in CLOUD_BASELINE.items():
        p = os.path.join(authority_dir, name)
        if not os.path.exists(p):
            print(f"  [缺失] {name}")
            ok = False
            continue
        size = os.path.getsize(p)
        if size != expect:
            print(f"  [差异] {name:28s} 本地 {size:>12,} B  基准 {expect:>12,} B")
            ok = False
        else:
            print(f"  [OK  ] {name:28s} {size:>12,} B")

    metap = os.path.join(authority_dir, 'authority.meta.json')
    idxp = os.path.join(authority_dir, 'authority.index.json.gz')
    pairsp = os.path.join(authority_dir, 'authority.pairs.json.gz')

    ver = None
    if os.path.exists(metap):
        ver = json.load(open(metap, encoding='utf-8')).get('version')
        print(f"\n  meta.version = {ver}")

    blocks = chars = 0
    if os.path.exists(idxp):
        try:
            idx = json.load(gzip.open(idxp, 'rt', encoding='utf-8'))
            iv = idx.get('version')
            print(f"  index.version = {iv}")
            if ver and iv and ver != iv:
                print("  [错误] 版本不一致 —— 需重新同步后再制作")
                ok = False
            else:
                print("  版本一致: 是")
            blocks = sum(len(f.get('blocks') or []) for f in idx.get('files', []))
            chars = sum(len(b.get('text') or '')
                        for f in idx.get('files', []) for b in (f.get('blocks') or []))
            print(f"  index 正文: {blocks:,} 块 / {chars:,} 字  (files={len(idx.get('files', []))})")
        except Exception as e:
            print(f"  [错误] index 解析失败: {e}")
            ok = False
    else:
        print("\n  [致命] 缺 authority.index.json.gz")
        print("         叙述类整节正文（服务计划/培训/保养/资质清单）只能由此召回。")

    if blocks and os.path.exists(pairsp):
        pairs = json.load(gzip.open(pairsp, 'rt', encoding='utf-8'))['pairs']
        pct = len(pairs) / blocks * 100
        print(f"\n  pairs 覆盖度: {len(pairs):,} 对 / {blocks:,} 块 = {pct:.1f}%")
        print(f"  → 只查 pairs 会丢掉约 {100-pct:.1f}% 的正文块")

    print()
    print("=" * 74)
    if ok:
        print("结论: 四件套齐全且版本一致 —— 可开始制作")
    else:
        print("结论: 存在缺失或版本不一致 —— 先同步权威库，再动手制作")
        print("      同步要点：index 需 sudo 复制到 /tmp 后再 scp（该目录普通用户无权读）")
    print("=" * 74)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
